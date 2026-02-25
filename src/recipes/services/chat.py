"""Natural language recipe recommendation chat service.

Flow per message:
1. handle_chat_message loads/saves session history
2. LLM Call 1: with query_recipes tool definition
3. Tool executed against DB → annotated recipe list
4. LLM Call 2: with tool result → structured JSON response
5. Falls back to single-shot (inject all recipes) if model skips the tool
"""

import json
import logging
from dataclasses import dataclass, field

from django.conf import settings

from inventory.services import get_ingredient_match_sets
from recipes.services.image_parser import VALID_TASTE_TAG_SLUGS

logger = logging.getLogger(__name__)

MAX_TURNS = 10           # session history cap (pairs of user+assistant)
MAX_TOOL_RESULTS = 50    # cap on recipe candidates passed to LLM
SESSION_KEY = "recipe_chat_history"

DEPTH_DESCRIPTIONS = {
    1: "Same-category (depth=1): any London Dry Gin can substitute for London Dry Gin",
    2: "Parent-category (depth=2): any Gin can substitute for London Dry Gin",
}

DEPTH_NOTES = {
    1: "Only suggest substitutions within the same specific category.",
    2: "You may suggest broader category substitutions (e.g. any Gin for a specific Gin style).",
}

SYSTEM_PROMPT_TEMPLATE = """\
You are a cocktail recommendation assistant. The user can make the recipes returned
by the query_recipes tool with their current inventory.

Substitution mode: {depth_description}
{depth_note}

Instructions:
1. ALWAYS call query_recipes to get candidates before making any recommendation.
2. Use tags for flavor/style (smoky, strong, refreshing…).
   Use categories for spirit/ingredient types — e.g. if someone asks for a "whisky
   drink" pass categories=["Whisky"]; "gin cocktail" → categories=["Gin"].
   If someone describes a style or vibe (e.g. "something like an Old Fashioned"),
   translate that into tags and/or categories rather than searching by name.
3. Select up to 8 best matches. For each, write a 1-2 sentence blurb explaining
   why it fits and noting any category-substituted ingredients.
4. Return ONLY valid JSON: {{"message": "...", "recommendations": [{{"name": "...", "slug": "...", "blurb": "..."}}]}}
   If the tool returns 0 results, set recommendations to [] and explain in message.
Do not recommend recipes not returned by query_recipes.\
"""

TOOL_DESCRIPTION = {
    "name": "query_recipes",
    "description": (
        "Query cocktail recipes the user can make with their current inventory. "
        "Returns recipes matching the given filters."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Filter by flavor/style. Valid slugs: "
                    "citrusy, smoky, strong, low_abv, bitter, refreshing, sweet. "
                    "Leave empty to return all makeable recipes."
                ),
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Filter by main spirit or ingredient type. Pass the common name of the "
                    "spirit/ingredient family — e.g. 'Whisky', 'Gin', 'Rum', 'Tequila', "
                    "'Vodka', 'Brandy', 'Vermouth', 'Amaro', 'Champagne'. "
                    "Use when the user mentions a spirit type or ingredient family. "
                    "To find 'something like an Old Fashioned', use categories=['Whisky'] "
                    "and tags=['strong'] rather than searching by cocktail name. "
                    "Leave empty to skip category filtering."
                ),
            },
        },
        "required": [],
    },
}

CHAT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                    "blurb": {"type": "string"},
                },
                "required": ["name", "slug", "blurb"],
            },
        },
    },
    "required": ["message", "recommendations"],
}


@dataclass
class ChatResponse:
    message: str
    recommendations: list[dict] = field(default_factory=list)
    error: str | None = None


def _fmt_tool_args(args: dict) -> str:
    """Format query_recipes args for a compact log line."""
    parts = []
    if args.get("tags"):
        parts.append(f"tags={args['tags']}")
    if args.get("categories"):
        parts.append(f"categories={args['categories']}")
    return f"({', '.join(parts)})" if parts else "(no filters)"


def _fmt_recipe_list(recipes: list[dict], limit: int = 6) -> str:
    """Format a list of recipe dicts as a compact name summary."""
    names = [r["name"] for r in recipes[:limit]]
    suffix = f" + {len(recipes) - limit} more" if len(recipes) > limit else ""
    return ", ".join(names) + suffix


def handle_chat_message(user, session, user_message: str, depth: int) -> ChatResponse:
    """Handle a single chat message turn, maintaining session history."""
    history = list(session.get(SESSION_KEY, []))

    # Build the full message list for the LLM (includes current user message)
    messages_for_llm = history + [{"role": "user", "content": user_message}]

    # Compute ingredient match sets once per request
    user_ing_ids, category_ing_ids = get_ingredient_match_sets(user, depth)

    provider = getattr(settings, "LLM_PROVIDER", "ollama").lower()

    logger.info(
        "user=%s | provider=%s | depth=%d | history=%d turns | %r",
        user.username, provider, depth, len(history) // 2, user_message[:120],
    )

    try:
        if provider == "gemini":
            response = _chat_with_gemini(
                messages_for_llm, user, depth, user_ing_ids, category_ing_ids
            )
        else:
            response = _chat_with_ollama(
                messages_for_llm, user, depth, user_ing_ids, category_ing_ids
            )
    except Exception as e:
        logger.exception("LLM call failed: %s", e)
        response = ChatResponse(
            message="",
            error="Something went wrong generating recommendations. Please try again.",
        )

    # Only persist history on success — errors leave the session unchanged
    if not response.error:
        new_history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response.message},
        ]
        # Trim to MAX_TURNS pairs after adding both messages
        if len(new_history) > MAX_TURNS * 2:
            new_history = new_history[-(MAX_TURNS * 2):]
        session[SESSION_KEY] = new_history
        session.modified = True

    return response


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_system_prompt(depth: int) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        depth_description=DEPTH_DESCRIPTIONS.get(depth, DEPTH_DESCRIPTIONS[1]),
        depth_note=DEPTH_NOTES.get(depth, DEPTH_NOTES[1]),
    )


def _execute_query_recipes(
    user, depth: int, args: dict, user_ing_ids: set, category_ing_ids: set
) -> list[dict]:
    """Execute the query_recipes tool: filter makeable recipes and annotate ingredients."""
    from django.db.models import Q

    from ingredients.models import IngredientCategory, IngredientCategoryAncestor
    from inventory.services import get_makeable_recipes

    raw_tags = args.get("tags", [])
    tags = [t for t in (raw_tags if isinstance(raw_tags, list) else []) if t in VALID_TASTE_TAG_SLUGS]

    raw_categories = args.get("categories", [])
    categories = [c for c in (raw_categories if isinstance(raw_categories, list) else []) if c]

    recipes = get_makeable_recipes(user, max_depth=depth).prefetch_related(
        "recipe_ingredients__ingredient",
        "taste_tags",
    )

    if tags:
        recipes = recipes.filter(taste_tags__slug__in=tags).distinct()

    if categories:
        # Resolve each category name to its full descendant subtree, then OR-combine
        cat_q = Q()
        resolved = []
        for cat_name in categories:
            matched = IngredientCategory.objects.filter(name__icontains=cat_name).first()
            if matched:
                resolved.append(matched.name)
                descendant_ids = IngredientCategoryAncestor.objects.filter(
                    ancestor=matched
                ).values_list("category_id", flat=True)
                cat_q |= Q(recipe_ingredients__ingredient__categories__in=list(descendant_ids))
        if cat_q:
            logger.debug("category filter resolved %s → %s", categories, resolved)
            recipes = recipes.filter(cat_q).distinct()

    results = []
    for recipe in recipes[:MAX_TOOL_RESULTS]:
        ingredients = []
        for ri in recipe.recipe_ingredients.all():
            ing_id = ri.ingredient_id
            if ing_id in user_ing_ids:
                match = "exact"
            elif ing_id in category_ing_ids:
                match = "substituted"
            else:
                match = "missing"
            ingredients.append({"name": ri.ingredient.name, "match": match})

        results.append({
            "name": recipe.name,
            "slug": recipe.slug,
            "tags": [tag.slug for tag in recipe.taste_tags.all()],
            "glassware": recipe.glassware,
            "ingredients": ingredients,
        })

    logger.info(
        "tool result → %d recipes %s: %s",
        len(results),
        _fmt_tool_args({"tags": tags, "categories": categories}),
        _fmt_recipe_list(results),
    )
    return results


def _execute_query_recipes_relaxed(
    user, depth: int, args: dict, user_ing_ids: set, category_ing_ids: set
) -> list[dict]:
    """
    Execute query_recipes with progressive filter relaxation on empty results.

    Tries in order:
      1. Original args (tags + categories)
      2. Drop categories, keep tags
      3. No filters (all makeable recipes)
    """
    result = _execute_query_recipes(user, depth, args, user_ing_ids, category_ing_ids)
    if result:
        return result

    if args.get("categories"):
        relaxed = {**args, "categories": []}
        logger.info("0 results — retrying without categories: query_recipes%s", _fmt_tool_args(relaxed))
        result = _execute_query_recipes(user, depth, relaxed, user_ing_ids, category_ing_ids)
        if result:
            return result

    if args.get("tags") or args.get("categories"):
        logger.info("0 results — returning all makeable recipes (no filters)")
        result = _execute_query_recipes(user, depth, {}, user_ing_ids, category_ing_ids)

    return result


def _to_gemini_messages(messages: list[dict]) -> list[dict]:
    """Convert standard {role, content} messages to Gemini format."""
    result = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else msg["role"]
        result.append({"role": role, "parts": [{"text": msg["content"]}]})
    return result


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

def _build_gemini_tool(genai):
    """Build a Gemini Tool for query_recipes using protos.Schema."""
    props = TOOL_DESCRIPTION["parameters"]["properties"]
    return genai.types.Tool(function_declarations=[
        genai.types.FunctionDeclaration(
            name=TOOL_DESCRIPTION["name"],
            description=TOOL_DESCRIPTION["description"],
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "tags": genai.protos.Schema(
                        type=genai.protos.Type.ARRAY,
                        items=genai.protos.Schema(type=genai.protos.Type.STRING),
                        description=props["tags"]["description"],
                    ),
                    "categories": genai.protos.Schema(
                        type=genai.protos.Type.ARRAY,
                        items=genai.protos.Schema(type=genai.protos.Type.STRING),
                        description=props["categories"]["description"],
                    ),
                },
            ),
        )
    ])


def _chat_with_gemini(messages, user, depth, user_ing_ids, category_ing_ids) -> ChatResponse:
    """Two-call chat flow using Gemini with function calling."""
    import google.generativeai as genai

    api_key = getattr(settings, "GEMINI_API_KEY", None)
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    genai.configure(api_key=api_key)
    model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
    system_prompt = _build_system_prompt(depth)
    model = genai.GenerativeModel(model_name, system_instruction=system_prompt)

    # Build tool definition (fall back to single-shot if schema build fails)
    try:
        tool = _build_gemini_tool(genai)
    except Exception as e:
        logger.warning("Failed to build Gemini tool schema: %s — using single-shot fallback", e)
        return _single_shot_recommendation(
            messages, user, depth, user_ing_ids, category_ing_ids, provider="gemini"
        )

    gemini_messages = _to_gemini_messages(messages)

    # --- Call 1: with tool definition ---
    logger.info("[Gemini/%s] Call 1 →", model_name)
    response1 = model.generate_content(
        gemini_messages,
        tools=[tool],
        generation_config={"temperature": 0.7},
    )

    # Extract function call
    function_call_args = None
    model_content = None
    try:
        model_content = response1.candidates[0].content
        for part in model_content.parts:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "name", None) == "query_recipes":
                function_call_args = dict(fc.args)
                break
    except (IndexError, AttributeError, Exception) as e:
        logger.debug("Could not extract function call: %s", e)

    if function_call_args is None:
        logger.warning("[Gemini/%s] no tool call in response — single-shot fallback", model_name)
        return _single_shot_recommendation(
            messages, user, depth, user_ing_ids, category_ing_ids, provider="gemini"
        )

    logger.info("[Gemini/%s] tool call → query_recipes%s", model_name, _fmt_tool_args(function_call_args))
    tool_result = _execute_query_recipes_relaxed(user, depth, function_call_args, user_ing_ids, category_ing_ids)

    # --- Call 2: with tool result, requesting JSON ---
    logger.info("[Gemini/%s] Call 2 → (%d recipes)", model_name, len(tool_result))
    # Try proper proto-based function_response first, fall back to text injection
    try:
        fn_response_content = genai.protos.Content(
            role="user",
            parts=[genai.protos.Part(
                function_response=genai.protos.FunctionResponse(
                    name="query_recipes",
                    response={"result": tool_result},
                )
            )],
        )
        contents_for_call2 = gemini_messages + [model_content, fn_response_content]
        response2 = model.generate_content(
            contents_for_call2,
            generation_config={"temperature": 0.7, "response_mime_type": "application/json"},
        )
        content = response2.text
    except Exception as e:
        logger.warning("Gemini Call 2 with protos failed: %s — injecting results as text", e)
        # Intentionally omit model_content (which contains the function_call) in this
        # fallback — including it without a matching function_response would confuse the
        # model. Instead we start a fresh user turn with the results as plain text.
        contents_for_call2 = gemini_messages + [
            {
                "role": "user",
                "parts": [{"text": (
                    f"Available recipes for recommendation:\n"
                    f"{json.dumps(tool_result, indent=2)}\n\n"
                    f"Respond ONLY with valid JSON."
                )}],
            }
        ]
        response2 = model.generate_content(
            contents_for_call2,
            generation_config={"temperature": 0.7, "response_mime_type": "application/json"},
        )
        content = response2.text

    return _parse_chat_response(content)


def _single_shot_gemini(system_prompt: str, messages: list[dict]) -> str:
    """Single-shot Gemini call with all recipes injected into system prompt."""
    import google.generativeai as genai

    api_key = getattr(settings, "GEMINI_API_KEY", None)
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    genai.configure(api_key=api_key)
    model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
    model = genai.GenerativeModel(model_name, system_instruction=system_prompt)

    response = model.generate_content(
        _to_gemini_messages(messages),
        generation_config={"temperature": 0.7, "response_mime_type": "application/json"},
    )
    return response.text


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------

def _chat_with_ollama(messages, user, depth, user_ing_ids, category_ing_ids) -> ChatResponse:
    """Two-call chat flow using Ollama with function calling."""
    import ollama

    host = getattr(settings, "OLLAMA_HOST", "http://localhost:11434")
    model_name = getattr(settings, "OLLAMA_CHAT_MODEL", "llama3.1:8b")
    system_prompt = _build_system_prompt(depth)

    ollama_messages = [{"role": "system", "content": system_prompt}] + messages
    tools = [{"type": "function", "function": TOOL_DESCRIPTION}]

    # --- Call 1: with tool definition ---
    logger.info("[Ollama/%s] Call 1 →", model_name)
    try:
        client = ollama.Client(host=host)
        response1 = client.chat(
            model=model_name,
            messages=ollama_messages,
            tools=tools,
            options={"temperature": 0.7},
        )
    except Exception as e:
        raise ValueError(f"Ollama Call 1 failed: {e}") from e

    # Extract tool calls (handle both dict-style and attribute-style access)
    try:
        msg1 = response1["message"]
        tool_calls = msg1.get("tool_calls") or []
    except (TypeError, KeyError):
        msg1_obj = getattr(response1, "message", None)
        tool_calls = getattr(msg1_obj, "tool_calls", None) or []
        msg1 = {"role": "assistant", "content": getattr(msg1_obj, "content", "") or ""}

    if tool_calls:
        all_names = [
            (tc.get("function", {}).get("name") if isinstance(tc, dict) else tc.function.name)
            for tc in tool_calls
        ]
        logger.debug("[Ollama/%s] raw tool_calls: %s", model_name, all_names)
    else:
        logger.debug("[Ollama/%s] no tool_calls in response", model_name)

    tool_result = None
    for tc in tool_calls:
        if isinstance(tc, dict):
            func_name = tc.get("function", {}).get("name", "")
            func_args = tc.get("function", {}).get("arguments", {})
        else:
            func_name = tc.function.name
            func_args = tc.function.arguments

        if func_name == "query_recipes":
            if isinstance(func_args, str):
                try:
                    func_args = json.loads(func_args)
                except json.JSONDecodeError:
                    func_args = {}
            logger.info("[Ollama/%s] tool call → query_recipes%s", model_name, _fmt_tool_args(func_args or {}))
            tool_result = _execute_query_recipes_relaxed(
                user, depth, func_args or {}, user_ing_ids, category_ing_ids
            )
            break

    if tool_result is None:
        logger.warning("[Ollama/%s] no query_recipes call — single-shot fallback", model_name)
        return _single_shot_recommendation(
            messages, user, depth, user_ing_ids, category_ing_ids, provider="ollama"
        )

    # --- Call 2: append tool result, request JSON ---
    logger.info("[Ollama/%s] Call 2 → (%d recipes)", model_name, len(tool_result))
    messages_for_call2 = ollama_messages + [
        msg1,
        {"role": "tool", "content": json.dumps(tool_result)},
    ]

    try:
        response2 = client.chat(
            model=model_name,
            messages=messages_for_call2,
            format=CHAT_RESPONSE_SCHEMA,
            options={"temperature": 0.7},
        )
        try:
            content = response2["message"]["content"]
        except (TypeError, KeyError):
            content = response2.message.content
    except Exception as e:
        raise ValueError(f"Ollama Call 2 failed: {e}") from e

    return _parse_chat_response(content)


def _single_shot_ollama(system_prompt: str, messages: list[dict]) -> str:
    """Single-shot Ollama call with all recipes injected into system prompt."""
    import ollama

    host = getattr(settings, "OLLAMA_HOST", "http://localhost:11434")
    model_name = getattr(settings, "OLLAMA_CHAT_MODEL", "llama3.1:8b")

    ollama_messages = [{"role": "system", "content": system_prompt}] + messages
    client = ollama.Client(host=host)

    response = client.chat(
        model=model_name,
        messages=ollama_messages,
        format=CHAT_RESPONSE_SCHEMA,
        options={"temperature": 0.7},
    )
    try:
        return response["message"]["content"]
    except (TypeError, KeyError):
        return response.message.content


# ---------------------------------------------------------------------------
# Fallback: single-shot (no tool calling)
# ---------------------------------------------------------------------------

def _single_shot_recommendation(
    messages, user, depth, user_ing_ids, category_ing_ids, provider: str
) -> ChatResponse:
    """Fallback: inject all makeable recipes directly into the prompt as a single LLM call."""
    # Reuse _execute_query_recipes with no filters to get all makeable recipes,
    # including ingredient match annotations (exact/substituted) for the LLM.
    recipe_list = _execute_query_recipes(user, depth, {}, user_ing_ids, category_ing_ids)

    logger.info("single-shot fallback | %d recipes injected: %s", len(recipe_list), _fmt_recipe_list(recipe_list))

    system_prompt = (
        _build_system_prompt(depth)
        + f"\n\nAvailable recipes (user can make all of these with their inventory):\n"
        + json.dumps(recipe_list, indent=2)
        + "\n\nRespond ONLY with valid JSON: "
        + '{"message": "...", "recommendations": [{"name": "...", "slug": "...", "blurb": "..."}]}'
    )

    if provider == "gemini":
        content = _single_shot_gemini(system_prompt, messages)
    else:
        content = _single_shot_ollama(system_prompt, messages)

    return _parse_chat_response(content)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_chat_response(content: str) -> ChatResponse:
    """Parse LLM JSON response into a ChatResponse."""
    try:
        content = content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.splitlines()
            start = 1
            end = len(lines) - 1 if lines and lines[-1].strip() == "```" else len(lines)
            content = "\n".join(lines[start:end])

        data = json.loads(content)
        message = str(data.get("message", ""))

        valid_recs = []
        for rec in data.get("recommendations", []):
            if isinstance(rec, dict) and rec.get("slug"):
                valid_recs.append({
                    "name": str(rec.get("name", "")),
                    "slug": str(rec.get("slug", "")),
                    "blurb": str(rec.get("blurb", "")),
                })

        logger.info(
            "response → %d recs: %s",
            len(valid_recs),
            _fmt_recipe_list(valid_recs) if valid_recs else "(none)",
        )
        logger.debug("response message: %r", message[:200])
        return ChatResponse(message=message, recommendations=valid_recs)

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("failed to parse response JSON: %s | content: %.300s", e, content)
        return ChatResponse(
            message=content[:1000] if content else "Unable to generate recommendations.",
            recommendations=[],
        )
