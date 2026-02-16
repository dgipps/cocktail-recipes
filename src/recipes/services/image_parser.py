"""Three-step recipe image parsing using LLM (Ollama or Gemini).

Step 1: OCR - Vision model extracts text from image
Step 2: Parse - Text LLM parses text into structured JSON
Step 3: Match - Fuzzy match ingredients against existing database
"""

import base64
import json
import logging
from difflib import SequenceMatcher
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Minimum similarity ratio (0-1) to trigger LLM verification
MATCH_THRESHOLD = 0.6

# OCR prompt - focused on accurate text extraction
OCR_PROMPT = """\
Read all text visible in this cocktail recipe image.

Guidelines:
- Transcribe text exactly as written, paying close attention to brand names
- For ingredients: keep the amount, unit, and full ingredient name together on ONE line
  Example: "1½ OUNCES BARBADILLO PRINCIPÉ AMONTILLADO SHERRY" (not split across lines)
- Include ALL ingredients, even those with small amounts like "1 dash" or "1 drop"
- Include ingredients without amounts (e.g., "CLUB SODA", "DRY CHAMPAGNE")
- Include countable ingredients (e.g., "1 EGG WHITE", "2 STRAWBERRIES")
- Preserve recipe names, measurements, and instructions accurately
"""

# Parse prompt - structured extraction from OCR text
PARSE_PROMPT = """\
Extract cocktail recipes from this OCR text.
Ignore headers, page numbers, and non-recipe content.

Text:
{extracted_text}

CRITICAL - Ingredient parsing:
- Extract EVERY ingredient, including small amounts (1 dash, 1 drop, etc.)
- Ingredient names may span multiple lines. If a line has NO amount/number at the start,
  it is a CONTINUATION of the previous ingredient name. Combine them.
  Example: "1½ OUNCES BARBADILLO PRINCIPÉ" followed by "AMONTILLADO SHERRY"
  → This is ONE ingredient: "1.5 oz Barbadillo Principé Amontillado Sherry"
- A new ingredient always starts with an amount (number) OR is a standalone ingredient

IMPORTANT - Handle these special ingredient types:
1. Countable items without units: "1 EGG WHITE" → amount: "1", unit: "whole", name: "Egg White"
   Other examples: "2 STRAWBERRIES", "1 ORANGE SLICE", "3 MINT LEAVES"
2. Toppers/fillers with no amount: "CLUB SODA" → amount: "", unit: "top", name: "Club Soda"
   Other examples: "DRY CHAMPAGNE", "GINGER BEER", "SODA WATER"
3. These ARE ingredients - do not skip them!

Formatting rules:
- Convert fractions to decimals: 1½ → 1.5, ¾ → 0.75, ¼ → 0.25
- Normalize units: ounce/ounces → oz, teaspoon → tsp, tablespoon → tbsp
- Keep brand names exactly as written
- If no page number visible, use null for page

Return ONLY valid JSON matching this structure:
{{
  "recipes": [
    {{
      "name": "Recipe Name",
      "page": 123,
      "ingredients": [
        {{"amount": "1.5", "unit": "oz", "name": "Ingredient Name"}}
      ],
      "method": "Instructions...",
      "garnish": "Garnish description or null",
      "notes": "Optional notes or null"
    }}
  ]
}}
"""

# JSON schema for structured output (Ollama format)
RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "recipes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                    "ingredients": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "amount": {"type": "string"},
                                "unit": {"type": "string"},
                                "name": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                    "method": {"type": "string"},
                    "garnish": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                },
                "required": ["name", "ingredients"],
            },
        }
    },
    "required": ["recipes"],
}

# Prompt for ingredient matching verification
INGREDIENT_MATCH_PROMPT = """\
Do these two ingredient names refer to the SAME ingredient?

OCR text: "{parsed_name}"
Database: "{existing_name}"

STEP 1 - Check for different flavors/variants (answer "no" if different):
- LICORICE vs LAVENDER = different flavors → no
- ORANGE vs ANGOSTURA = different types → no
- BLANC vs DRY vs ROUGE = different styles → no

STEP 2 - If same flavor/variant, allow for OCR misspellings:
- "DOLLAR" = "DOLIN" (OCR error) → yes
- "LUARDOR" = "LUXARDO" (OCR error) → yes
- "Scrapy's" = "SCRAPPY'S" (OCR error) → yes

Answer "no" if the flavor/variant/type is different.
Answer "yes" only if it's the same product with OCR spelling errors.

Answer with ONLY "yes" or "no".
"""


class ParseError(Exception):
    """Raised when image parsing fails."""

    pass


def _get_provider() -> str:
    """Get the LLM provider to use (ollama or gemini)."""
    return getattr(settings, "LLM_PROVIDER", "ollama").lower()


def _get_gemini_model():
    """Get configured Gemini model instance."""
    import google.generativeai as genai

    api_key = getattr(settings, "GEMINI_API_KEY", None)
    if not api_key:
        raise ParseError("GEMINI_API_KEY not configured")

    genai.configure(api_key=api_key)
    model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
    return genai.GenerativeModel(model_name)


def extract_text_from_image(image_path: str | Path) -> str:
    """
    Step 1: Use vision model to OCR the image.

    Args:
        image_path: Path to the image file.

    Returns:
        Raw text extracted from the image.

    Raises:
        ParseError: If OCR fails.
    """
    image_path = Path(image_path)

    if not image_path.exists():
        raise ParseError(f"Image file not found: {image_path}")

    provider = _get_provider()
    logger.info(f"OCR with provider: {provider}")

    if provider == "gemini":
        text = _ocr_with_gemini(image_path)
    else:
        text = _ocr_with_ollama(image_path)

    logger.info(f"OCR extracted {len(text)} chars")
    logger.debug(f"OCR text: {text}")

    if not text.strip():
        raise ParseError("OCR returned empty text")

    return text


def _ocr_with_ollama(image_path: Path) -> str:
    """OCR using Ollama vision model."""
    import httpx

    host = getattr(settings, "OLLAMA_HOST", "http://localhost:11434")
    model = getattr(settings, "OLLAMA_OCR_MODEL", "minicpm-v")

    logger.info(f"OCR with Ollama {model}: {image_path}")

    try:
        with open(image_path, "rb") as f:
            img_base64 = base64.b64encode(f.read()).decode()

        with httpx.Client(timeout=180) as http_client:
            response = http_client.post(
                f"{host}/api/generate",
                json={
                    "model": model,
                    "prompt": OCR_PROMPT,
                    "images": [img_base64],
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 8192},
                },
            )
            response.raise_for_status()
            return response.json()["response"]

    except Exception as e:
        raise ParseError(f"Ollama OCR failed: {e}") from e


def _ocr_with_gemini(image_path: Path) -> str:
    """OCR using Google Gemini vision model."""
    from PIL import Image

    logger.info(f"OCR with Gemini: {image_path}")

    try:
        model = _get_gemini_model()
        image = Image.open(image_path)

        response = model.generate_content(
            [OCR_PROMPT, image],
            generation_config={"temperature": 0.1, "max_output_tokens": 8192},
        )

        return response.text

    except Exception as e:
        raise ParseError(f"Gemini OCR failed: {e}") from e


def parse_recipe_text(text: str) -> dict:
    """
    Step 2: Use text LLM to parse OCR text into structured recipes.

    Args:
        text: Raw OCR text from the image.

    Returns:
        Parsed recipe data dict.

    Raises:
        ParseError: If parsing fails or returns invalid data.
    """
    provider = _get_provider()
    logger.info(f"Parsing text with provider: {provider}")

    if provider == "gemini":
        parsed = _parse_with_gemini(text)
    else:
        parsed = _parse_with_ollama(text)

    # Validate structure
    if not isinstance(parsed, dict):
        raise ParseError(f"Expected dict, got {type(parsed).__name__}")

    if "recipes" not in parsed:
        raise ParseError("Response missing 'recipes' key")

    if not isinstance(parsed["recipes"], list):
        raise ParseError("'recipes' must be a list")

    for i, recipe in enumerate(parsed["recipes"]):
        _validate_recipe(recipe, i)

    return parsed


def _parse_with_ollama(text: str) -> dict:
    """Parse recipe text using Ollama."""
    import ollama

    host = getattr(settings, "OLLAMA_HOST", "http://localhost:11434")
    model = getattr(settings, "OLLAMA_PARSE_MODEL", "llama3.2")

    logger.info(f"Parsing with Ollama {model}")
    prompt = PARSE_PROMPT.format(extracted_text=text)

    try:
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format=RECIPE_SCHEMA,
            options={"temperature": 0.1, "num_predict": 8192},
        )
        content = response["message"]["content"]

    except Exception as e:
        raise ParseError(f"Ollama parse failed: {e}") from e

    logger.debug(f"Parse response: {content}")

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ParseError(f"Failed to parse JSON: {e}\nContent: {content[:500]}") from e


def _parse_with_gemini(text: str) -> dict:
    """Parse recipe text using Gemini."""
    logger.info("Parsing with Gemini")
    prompt = PARSE_PROMPT.format(extracted_text=text)

    try:
        model = _get_gemini_model()
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.1,
                "max_output_tokens": 8192,
                "response_mime_type": "application/json",
            },
        )
        content = response.text

    except Exception as e:
        raise ParseError(f"Gemini parse failed: {e}") from e

    logger.debug(f"Parse response: {content}")

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ParseError(f"Failed to parse JSON: {e}\nContent: {content[:500]}") from e


def match_ingredients(parsed_data: dict) -> dict:
    """
    Step 3: Match parsed ingredient names against existing database ingredients.

    Uses fuzzy matching to find candidates, then LLM to verify matches.
    Updates ingredient names in-place when matches are found.
    Adds a 'matching_log' field to parsed_data with details of each decision.

    Args:
        parsed_data: Parsed recipe data from step 2.

    Returns:
        Updated parsed_data with corrected ingredient names and matching_log.
    """
    from ingredients.models import Ingredient

    matching_log = []

    existing_ingredients = list(Ingredient.objects.values_list("name", flat=True))

    if not existing_ingredients:
        logger.info("No existing ingredients in database, skipping matching")
        parsed_data["matching_log"] = matching_log
        return parsed_data

    logger.info(f"Matching against {len(existing_ingredients)} existing ingredients")

    name_lookup = {name.lower(): name for name in existing_ingredients}

    for recipe in parsed_data.get("recipes", []):
        recipe_name = recipe.get("name", "Unknown")

        for ingredient in recipe.get("ingredients", []):
            parsed_name = ingredient.get("name", "")
            if not parsed_name:
                continue

            log_entry = {
                "recipe": recipe_name,
                "original": parsed_name,
                "status": None,
                "matched_to": None,
                "similarity": None,
                "candidates_checked": [],
            }

            # Check for exact match first (case-insensitive)
            if parsed_name.lower() in name_lookup:
                db_name = name_lookup[parsed_name.lower()]
                ingredient["name"] = db_name
                log_entry["status"] = "exact_match"
                log_entry["matched_to"] = db_name
                logger.debug(f"[{recipe_name}] Exact match: '{parsed_name}'")
                matching_log.append(log_entry)
                continue

            # Find fuzzy matches above threshold
            candidates = _find_fuzzy_matches(parsed_name, existing_ingredients)

            if not candidates:
                log_entry["status"] = "no_match"
                ingredient["name"] = parsed_name.upper()
                logger.info(
                    f"[{recipe_name}] No match found for: '{parsed_name}' "
                    f"→ '{ingredient['name']}' (new ingredient)"
                )
                matching_log.append(log_entry)
                continue

            logger.debug(
                f"[{recipe_name}] Checking '{parsed_name}' against candidates: "
                f"{[(c, f'{s:.0%}') for c, s in candidates]}"
            )

            # Use LLM to verify candidates
            matched = False
            for candidate_name, similarity in candidates:
                log_entry["candidates_checked"].append({
                    "name": candidate_name,
                    "similarity": round(similarity, 3),
                })

                is_match = _verify_ingredient_match(parsed_name, candidate_name)

                if is_match:
                    logger.info(
                        f"[{recipe_name}] MATCHED: '{parsed_name}' → "
                        f"'{candidate_name}' (similarity: {similarity:.0%})"
                    )
                    ingredient["name"] = candidate_name
                    log_entry["status"] = "fuzzy_matched"
                    log_entry["matched_to"] = candidate_name
                    log_entry["similarity"] = round(similarity, 3)
                    matched = True
                    break
                else:
                    logger.debug(
                        f"[{recipe_name}] LLM rejected: '{parsed_name}' ≠ "
                        f"'{candidate_name}'"
                    )

            if not matched:
                log_entry["status"] = "no_match"
                ingredient["name"] = parsed_name.upper()
                logger.info(
                    f"[{recipe_name}] No match confirmed for: '{parsed_name}' "
                    f"→ '{ingredient['name']}' (checked {len(candidates)} candidates)"
                )

            matching_log.append(log_entry)

    exact = sum(1 for e in matching_log if e["status"] == "exact_match")
    fuzzy = sum(1 for e in matching_log if e["status"] == "fuzzy_matched")
    no_match = sum(1 for e in matching_log if e["status"] == "no_match")

    logger.info(
        f"Ingredient matching complete: {exact} exact, {fuzzy} fuzzy, "
        f"{no_match} new/unmatched"
    )

    parsed_data["matching_log"] = matching_log
    return parsed_data


def _find_fuzzy_matches(
    parsed_name: str,
    existing_names: list[str],
    threshold: float = MATCH_THRESHOLD,
    max_candidates: int = 3,
) -> list[tuple[str, float]]:
    """Find existing ingredient names that fuzzy-match the parsed name."""
    matches = []
    parsed_lower = parsed_name.lower()

    for existing_name in existing_names:
        existing_lower = existing_name.lower()
        ratio = SequenceMatcher(None, parsed_lower, existing_lower).ratio()

        if ratio >= threshold:
            matches.append((existing_name, ratio))

    matches.sort(key=lambda x: x[1], reverse=True)
    return matches[:max_candidates]


def _verify_ingredient_match(parsed_name: str, existing_name: str) -> bool:
    """Use LLM to verify if two ingredient names refer to the same ingredient."""
    provider = _get_provider()

    if provider == "gemini":
        return _verify_with_gemini(parsed_name, existing_name)
    else:
        return _verify_with_ollama(parsed_name, existing_name)


def _verify_with_ollama(parsed_name: str, existing_name: str) -> bool:
    """Verify ingredient match using Ollama."""
    import ollama

    host = getattr(settings, "OLLAMA_HOST", "http://localhost:11434")
    model = getattr(settings, "OLLAMA_PARSE_MODEL", "llama3.2")

    prompt = INGREDIENT_MATCH_PROMPT.format(
        parsed_name=parsed_name,
        existing_name=existing_name,
    )

    try:
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1, "num_predict": 10},
        )
        answer = response["message"]["content"].strip().lower()
        is_match = answer.startswith("yes")
        logger.info(
            f"LLM verify '{parsed_name}' vs '{existing_name}': "
            f"answer='{answer}' → {is_match}"
        )
        return is_match

    except Exception as e:
        logger.warning(f"LLM verification failed for '{parsed_name}': {e}")
        return False


def _verify_with_gemini(parsed_name: str, existing_name: str) -> bool:
    """Verify ingredient match using Gemini."""
    prompt = INGREDIENT_MATCH_PROMPT.format(
        parsed_name=parsed_name,
        existing_name=existing_name,
    )

    try:
        model = _get_gemini_model()
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.1, "max_output_tokens": 10},
        )
        answer = response.text.strip().lower()
        is_match = answer.startswith("yes")
        logger.info(
            f"LLM verify '{parsed_name}' vs '{existing_name}': "
            f"answer='{answer}' → {is_match}"
        )
        return is_match

    except Exception as e:
        logger.warning(f"Gemini verification failed for '{parsed_name}': {e}")
        return False


def parse_recipe_image(image_path: str | Path) -> tuple[str, dict]:
    """
    Parse a recipe image using three-step approach.

    Step 1: OCR - Extract text from image
    Step 2: Parse - Convert text to structured JSON
    Step 3: Match - Fuzzy match ingredients against database

    Args:
        image_path: Path to the image file.

    Returns:
        Tuple of (raw_ocr_text, parsed_data) where:
        - raw_ocr_text: The raw text extracted by OCR (for debugging)
        - parsed_data: Structured recipe data dict with matched ingredients

    Raises:
        ParseError: If OCR or parsing fails.
    """
    raw_text = extract_text_from_image(image_path)
    parsed = parse_recipe_text(raw_text)
    parsed = match_ingredients(parsed)
    return raw_text, parsed


def _validate_recipe(recipe: dict, index: int) -> None:
    """Validate a single recipe dict."""
    if not isinstance(recipe, dict):
        raise ParseError(f"Recipe {index}: expected dict, got {type(recipe).__name__}")

    if "name" not in recipe:
        raise ParseError(f"Recipe {index}: missing 'name'")

    if "ingredients" not in recipe:
        raise ParseError(f"Recipe {index}: missing 'ingredients'")

    if not isinstance(recipe["ingredients"], list):
        raise ParseError(f"Recipe {index}: 'ingredients' must be a list")

    for j, ing in enumerate(recipe["ingredients"]):
        if not isinstance(ing, dict):
            type_name = type(ing).__name__
            raise ParseError(
                f"Recipe {index}, ingredient {j}: expected dict, got {type_name}"
            )
        if "name" not in ing:
            raise ParseError(f"Recipe {index}, ingredient {j}: missing 'name'")
