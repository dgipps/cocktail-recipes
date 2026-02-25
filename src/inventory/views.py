# Views for inventory app

from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from ingredients.models import Ingredient, IngredientCategory, IngredientCategoryAncestor

from .models import UserInventory


@login_required
def inventory_page(request):
    """Display all ingredients with the user's inventory status."""
    q = request.GET.get("q", "")
    cat = request.GET.get("cat", "")
    show = request.GET.get("show", "all")

    if q:
        ingredients = (
            Ingredient.objects
            .prefetch_related("categories")
            .annotate(similarity=TrigramSimilarity("name", q))
            .filter(similarity__gte=0.2)
            .order_by("-similarity", "name")
        )
    else:
        ingredients = Ingredient.objects.prefetch_related("categories").order_by("name")

    if cat:
        selected_cat = IngredientCategory.objects.filter(pk=cat).first()
        if selected_cat:
            cat_ids = IngredientCategoryAncestor.objects.filter(
                ancestor=selected_cat
            ).values_list("category_id", flat=True)
            ingredients = ingredients.filter(categories__in=cat_ids).distinct()

    # Annotate each ingredient with the user's in_stock status (single query)
    ingredients = ingredients.annotate(
        in_stock=Exists(
            UserInventory.objects.filter(
                user=request.user,
                ingredient=OuterRef("pk"),
                in_stock=True,
            )
        )
    )

    if show == "in_stock":
        ingredients = ingredients.filter(in_stock=True)

    if request.headers.get("HX-Request"):
        return render(request, "inventory/partials/ingredient_list.html", {
            "ingredients": ingredients,
        })

    in_stock_count = UserInventory.objects.filter(user=request.user, in_stock=True).count()

    return render(request, "inventory/inventory.html", {
        "ingredients": ingredients,
        "categories": IngredientCategory.objects.order_by("name"),
        "in_stock_count": in_stock_count,
        "q": q,
        "cat": cat,
        "show": show,
    })


@login_required
@require_POST
def toggle_ingredient(request):
    """Toggle an ingredient's in_stock status for the current user."""
    ingredient_id = request.POST.get("ingredient_id")
    ingredient = get_object_or_404(Ingredient, pk=ingredient_id)

    obj, created = UserInventory.objects.get_or_create(
        user=request.user,
        ingredient=ingredient,
        defaults={"in_stock": True},
    )
    if not created:
        obj.in_stock = not obj.in_stock
        obj.save(update_fields=["in_stock"])

    return render(request, "inventory/partials/ingredient_toggle.html", {
        "ingredient": ingredient,
        "in_stock": obj.in_stock,
    })
