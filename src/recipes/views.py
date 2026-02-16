"""Frontend views for recipes."""

from django.shortcuts import get_object_or_404, render

from .models import Recipe


def recipe_list(request):
    """Display all recipes with search functionality."""
    recipes = Recipe.objects.prefetch_related(
        "recipe_ingredients__ingredient"
    ).order_by("name")

    search = request.GET.get("q", "")
    if search:
        recipes = recipes.filter(name__icontains=search)

    # HTMX partial response for search
    if request.headers.get("HX-Request"):
        return render(
            request,
            "recipes/partials/recipe_results.html",
            {"recipes": recipes},
        )

    return render(
        request,
        "recipes/recipe_list.html",
        {"recipes": recipes, "search": search},
    )


def recipe_detail(request, slug):
    """Display a single recipe with full details."""
    recipe = get_object_or_404(
        Recipe.objects.prefetch_related("recipe_ingredients__ingredient"),
        slug=slug,
    )
    return render(request, "recipes/recipe_detail.html", {"recipe": recipe})
