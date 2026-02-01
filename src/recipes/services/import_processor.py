"""Process approved recipe imports into Recipe objects."""

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from ingredients.models import Ingredient
from recipes.measurements import MeasurementUnit
from recipes.models import Recipe, RecipeImport, RecipeIngredient

logger = logging.getLogger(__name__)

# Map common unit variations to our MeasurementUnit values
UNIT_ALIASES: dict[str, str] = {
    "oz": MeasurementUnit.OZ,
    "ounce": MeasurementUnit.OZ,
    "ounces": MeasurementUnit.OZ,
    "ml": MeasurementUnit.ML,
    "milliliter": MeasurementUnit.ML,
    "milliliters": MeasurementUnit.ML,
    "cl": MeasurementUnit.CL,
    "centiliter": MeasurementUnit.CL,
    "centiliters": MeasurementUnit.CL,
    "tsp": MeasurementUnit.TSP,
    "teaspoon": MeasurementUnit.TSP,
    "teaspoons": MeasurementUnit.TSP,
    "tbsp": MeasurementUnit.TBSP,
    "tablespoon": MeasurementUnit.TBSP,
    "tablespoons": MeasurementUnit.TBSP,
    "barspoon": MeasurementUnit.BARSPOON,
    "barspoons": MeasurementUnit.BARSPOON,
    "dash": MeasurementUnit.DASH,
    "dashes": MeasurementUnit.DASH,
    "drop": MeasurementUnit.DROP,
    "drops": MeasurementUnit.DROP,
    "rinse": MeasurementUnit.RINSE,
    "float": MeasurementUnit.FLOAT,
    "top": MeasurementUnit.TOP,
    "splash": MeasurementUnit.SPLASH,
    "whole": MeasurementUnit.WHOLE,
    "piece": MeasurementUnit.PIECE,
    "pieces": MeasurementUnit.PIECE,
    "slice": MeasurementUnit.SLICE,
    "slices": MeasurementUnit.SLICE,
    "wedge": MeasurementUnit.WEDGE,
    "wedges": MeasurementUnit.WEDGE,
    "sprig": MeasurementUnit.SPRIG,
    "sprigs": MeasurementUnit.SPRIG,
    "leaf": MeasurementUnit.LEAF,
    "leaves": MeasurementUnit.LEAF,
}


def normalize_unit(unit: str | None) -> str:
    """Normalize a unit string to a MeasurementUnit value."""
    if not unit:
        return ""
    unit_lower = unit.lower().strip()
    return UNIT_ALIASES.get(unit_lower, unit_lower)


def parse_amount(amount_str: str | None) -> Decimal | None:
    """Parse an amount string to Decimal."""
    if not amount_str:
        return None
    try:
        return Decimal(str(amount_str).strip())
    except InvalidOperation:
        logger.warning(f"Could not parse amount: {amount_str}")
        return None


def get_or_create_ingredient(name: str) -> tuple[Ingredient, bool]:
    """
    Get or create an ingredient by name.

    Returns (ingredient, created) tuple.
    If created, ingredient is flagged for categorization.
    """
    slug = slugify(name)[:50]  # Ensure slug fits in field

    # Try exact match first
    ingredient = Ingredient.objects.filter(name__iexact=name).first()
    if ingredient:
        return ingredient, False

    # Try slug match (handles case differences)
    ingredient = Ingredient.objects.filter(slug=slug).first()
    if ingredient:
        return ingredient, False

    # Create new ingredient, flagged for categorization
    ingredient = Ingredient.objects.create(
        name=name,
        slug=slug,
        needs_categorization=True,
    )
    logger.info(f"Created new ingredient: {name}")
    return ingredient, True


def generate_unique_slug(base_slug: str) -> str:
    """Generate a unique recipe slug by appending numbers if needed."""
    slug = base_slug
    counter = 1
    while Recipe.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


@transaction.atomic
def create_recipe_from_data(
    recipe_data: dict,
    source: str = "",
) -> Recipe:
    """
    Create a Recipe and its RecipeIngredients from parsed data.

    Args:
        recipe_data: Dict with keys: name, page, ingredients, method, garnish
        source: Optional source name (e.g., "Death & Co")

    Returns:
        Created Recipe instance.
    """
    name = recipe_data.get("name", "Untitled")
    base_slug = slugify(name)[:50]
    slug = generate_unique_slug(base_slug)

    recipe = Recipe.objects.create(
        name=name,
        slug=slug,
        source=source,
        page=recipe_data.get("page"),
        method=recipe_data.get("method", ""),
        garnish=recipe_data.get("garnish", ""),
    )

    # Create recipe ingredients
    for order, ing_data in enumerate(recipe_data.get("ingredients", [])):
        ingredient, _ = get_or_create_ingredient(ing_data.get("name", "Unknown"))

        RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=ingredient,
            amount=parse_amount(ing_data.get("amount")),
            unit=normalize_unit(ing_data.get("unit")),
            order=order,
        )

    return recipe


@transaction.atomic
def update_recipe_from_data(
    recipe: Recipe,
    recipe_data: dict,
) -> Recipe:
    """
    Update an existing Recipe from parsed data.

    Replaces all ingredients with new ones from data.
    """
    # Update recipe fields
    if recipe_data.get("page"):
        recipe.page = recipe_data["page"]
    if recipe_data.get("method"):
        recipe.method = recipe_data["method"]
    if recipe_data.get("garnish"):
        recipe.garnish = recipe_data["garnish"]
    recipe.save()

    # Replace ingredients
    recipe.recipe_ingredients.all().delete()

    for order, ing_data in enumerate(recipe_data.get("ingredients", [])):
        ingredient, _ = get_or_create_ingredient(ing_data.get("name", "Unknown"))

        RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=ingredient,
            amount=parse_amount(ing_data.get("amount")),
            unit=normalize_unit(ing_data.get("unit")),
            order=order,
        )

    return recipe


def find_matching_recipe(name: str) -> Recipe | None:
    """Find an existing recipe that matches by name."""
    return Recipe.objects.filter(name__iexact=name).first()


@transaction.atomic
def approve_import(
    recipe_import: RecipeImport,
    recipe_index: int = 0,
    source: str = "",
) -> Recipe:
    """
    Approve a recipe import and create/update the Recipe.

    Args:
        recipe_import: The RecipeImport to approve
        recipe_index: Which recipe in parsed_data to import (default first)
        source: Source name for the recipe

    Returns:
        Created or updated Recipe.

    Raises:
        ValueError: If import cannot be approved.
    """
    if recipe_import.status == RecipeImport.Status.APPROVED:
        raise ValueError("Import already approved")

    if not recipe_import.parsed_data:
        raise ValueError("No parsed data to import")

    recipes = recipe_import.parsed_data.get("recipes", [])
    if not recipes:
        raise ValueError("No recipes in parsed data")

    if recipe_index >= len(recipes):
        raise ValueError(f"Recipe index {recipe_index} out of range")

    recipe_data = recipes[recipe_index]
    name = recipe_data.get("name", "")

    # Check for existing recipe to update
    existing = find_matching_recipe(name)

    if existing:
        recipe = update_recipe_from_data(existing, recipe_data)
        logger.info(f"Updated existing recipe: {name}")
    else:
        recipe = create_recipe_from_data(recipe_data, source=source)
        logger.info(f"Created new recipe: {name}")

    # Update import status
    recipe_import.status = RecipeImport.Status.APPROVED
    recipe_import.recipe = recipe
    recipe_import.approved_at = timezone.now()
    recipe_import.save()

    return recipe


@transaction.atomic
def reject_import(recipe_import: RecipeImport) -> None:
    """
    Reject a recipe import.

    Args:
        recipe_import: The RecipeImport to reject.
    """
    recipe_import.status = RecipeImport.Status.REJECTED
    recipe_import.save()
    logger.info(f"Rejected import {recipe_import.pk}")
