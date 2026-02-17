import json
import logging

from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.urls import path
from django.utils import timezone
from django.utils.html import escape, format_html, mark_safe

from inventory.services import get_makeable_recipes, get_user_inventory_stats

from .models import Recipe, RecipeImport, RecipeIngredient
from .services.image_parser import ParseError, parse_recipe_image
from .services.import_processor import (
    approve_import,
    find_matching_recipe,
    reject_import,
)

logger = logging.getLogger(__name__)


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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "available/",
                self.admin_site.admin_view(self.available_recipes_view),
                name="recipes_recipe_available",
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        """Add available recipes button to changelist."""
        extra_context = extra_context or {}
        extra_context["show_available_recipes_button"] = True
        return super().changelist_view(request, extra_context)

    def available_recipes_view(self, request):
        """Show recipes user can make with their inventory."""
        # Get depth parameter (default 1 = same category)
        try:
            depth = int(request.GET.get("depth", 1))
            depth = max(0, min(depth, 5))  # Clamp to 0-5
        except (ValueError, TypeError):
            depth = 1

        # Get makeable recipes
        recipes = get_makeable_recipes(request.user, max_depth=depth)

        # Get inventory stats
        inventory_stats = get_user_inventory_stats(request.user)

        # Depth options for UI
        depth_options = [
            (0, "Exact match only"),
            (1, "Same category (default)"),
            (2, "Parent category"),
            (3, "Grandparent category"),
        ]

        context = {
            **self.admin_site.each_context(request),
            "recipes": recipes,
            "recipe_count": recipes.count(),
            "depth": depth,
            "depth_options": depth_options,
            "inventory_stats": inventory_stats,
            "title": "Available Recipes",
            "opts": self.model._meta,
        }
        return render(
            request, "admin/recipes/recipe/available_recipes.html", context
        )

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


class MultipleFileInput(forms.ClearableFileInput):
    """Widget that allows multiple file selection."""

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Form field for multiple file uploads."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(d, initial) for d in data]
        return [single_file_clean(data, initial)]


class RecipeImportUploadForm(forms.Form):
    """Form for bulk uploading recipe images."""

    images = MultipleFileField(
        label="Recipe Images",
        help_text="Select one or more images of recipe pages to parse.",
    )
    source = forms.CharField(
        max_length=200,
        required=False,
        help_text="Source name (e.g., 'Death & Co') - applied to all imported recipes.",
    )


@admin.register(RecipeImport)
class RecipeImportAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "get_recipe_names",
        "status",
        "get_existing_match",
        "created_at",
        "approved_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["parsed_data"]
    readonly_fields = [
        "status",
        "parse_error",
        "raw_ocr_text_display",
        "matching_log_display",
        "parsed_data_display",
        "recipe",
        "created_at",
        "processed_at",
        "approved_at",
        "image_preview",
    ]
    ordering = ["-created_at"]
    actions = ["approve_selected", "reject_selected", "reparse_selected"]

    fieldsets = [
        (None, {"fields": ["source_image", "image_preview", "status"]}),
        (
            "OCR Text",
            {
                "fields": ["raw_ocr_text_display"],
                "classes": ["wide", "collapse"],
            },
        ),
        (
            "Ingredient Matching",
            {
                "fields": ["matching_log_display"],
                "classes": ["wide"],
            },
        ),
        (
            "Parsed Data",
            {
                "fields": ["parsed_data_display", "parse_error"],
                "classes": ["wide"],
            },
        ),
        (
            "Result",
            {
                "fields": ["recipe", "created_at", "processed_at", "approved_at"],
            },
        ),
    ]

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom_urls = [
            path(
                "upload/",
                self.admin_site.admin_view(self.upload_view),
                name="recipes_recipeimport_upload",
            ),
        ]
        return custom_urls + urls

    def upload_view(self, request):
        """Handle bulk image upload."""
        from django.shortcuts import redirect, render

        if request.method == "POST":
            form = RecipeImportUploadForm(request.POST, request.FILES)
            if form.is_valid():
                files = request.FILES.getlist("images")
                created_count = 0

                for uploaded_file in files:
                    # Create RecipeImport for each file
                    recipe_import = RecipeImport.objects.create(
                        source_image=uploaded_file,
                        status=RecipeImport.Status.PENDING,
                    )
                    created_count += 1

                    # Attempt to parse immediately
                    self._parse_import(recipe_import)

                msg = f"Uploaded {created_count} images. Check list for results."
                messages.success(request, msg)
                return redirect("admin:recipes_recipeimport_changelist")
        else:
            form = RecipeImportUploadForm()

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Upload Recipe Images",
            "opts": self.model._meta,
        }
        return render(request, "admin/recipes/recipeimport/upload.html", context)

    def _parse_import(self, recipe_import: RecipeImport) -> None:
        """Parse a single import using Ollama (two-step: OCR then parse)."""
        try:
            with recipe_import.source_image.open("rb") as f:
                image_bytes = f.read()
            raw_text, parsed = parse_recipe_image(image_bytes)
            recipe_import.raw_ocr_text = raw_text
            recipe_import.parsed_data = parsed
            recipe_import.status = RecipeImport.Status.PARSED
            recipe_import.processed_at = timezone.now()
            recipe_import.save()
        except ParseError as e:
            recipe_import.status = RecipeImport.Status.ERROR
            recipe_import.parse_error = str(e)
            recipe_import.processed_at = timezone.now()
            recipe_import.save()
            logger.error(f"Parse error for import {recipe_import.pk}: {e}")
        except Exception as e:
            recipe_import.status = RecipeImport.Status.ERROR
            recipe_import.parse_error = f"Unexpected error: {e}"
            recipe_import.processed_at = timezone.now()
            recipe_import.save()
            logger.exception(f"Unexpected error parsing import {recipe_import.pk}")

    def changelist_view(self, request, extra_context=None):
        """Add upload button to changelist."""
        extra_context = extra_context or {}
        extra_context["show_upload_button"] = True
        return super().changelist_view(request, extra_context)

    @admin.display(description="Recipes")
    def get_recipe_names(self, obj):
        """Show names of parsed recipes."""
        if not obj.parsed_data or "recipes" not in obj.parsed_data:
            return "-"
        names = [r.get("name") or "?" for r in obj.parsed_data["recipes"][:3]]
        result = ", ".join(names)
        if len(obj.parsed_data["recipes"]) > 3:
            result += f" (+{len(obj.parsed_data['recipes']) - 3} more)"
        return result

    @admin.display(description="Existing Match")
    def get_existing_match(self, obj):
        """Show if any parsed recipe matches an existing one."""
        if not obj.parsed_data or "recipes" not in obj.parsed_data:
            return "-"
        matches = []
        for recipe_data in obj.parsed_data["recipes"]:
            name = recipe_data.get("name", "")
            if name and find_matching_recipe(name):
                matches.append(name)
        if matches:
            return format_html(
                '<span style="color: orange;">⚠️ {}</span>',
                ", ".join(matches),
            )
        return "-"

    @admin.display(description="Image Preview")
    def image_preview(self, obj):
        """Show thumbnail of uploaded image."""
        if obj.source_image:
            return format_html(
                '<img src="{}" style="max-width: 400px; max-height: 400px;" />',
                obj.source_image.url,
            )
        return "-"

    @admin.display(description="Raw OCR Text")
    def raw_ocr_text_display(self, obj):
        """Show raw OCR text extracted from image."""
        if not obj.raw_ocr_text:
            return "-"
        return format_html(
            '<pre style="white-space: pre-wrap;">{}</pre>', obj.raw_ocr_text
        )

    @admin.display(description="Ingredient Matching Log")
    def matching_log_display(self, obj):
        """Show ingredient matching decisions in a readable format."""
        if not obj.parsed_data or "matching_log" not in obj.parsed_data:
            return "-"

        log = obj.parsed_data["matching_log"]
        if not log:
            return "No ingredients processed"

        # Build HTML table - escape user data
        rows = []
        for entry in log:
            status = entry.get("status", "unknown")
            original = escape(entry.get("original", "?"))
            matched_to = escape(entry.get("matched_to") or "")
            similarity = entry.get("similarity")
            recipe = escape(entry.get("recipe", "?"))

            # Get candidates checked info
            candidates = entry.get("candidates_checked", [])
            candidates_info = ""
            if candidates:
                cand_list = ", ".join(
                    f"{c['name']} ({c['similarity']:.0%})"
                    for c in candidates[:3]
                )
                candidates_info = f" [checked: {cand_list}]"

            # Status styling
            if status == "exact_match":
                status_html = '<span style="color: green;">✓ Exact</span>'
                result = matched_to or original
            elif status == "fuzzy_matched":
                pct = f"{similarity:.0%}" if similarity else "?"
                status_html = f'<span style="color: orange;">↔ Fuzzy ({pct})</span>'
                result = f"{original} → <strong>{matched_to}</strong>"
            else:  # no_match
                status_html = '<span style="color: gray;">+ New</span>'
                result = f"{original}{candidates_info}"

            rows.append(
                f"<tr><td>{recipe}</td><td>{status_html}</td><td>{result}</td></tr>"
            )

        th_style = "text-align:left;padding:4px;border-bottom:1px solid #ccc;"
        table = (
            '<table style="border-collapse:collapse;width:100%;">'
            "<thead><tr>"
            f'<th style="{th_style}">Recipe</th>'
            f'<th style="{th_style}">Status</th>'
            f'<th style="{th_style}">Ingredient</th>'
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

        # Summary
        exact = sum(1 for e in log if e.get("status") == "exact_match")
        fuzzy = sum(1 for e in log if e.get("status") == "fuzzy_matched")
        new = sum(1 for e in log if e.get("status") == "no_match")
        summary = (
            f"<p><strong>Summary:</strong> {exact} exact matches, "
            f"{fuzzy} fuzzy matches, {new} new ingredients</p>"
        )

        return mark_safe(summary + table)

    @admin.display(description="Parsed Data")
    def parsed_data_display(self, obj):
        """Show formatted JSON of parsed data."""
        if not obj.parsed_data:
            return "-"
        formatted = json.dumps(obj.parsed_data, indent=2)
        return format_html("<pre>{}</pre>", formatted)

    @admin.action(description="Approve selected imports")
    def approve_selected(self, request, queryset):
        """Approve selected imports and create recipes."""
        approved = 0
        errors = []
        for recipe_import in queryset.filter(status=RecipeImport.Status.PARSED):
            try:
                # Approve all recipes in the import
                recipes_data = recipe_import.parsed_data.get("recipes", [])
                for i in range(len(recipes_data)):
                    approve_import(recipe_import, recipe_index=i)
                approved += 1
            except Exception as e:
                errors.append(f"Import {recipe_import.pk}: {e}")

        if approved:
            messages.success(request, f"Approved {approved} imports.")
        if errors:
            messages.error(request, f"Errors: {'; '.join(errors)}")

    @admin.action(description="Reject selected imports")
    def reject_selected(self, request, queryset):
        """Reject selected imports."""
        count = 0
        for recipe_import in queryset.exclude(status=RecipeImport.Status.APPROVED):
            reject_import(recipe_import)
            count += 1
        messages.success(request, f"Rejected {count} imports.")

    @admin.action(description="Re-parse selected imports")
    def reparse_selected(self, request, queryset):
        """Re-parse selected imports with Ollama."""
        count = 0
        for recipe_import in queryset:
            self._parse_import(recipe_import)
            count += 1
        messages.success(request, f"Re-parsed {count} imports.")
