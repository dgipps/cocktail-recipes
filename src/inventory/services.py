"""Services for inventory-related queries."""

from django.db.models import Count, F, Q

from ingredients.models import Ingredient, IngredientCategoryAncestor
from recipes.models import Recipe

from .models import UserInventory


def get_makeable_recipes(user, max_depth=1):
    """
    Find recipes user can make with their inventory.

    Args:
        user: The user whose inventory to check
        max_depth: Category matching depth
            0 = exact ingredient match only
            1 = same category (e.g., any London Dry Gin)
            2 = parent category (e.g., any Gin)
            3+ = higher ancestors (e.g., any Spirit)

    Returns:
        QuerySet of Recipe objects the user can make
    """
    # 1. Get user's in-stock ingredient IDs
    user_ing_ids = set(
        UserInventory.objects.filter(user=user, in_stock=True).values_list(
            "ingredient_id", flat=True
        )
    )

    if not user_ing_ids:
        return Recipe.objects.none()

    if max_depth == 0:
        # Exact match only
        satisfiable_ids = user_ing_ids
    else:
        # max_depth controls how far up the category tree we go:
        #   1 = same leaf category only (closure depth 0)
        #   2 = include parent category (closure depth 0-1)
        #   3 = include grandparent (closure depth 0-2)
        # So closure_depth = max_depth - 1
        closure_depth = max_depth - 1

        # 2. Get categories of user's ingredients + ancestors up to closure_depth
        #    e.g., if user has Plymouth (Navy Strength), at max_depth=2:
        #    closure_depth=1, so we get {Navy Strength (d=0), Gin (d=1)}
        user_ancestor_categories = set(
            IngredientCategoryAncestor.objects.filter(
                category__ingredients__in=user_ing_ids, depth__lte=closure_depth
            ).values_list("ancestor_id", flat=True)
        )

        # 3. Get ALL categories that descend from user's ancestor categories
        #    This includes siblings! e.g., if user has Navy Strength and we
        #    found Gin as ancestor, we now include London Dry as a descendant of Gin
        all_satisfiable_categories = set(
            IngredientCategoryAncestor.objects.filter(
                ancestor__in=user_ancestor_categories
            ).values_list("category_id", flat=True)
        )

        # 4. Find ALL ingredients in those categories
        satisfiable_ids = set(
            Ingredient.objects.filter(
                categories__in=all_satisfiable_categories
            ).values_list("id", flat=True)
        )

        # Also include exact matches (for ingredients without categories)
        satisfiable_ids |= user_ing_ids

    # 5. Find recipes where ALL required ingredients are satisfiable
    #    Also annotate with exact vs category match counts
    return (
        Recipe.objects.annotate(
            required_count=Count(
                "recipe_ingredients", filter=Q(recipe_ingredients__optional=False)
            ),
            satisfied_count=Count(
                "recipe_ingredients",
                filter=Q(
                    recipe_ingredients__optional=False,
                    recipe_ingredients__ingredient_id__in=satisfiable_ids,
                ),
            ),
            exact_match_count=Count(
                "recipe_ingredients",
                filter=Q(
                    recipe_ingredients__optional=False,
                    recipe_ingredients__ingredient_id__in=user_ing_ids,
                ),
            ),
            category_match_count=F("required_count") - F("exact_match_count"),
        )
        .filter(required_count=F("satisfied_count"), required_count__gt=0)
        .order_by("name")
    )


def get_user_inventory_stats(user):
    """Get basic stats about user's inventory."""
    in_stock = UserInventory.objects.filter(user=user, in_stock=True).count()
    total = Ingredient.objects.count()
    return {"in_stock": in_stock, "total": total}
