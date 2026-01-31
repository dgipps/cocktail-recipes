from django.contrib import admin
from django.utils.html import format_html_join, mark_safe

from .models import Ingredient, IngredientCategory, IngredientCategoryAncestor


@admin.register(IngredientCategory)
class IngredientCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "get_parent", "get_depth", "get_ingredient_count"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["name"]
    readonly_fields = [
        "ancestor_hierarchy_display",
        "child_categories_display",
        "ingredients_display",
    ]

    fieldsets = [
        (None, {"fields": ["name", "slug", "description"]}),
        (
            "Hierarchy (read-only)",
            {
                "fields": ["ancestor_hierarchy_display", "child_categories_display"],
                "description": "Parent and child categories in the hierarchy",
            },
        ),
        (
            "Ingredients (read-only)",
            {
                "fields": ["ingredients_display"],
                "description": "Ingredients directly assigned to this category",
            },
        ),
    ]

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
        max_ancestor = obj.ancestor_links.order_by("-depth").first()
        return max_ancestor.depth if max_ancestor else 0

    get_depth.short_description = "Depth"

    def get_ingredient_count(self, obj):
        """Count ingredients in this category and all subcategories."""
        descendant_categories = obj.get_descendants(include_self=True)
        return (
            Ingredient.objects.filter(categories__in=descendant_categories)
            .distinct()
            .count()
        )

    get_ingredient_count.short_description = "Ingredients"

    @admin.display(description="Parent Categories")
    def ancestor_hierarchy_display(self, obj):
        """Show ancestor chain: 'London Dry → Gin → Spirits' with links."""
        if not obj.pk:
            return "Save first to see hierarchy"

        ancestors = list(obj.get_ancestors(include_self=True))
        if not ancestors:
            return "No ancestors (top-level category)"

        return format_html_join(
            " → ",
            '<a href="/admin/ingredients/ingredientcategory/{}/change/">{}</a>',
            ((c.pk, c.name) for c in ancestors),
        )

    @admin.display(description="Child Categories")
    def child_categories_display(self, obj):
        """Show direct children of this category."""
        if not obj.pk:
            return "Save first to see children"

        # Get direct children (depth=1 from this category)
        children = IngredientCategory.objects.filter(
            ancestor_links__ancestor=obj,
            ancestor_links__depth=1,
        ).order_by("name")

        if not children:
            return "No child categories"

        return format_html_join(
            ", ",
            '<a href="/admin/ingredients/ingredientcategory/{}/change/">{}</a>',
            ((child.pk, child.name) for child in children),
        )

    @admin.display(description="Ingredients in this Category")
    def ingredients_display(self, obj):
        """Show all ingredients in this category or any subcategory as a table."""
        if not obj.pk:
            return "Save first to see ingredients"

        # Get all descendant categories (including self)
        descendant_categories = obj.get_descendants(include_self=True)

        # Get all ingredients in any of these categories
        ingredients = (
            Ingredient.objects.filter(categories__in=descendant_categories)
            .prefetch_related("categories")
            .distinct()
            .order_by("name")
        )
        total = ingredients.count()
        ingredients = ingredients[:50]

        if not ingredients:
            return "No ingredients in this category or subcategories"

        # Build HTML table
        rows = format_html_join(
            "",
            "<tr><td><a href='/admin/ingredients/ingredient/{}/change/'>{}</a></td>"
            "<td>{}</td></tr>",
            (
                (ing.pk, ing.name, ", ".join(c.name for c in ing.categories.all()))
                for ing in ingredients
            ),
        )

        table = mark_safe(
            f"<table style='width:100%'>"
            f"<thead><tr><th style='text-align:left'>Ingredient</th>"
            f"<th style='text-align:left'>Category</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

        if total > 50:
            return mark_safe(f"{table}<p>... and {total - 50} more</p>")

        return table


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
    readonly_fields = ["category_hierarchy_display"]

    fieldsets = [
        (None, {"fields": ["name", "slug", "description"]}),
        ("Categories", {"fields": ["categories"]}),
        (
            "Category Hierarchy (read-only)",
            {
                "fields": ["category_hierarchy_display"],
                "description": "Shows the full hierarchy for each assigned category",
            },
        ),
    ]

    def get_categories(self, obj):
        return ", ".join(c.name for c in obj.categories.all()[:3])

    get_categories.short_description = "Categories"

    @admin.display(description="Hierarchy")
    def category_hierarchy_display(self, obj):
        """Show category hierarchies as 'London Dry -> Gin -> Spirits' with links."""
        if not obj.pk:
            return "Save the ingredient first to see hierarchy"

        hierarchies = []
        for category in obj.categories.all():
            ancestors = list(category.get_ancestors(include_self=True))
            chain = format_html_join(
                " → ",
                '<a href="/admin/ingredients/ingredientcategory/{}/change/">{}</a>',
                ((c.pk, c.name) for c in ancestors),
            )
            hierarchies.append(chain)

        if not hierarchies:
            return "No categories assigned"

        return mark_safe("<br>".join(str(h) for h in hierarchies))
