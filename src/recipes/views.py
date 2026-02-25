"""Frontend views for recipes."""

from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Max
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from ingredients.models import IngredientCategory, IngredientCategoryAncestor
from inventory.models import UserInventory
from inventory.services import get_ingredient_match_sets

from .models import Recipe, TasteTag


@login_required
def recipe_list(request):
    """Display all recipes with search functionality."""
    search = request.GET.get("q", "")
    cat = request.GET.get("cat", "")
    tags = request.GET.getlist("tags")

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

    if tags:
        recipes = recipes.filter(taste_tags__slug__in=tags).distinct()

    all_tags = TasteTag.objects.all()
    selected_tags = set(tags)

    context = {
        "recipes": recipes,
        "search": search,
        "cat": cat,
        "categories": categories,
        "selected_category": selected_cat,
        "all_tags": all_tags,
        "selected_tags": selected_tags,
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



@login_required
def chat_page(request):
    """Dedicated natural language recommendation chat page."""
    has_inventory = UserInventory.objects.filter(user=request.user, in_stock=True).exists()
    return render(request, "recipes/chat.html", {"has_inventory": has_inventory})


@login_required
@require_POST
def chat_message(request):
    """Handle a chat message and return a rendered conversation turn."""
    from .services.chat import handle_chat_message

    user_message = request.POST.get("message", "").strip()[:500]
    if not user_message:
        return render(request, "recipes/partials/chat_response.html", {
            "user_message": "",
            "response": None,
            "recommendations": [],
        })

    try:
        depth = int(request.POST.get("depth", 1))
    except (ValueError, TypeError):
        depth = 1
    depth = max(1, min(2, depth))

    response = handle_chat_message(request.user, request.session, user_message, depth)

    slugs = [r["slug"] for r in response.recommendations]
    recipes_by_slug = {
        r.slug: r
        for r in Recipe.objects.filter(slug__in=slugs).prefetch_related(
            "taste_tags", "recipe_ingredients__ingredient"
        )
    }

    recommendations_with_recipes = [
        {"recipe": recipes_by_slug[r["slug"]], "blurb": r["blurb"]}
        for r in response.recommendations
        if r["slug"] in recipes_by_slug
    ]

    return render(request, "recipes/partials/chat_response.html", {
        "user_message": user_message,
        "response": response,
        "recommendations": recommendations_with_recipes,
    })


@login_required
@require_POST
def chat_clear(request):
    """Clear the chat history from the session."""
    request.session["recipe_chat_history"] = []
    return render(request, "recipes/partials/chat_panel_empty.html")


@login_required
def available_recipes(request):
    """Display recipes user can make with their inventory."""
    from inventory.services import get_makeable_recipes

    max_depth = int(request.GET.get("depth", 1))
    max_depth = max(0, min(3, max_depth))  # Clamp to 0-3
    tags = request.GET.getlist("tags")

    recipes = get_makeable_recipes(request.user, max_depth=max_depth).prefetch_related(
        "recipe_ingredients__ingredient"
    )

    if tags:
        recipes = recipes.filter(taste_tags__slug__in=tags).distinct()

    # Get match sets for color-coding
    exact_match_ids, category_match_ids = get_ingredient_match_sets(
        request.user, max_depth
    )

    all_tags = TasteTag.objects.all()
    selected_tags = set(tags)

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

    has_inventory = UserInventory.objects.filter(user=request.user, in_stock=True).exists()

    return render(
        request,
        "recipes/available.html",
        {
            "recipes": recipes,
            "max_depth": max_depth,
            "exact_match_ids": exact_match_ids,
            "category_match_ids": category_match_ids,
            "all_tags": all_tags,
            "selected_tags": selected_tags,
            "has_inventory": has_inventory,
        },
    )
