"""Frontend views for recipes."""

from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Max
from django.shortcuts import get_object_or_404, render

from ingredients.models import Ingredient, IngredientCategory, IngredientCategoryAncestor
from inventory.models import UserInventory

from .models import Recipe


@login_required
def recipe_list(request):
    """Display all recipes with search functionality."""
    search = request.GET.get("q", "")
    cat = request.GET.get("cat", "")

    if search:
        # Fuzzy search using trigram similarity
        recipes = (
            Recipe.objects.prefetch_related("recipe_ingredients__ingredient")
            .annotate(similarity=TrigramSimilarity("name", search))
            .filter(similarity__gte=0.2)
            .order_by("-similarity", "name")
        )
    else:
        recipes = Recipe.objects.prefetch_related(
            "recipe_ingredients__ingredient"
        ).order_by("name")

    categories = (
        IngredientCategory.objects
        .annotate(max_depth=Max("ancestor_links__depth"))
        .filter(max_depth__in=[1, 2])
        .order_by("name")
    )

    selected_cat = None
    if cat:
        selected_cat = IngredientCategory.objects.filter(name__iexact=cat).first()
        if selected_cat:
            cat_ids = IngredientCategoryAncestor.objects.filter(
                ancestor=selected_cat
            ).values_list("category_id", flat=True)
            recipes = recipes.filter(
                recipe_ingredients__ingredient__categories__in=cat_ids
            ).distinct()

    context = {
        "recipes": recipes,
        "search": search,
        "cat": cat,
        "categories": categories,
        "selected_category": selected_cat,
    }

    # HTMX partial response for search
    if request.headers.get("HX-Request"):
        return render(
            request,
            "recipes/partials/recipe_results.html",
            {"recipes": recipes},
        )

    return render(request, "recipes/recipe_list.html", context)


@login_required
def recipe_detail(request, slug):
    """Display a single recipe with full details."""
    recipe = get_object_or_404(
        Recipe.objects.prefetch_related("recipe_ingredients__ingredient"),
        slug=slug,
    )
    return render(request, "recipes/recipe_detail.html", {"recipe": recipe})


def _get_ingredient_match_sets(user, max_depth):
    """Get sets of ingredient IDs for exact and category matches."""
    user_ing_ids = set(
        UserInventory.objects.filter(user=user, in_stock=True).values_list(
            "ingredient_id", flat=True
        )
    )

    if not user_ing_ids or max_depth == 0:
        return user_ing_ids, set()

    closure_depth = max_depth - 1

    user_ancestor_categories = set(
        IngredientCategoryAncestor.objects.filter(
            category__ingredients__in=user_ing_ids, depth__lte=closure_depth
        ).values_list("ancestor_id", flat=True)
    )

    all_satisfiable_categories = set(
        IngredientCategoryAncestor.objects.filter(
            ancestor__in=user_ancestor_categories
        ).values_list("category_id", flat=True)
    )

    category_match_ids = set(
        Ingredient.objects.filter(categories__in=all_satisfiable_categories).values_list(
            "id", flat=True
        )
    )

    # Category matches are those not in exact matches
    category_match_ids -= user_ing_ids

    return user_ing_ids, category_match_ids


@login_required
def available_recipes(request):
    """Display recipes user can make with their inventory."""
    from inventory.services import get_makeable_recipes

    max_depth = int(request.GET.get("depth", 1))
    max_depth = max(0, min(3, max_depth))  # Clamp to 0-3

    recipes = get_makeable_recipes(request.user, max_depth=max_depth).prefetch_related(
        "recipe_ingredients__ingredient"
    )

    # Get match sets for color-coding
    exact_match_ids, category_match_ids = _get_ingredient_match_sets(
        request.user, max_depth
    )

    # HTMX partial response
    if request.headers.get("HX-Request"):
        return render(
            request,
            "recipes/partials/available_results.html",
            {
                "recipes": recipes,
                "exact_match_ids": exact_match_ids,
                "category_match_ids": category_match_ids,
            },
        )

    return render(
        request,
        "recipes/available.html",
        {
            "recipes": recipes,
            "max_depth": max_depth,
            "exact_match_ids": exact_match_ids,
            "category_match_ids": category_match_ids,
        },
    )
