from django.contrib import admin

from .models import Ingredient, IngredientCategory, IngredientCategoryAncestor


@admin.register(IngredientCategory)
class IngredientCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "get_parent", "get_depth"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["name"]

    def get_parent(self, obj):
        """Get the direct parent (depth=1) of this category."""
        parent_link = obj.ancestor_links.filter(depth=1).first()
        return parent_link.ancestor if parent_link else None

    get_parent.short_description = "Parent"

    def get_depth(self, obj):
        """Get the depth of this category in the hierarchy."""
        self_link = obj.ancestor_links.filter(depth=0).first()
        if not self_link:
            return 0
        # Depth is the max depth in ancestor_links
        max_ancestor = obj.ancestor_links.order_by("-depth").first()
        return max_ancestor.depth if max_ancestor else 0

    get_depth.short_description = "Depth"


@admin.register(IngredientCategoryAncestor)
class IngredientCategoryAncestorAdmin(admin.ModelAdmin):
    list_display = ["category", "ancestor", "depth"]
    list_filter = ["depth"]
    search_fields = ["category__name", "ancestor__name"]
    ordering = ["category__name", "depth"]


@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "get_categories"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ["categories"]
    ordering = ["name"]

    def get_categories(self, obj):
        return ", ".join(c.name for c in obj.categories.all()[:3])

    get_categories.short_description = "Categories"
