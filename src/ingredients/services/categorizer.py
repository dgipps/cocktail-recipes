"""LLM-powered ingredient categorization service.

Uses a hierarchical approach:
1. Get top-level categories
2. Ask LLM to pick best top-level category
3. Drill down into subcategories until most specific match
"""

import logging

import ollama
from django.conf import settings
from django.db.models import Count

from ingredients.models import (
    Ingredient,
    IngredientCategory,
    IngredientCategoryAncestor,
    IngredientCategorySuggestion,
)

logger = logging.getLogger(__name__)

# Minimum confidence to create a suggestion
MIN_CONFIDENCE = 0.3

# Maximum hierarchy depth to traverse
MAX_DEPTH = 5

# JSON schema for category selection
CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "category_slug": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["category_slug", "confidence", "reasoning"],
}

# Prompt for top-level category selection
TOP_LEVEL_PROMPT = """\
You are categorizing cocktail ingredients. Select the BEST matching top-level category.

Ingredient: {ingredient_name}

Available categories (format: "slug: Name"):
{categories_list}

Instructions:
- Pick the single best matching category based on what this ingredient IS
- Consider: Is it a spirit? A liqueur? A mixer? Fresh produce? A sweetener?
- IMPORTANT: Return ONLY the slug (the part before the colon), not the name
- If the ingredient doesn't fit any category, set category_slug to null
- Confidence should be 0.0-1.0 (1.0 = certain, 0.5 = unsure)

Return JSON with: category_slug (the slug EXACTLY as shown, or null), confidence, reasoning
"""

# Prompt for subcategory refinement
SUBCATEGORY_PROMPT = """\
You are refining the category for a cocktail ingredient.
We've determined this ingredient belongs to: {parent_category}

Ingredient: {ingredient_name}

Available subcategories (format: "slug: Name"):
{subcategories_list}

Instructions:
- Pick the most SPECIFIC matching subcategory from the list above
- IMPORTANT: Return ONLY the slug (the part before the colon), exactly as shown
- If none of the subcategories fit, set category_slug to "parent" to stay at current level
- Be as specific as possible

Return JSON with: category_slug (exact slug from list, or "parent"), confidence, reasoning
"""


class CategorizationError(Exception):
    """Raised when ingredient categorization fails."""

    pass


def get_top_level_categories() -> list[IngredientCategory]:
    """
    Get all root-level categories (those with no parent).

    A category is top-level if its only ancestor_link is itself (depth=0).
    """
    # Categories where the only ancestor link is self (depth=0)
    # This means no parent categories exist
    top_level_ids = (
        IngredientCategoryAncestor.objects.values("category")
        .annotate(ancestor_count=Count("ancestor"))
        .filter(ancestor_count=1)
        .values_list("category", flat=True)
    )
    return list(
        IngredientCategory.objects.filter(id__in=top_level_ids).order_by("name")
    )


def get_subcategories(parent_category: IngredientCategory) -> list[IngredientCategory]:
    """
    Get direct children of a category.

    Direct children are categories where this category is an ancestor at depth=1.
    """
    child_ids = IngredientCategoryAncestor.objects.filter(
        ancestor=parent_category,
        depth=1,
    ).values_list("category", flat=True)
    return list(IngredientCategory.objects.filter(id__in=child_ids).order_by("name"))


def _format_categories_list(categories: list[IngredientCategory]) -> str:
    """Format categories for prompt display."""
    lines = []
    for cat in categories:
        # Count ingredients in this category
        count = cat.ingredients.count()
        lines.append(f"- {cat.slug}: {cat.name} ({count} ingredients)")
    return "\n".join(lines)


def _call_llm(prompt: str) -> dict:
    """Call Ollama LLM with structured output."""
    host = getattr(settings, "OLLAMA_HOST", "http://localhost:11434")
    model = getattr(settings, "OLLAMA_PARSE_MODEL", "llama3.2")

    try:
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format=CATEGORY_SCHEMA,
            options={"temperature": 0.1, "num_predict": 256},
        )
        import json

        content = response["message"]["content"]
        return json.loads(content)

    except ollama.ResponseError as e:
        raise CategorizationError(f"Ollama API error: {e}") from e
    except Exception as e:
        raise CategorizationError(f"LLM call failed: {e}") from e


def suggest_category_for_ingredient(
    ingredient: Ingredient,
) -> tuple[IngredientCategory | None, float, str]:
    """
    Use LLM to suggest the best category for an ingredient.

    Uses hierarchical approach:
    1. Pick top-level category
    2. Drill down into subcategories

    Returns:
        (category, confidence, reasoning) tuple
        category is None if no good match found
    """
    # Step 1: Get top-level categories
    top_level = get_top_level_categories()
    if not top_level:
        logger.warning("No top-level categories found")
        return None, 0.0, "No categories available"

    logger.info(f"Categorizing '{ingredient.name}' against {len(top_level)} categories")

    # Step 2: Ask LLM to pick top-level category
    prompt = TOP_LEVEL_PROMPT.format(
        ingredient_name=ingredient.name,
        categories_list=_format_categories_list(top_level),
    )

    result = _call_llm(prompt)
    category_slug = result.get("category_slug")
    confidence = result.get("confidence", 0.0)
    reasoning = result.get("reasoning", "")

    logger.info(f"Top-level result: {category_slug} (confidence: {confidence})")

    if not category_slug:
        return None, confidence, reasoning

    # Find the selected category
    try:
        current_category = IngredientCategory.objects.get(slug=category_slug)
    except IngredientCategory.DoesNotExist:
        logger.warning(f"LLM returned unknown category slug: {category_slug}")
        return None, 0.0, f"Unknown category: {category_slug}"

    # Step 3: Drill down into subcategories
    depth = 0
    while depth < MAX_DEPTH:
        subcategories = get_subcategories(current_category)
        if not subcategories:
            # No more subcategories - we're at the most specific level
            break

        logger.debug(
            f"Drilling down from '{current_category.name}' "
            f"into {len(subcategories)} subcategories"
        )

        prompt = SUBCATEGORY_PROMPT.format(
            ingredient_name=ingredient.name,
            parent_category=current_category.name,
            subcategories_list=_format_categories_list(subcategories),
        )

        result = _call_llm(prompt)
        sub_slug = result.get("category_slug")
        sub_confidence = result.get("confidence", 0.0)
        sub_reasoning = result.get("reasoning", "")

        logger.info(f"Subcategory result: {sub_slug} (confidence: {sub_confidence})")

        if not sub_slug or sub_slug == "parent":
            # Stay at current level
            reasoning = sub_reasoning or reasoning
            break

        # Move to subcategory
        try:
            current_category = IngredientCategory.objects.get(slug=sub_slug)
            confidence = sub_confidence
            reasoning = sub_reasoning
        except IngredientCategory.DoesNotExist:
            logger.warning(f"LLM returned unknown subcategory slug: {sub_slug}")
            break

        depth += 1

    return current_category, confidence, reasoning


def categorize_ingredient(
    ingredient: Ingredient,
) -> IngredientCategorySuggestion | None:
    """
    Main entry point - suggest a category for an ingredient.

    Creates an IngredientCategorySuggestion for admin review.

    Args:
        ingredient: The ingredient to categorize

    Returns:
        IngredientCategorySuggestion if created, None if no good match

    Raises:
        CategorizationError: If LLM is unavailable or returns invalid data
    """
    logger.info(f"Starting categorization for: {ingredient.name}")

    category, confidence, reasoning = suggest_category_for_ingredient(ingredient)

    if not category:
        logger.info(f"No category match for '{ingredient.name}': {reasoning}")
        return None

    if confidence < MIN_CONFIDENCE:
        logger.info(
            f"Confidence too low for '{ingredient.name}': "
            f"{confidence:.0%} < {MIN_CONFIDENCE:.0%}"
        )
        return None

    # Check for existing pending suggestion for same category
    existing = IngredientCategorySuggestion.objects.filter(
        ingredient=ingredient,
        suggested_category=category,
        status=IngredientCategorySuggestion.Status.PENDING,
    ).first()

    if existing:
        logger.info(f"Suggestion exists: '{ingredient.name}' -> '{category.name}'")
        return existing

    # Create new suggestion
    suggestion = IngredientCategorySuggestion.objects.create(
        ingredient=ingredient,
        suggested_category=category,
        confidence=confidence,
        reasoning=reasoning,
    )

    logger.info(
        f"Created suggestion: '{ingredient.name}' -> '{category.name}' "
        f"(confidence: {confidence:.0%})"
    )

    return suggestion
