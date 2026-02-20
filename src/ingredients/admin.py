from django.contrib import admin, messages
from django.contrib.postgres.search import TrigramSimilarity
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path
from django.utils.html import format_html, format_html_join, mark_safe
from django.utils.text import slugify

from inventory.models import UserInventory

from .models import (
    Ingredient,
    IngredientCategory,
    IngredientCategoryAncestor,
    IngredientCategorySuggestion,
)


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
    list_display = ["name", "slug", "get_categories", "needs_categorization"]
    list_filter = ["needs_categorization", "categories"]
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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "manage-inventory/",
                self.admin_site.admin_view(self.manage_inventory_view),
                name="ingredients_ingredient_manage_inventory",
            ),
            path(
                "add-similar/",
                self.admin_site.admin_view(self.add_similar_view),
                name="ingredients_ingredient_add_similar",
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        """Add manage inventory button to changelist."""
        extra_context = extra_context or {}
        extra_context["show_manage_inventory_button"] = True
        return super().changelist_view(request, extra_context)

    def manage_inventory_view(self, request):
        """List all ingredients with inventory toggle for current user."""
        if request.method == "POST":
            ingredient_id = request.POST.get("ingredient_id")
            action = request.POST.get("action")  # "add" or "remove"

            if ingredient_id and action:
                inventory, created = UserInventory.objects.get_or_create(
                    user=request.user,
                    ingredient_id=ingredient_id,
                    defaults={"in_stock": action == "add"},
                )
                if not created:
                    inventory.in_stock = action == "add"
                    inventory.save()

                # If AJAX request, return simple response
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return JsonResponse({
                        "status": "ok",
                        "in_stock": inventory.in_stock,
                    })

                return redirect("admin:ingredients_ingredient_manage_inventory")

        # Get filter parameters
        category_id = request.GET.get("category")
        search_query = request.GET.get("q", "")
        show_only = request.GET.get("show", "all")  # all, in_stock, out_of_stock

        # Get all ingredients
        ingredients = Ingredient.objects.all().prefetch_related("categories")

        if category_id:
            category = IngredientCategory.objects.filter(pk=category_id).first()
            if category:
                descendant_categories = category.get_descendants(include_self=True)
                ingredients = ingredients.filter(categories__in=descendant_categories)

        if search_query:
            ingredients = ingredients.filter(name__icontains=search_query)

        ingredients = ingredients.distinct().order_by("name")

        # Get user's inventory status
        user_inventory = set(
            UserInventory.objects.filter(
                user=request.user, in_stock=True
            ).values_list("ingredient_id", flat=True)
        )

        # Filter by stock status if requested
        if show_only == "in_stock":
            ingredients = ingredients.filter(id__in=user_inventory)
        elif show_only == "out_of_stock":
            ingredients = ingredients.exclude(id__in=user_inventory)

        # Get categories for filter dropdown
        categories = IngredientCategory.objects.all().order_by("name")

        context = {
            **self.admin_site.each_context(request),
            "ingredients": ingredients,
            "user_inventory": user_inventory,
            "categories": categories,
            "selected_category": category_id,
            "search_query": search_query,
            "show_only": show_only,
            "in_stock_count": len(user_inventory),
            "total_count": Ingredient.objects.count(),
            "title": "Manage My Inventory",
            "opts": self.model._meta,
        }
        return render(
            request, "admin/ingredients/ingredient/manage_inventory.html", context
        )

    @staticmethod
    def _unique_slug(name):
        base = slugify(name)
        slug = base
        n = 2
        while Ingredient.objects.filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug

    def add_similar_view(self, request):
        """Add a new ingredient similar to an existing one (same categories)."""
        source = get_object_or_404(Ingredient, pk=request.GET.get("source_id") or request.POST.get("source_id"))

        # HTMX live search
        if request.method == "GET" and request.headers.get("HX-Request"):
            name = request.GET.get("name", "").strip()
            results = []
            if name:
                results = (
                    Ingredient.objects.annotate(similarity=TrigramSimilarity("name", name))
                    .filter(similarity__gte=0.15)
                    .prefetch_related("categories")
                    .order_by("-similarity")[:8]
                )
            return render(
                request,
                "admin/ingredients/ingredient/partials/similar_search_results.html",
                {"results": results, "name": name, "source": source},
            )

        if request.method == "POST":
            action = request.POST.get("action")

            if action == "add_existing":
                ingredient_id = request.POST.get("ingredient_id")
                UserInventory.objects.update_or_create(
                    user=request.user,
                    ingredient_id=ingredient_id,
                    defaults={"in_stock": True},
                )
                messages.success(request, "Ingredient added to your inventory.")
                return redirect("admin:ingredients_ingredient_manage_inventory")

            if action == "create":
                name = request.POST.get("name", "").strip()
                if not name:
                    messages.error(request, "Please enter a name for the new ingredient.")
                else:
                    slug = self._unique_slug(name)
                    new_ing = Ingredient.objects.create(
                        name=name,
                        slug=slug,
                        needs_categorization=False,
                    )
                    new_ing.categories.set(source.categories.all())
                    UserInventory.objects.get_or_create(
                        user=request.user,
                        ingredient=new_ing,
                        defaults={"in_stock": True},
                    )
                    messages.success(request, f'Created "{name}" and added it to your inventory.')
                    return redirect("admin:ingredients_ingredient_manage_inventory")

        context = {
            **self.admin_site.each_context(request),
            "source": source,
            "title": "Add Similar Ingredient",
            "opts": self.model._meta,
        }
        return render(
            request,
            "admin/ingredients/ingredient/add_similar.html",
            context,
        )

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

    actions = ["trigger_categorization"]

    @admin.action(description="Trigger LLM categorization for selected")
    def trigger_categorization(self, request, queryset):
        """Queue selected ingredients for LLM categorization."""
        from .services import CategorizationError, categorize_ingredient

        categorized = 0
        errors = []

        for ingredient in queryset:
            try:
                suggestion = categorize_ingredient(ingredient)
                if suggestion:
                    categorized += 1
            except CategorizationError as e:
                errors.append(f"{ingredient.name}: {e}")

        if categorized:
            msg = f"Created suggestions for {categorized} ingredients."
            messages.success(request, msg)
        if errors:
            messages.error(request, f"Errors: {'; '.join(errors[:3])}")
        if not categorized and not errors:
            msg = "No suggestions created (low confidence or already suggested)."
            messages.info(request, msg)


@admin.register(IngredientCategorySuggestion)
class IngredientCategorySuggestionAdmin(admin.ModelAdmin):
    list_display = [
        "ingredient",
        "suggested_category",
        "category_hierarchy_display",
        "confidence_display",
        "status",
        "created_at",
    ]
    list_filter = ["status", "created_at", "suggested_category"]
    search_fields = ["ingredient__name", "suggested_category__name"]
    readonly_fields = [
        "ingredient",
        "suggested_category",
        "confidence",
        "reasoning",
        "created_at",
        "reviewed_at",
        "reviewed_by",
        "status",  # Make status read-only - use actions instead
    ]
    ordering = ["-created_at"]
    actions = ["approve_selected", "reject_selected"]

    fieldsets = [
        (None, {"fields": ["ingredient", "suggested_category", "status"]}),
        ("LLM Analysis", {"fields": ["confidence", "reasoning"]}),
        ("Review", {"fields": ["created_at", "reviewed_at", "reviewed_by"]}),
    ]

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        """Show confidence as percentage with color coding."""
        pct = obj.confidence * 100
        if pct >= 80:
            color = "green"
        elif pct >= 50:
            color = "orange"
        else:
            color = "red"
        pct_str = f"{pct:.0f}%"
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            pct_str,
        )

    @admin.display(description="Category Path")
    def category_hierarchy_display(self, obj):
        """Show full category path like 'London Dry -> Gin -> Spirits'."""
        ancestors = list(obj.suggested_category.get_ancestors(include_self=True))
        return " → ".join(c.name for c in ancestors)

    @admin.action(description="Approve selected suggestions")
    def approve_selected(self, request, queryset):
        """Approve selected suggestions and apply categories."""
        approved = 0
        pending = queryset.filter(status=IngredientCategorySuggestion.Status.PENDING)
        for suggestion in pending:
            suggestion.approve(user=request.user)
            approved += 1
        if approved:
            messages.success(request, f"Approved {approved} suggestions.")
        else:
            messages.info(request, "No pending suggestions to approve.")

    @admin.action(description="Reject selected suggestions")
    def reject_selected(self, request, queryset):
        """Reject selected suggestions."""
        rejected = 0
        pending = queryset.filter(status=IngredientCategorySuggestion.Status.PENDING)
        for suggestion in pending:
            suggestion.reject(user=request.user)
            rejected += 1
        if rejected:
            messages.success(request, f"Rejected {rejected} suggestions.")
        else:
            messages.info(request, "No pending suggestions to reject.")
