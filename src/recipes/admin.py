from django.contrib import admin

from .models import Recipe, RecipeIngredient


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1
    autocomplete_fields = ["ingredient"]
    fields = ["ingredient", "amount", "unit", "order", "optional", "notes"]
    ordering = ["order"]


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ["name", "source", "page", "get_ingredient_count", "updated_at"]
    list_filter = ["source"]
    search_fields = ["name", "source"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [RecipeIngredientInline]
    ordering = ["name"]

    fieldsets = [
        (None, {"fields": ["name", "slug"]}),
        ("Source", {"fields": ["source", "page"]}),
        ("Instructions", {"fields": ["method", "garnish", "notes"]}),
    ]

    def get_ingredient_count(self, obj):
        return obj.recipe_ingredients.count()

    get_ingredient_count.short_description = "Ingredients"


@admin.register(RecipeIngredient)
class RecipeIngredientAdmin(admin.ModelAdmin):
    list_display = [
        "recipe",
        "ingredient",
        "display_amount_formatted",
        "unit",
        "order",
        "optional",
    ]
    list_filter = ["optional", "unit", "recipe__source"]
    search_fields = ["recipe__name", "ingredient__name"]
    autocomplete_fields = ["recipe", "ingredient"]
    ordering = ["recipe__name", "order"]

    @admin.display(description="Amount")
    def display_amount_formatted(self, obj):
        """Show amount as bartender-friendly fraction."""
        return obj.display_amount()
