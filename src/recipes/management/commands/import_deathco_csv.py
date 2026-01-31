"""
One-off import of Death & Co recipe index CSV.

Usage:
    python manage.py import_deathco_csv /path/to/csv

Note: This CSV is an index only - it does not contain amounts.
RecipeIngredient.amount will be null for all imported records.
"""

import contextlib
import csv
import re
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from ingredients.models import (
    Ingredient,
    IngredientCategory,
    IngredientCategoryAncestor,
)
from recipes.models import Recipe, RecipeIngredient


class Command(BaseCommand):
    help = "Import Death & Co recipe index from CSV"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            type=str,
            help="Path to the Death & Co CSV file",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report without saving to database",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        dry_run = options["dry_run"]

        # Parse CSV into recipe groups
        recipes_data = self.parse_csv(csv_path)

        self.stdout.write(f"Parsed {len(recipes_data)} recipes from CSV")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run - no changes saved"))
            self.print_summary(recipes_data)
            return

        # Import to database
        with transaction.atomic():
            stats = self.import_recipes(recipes_data)

        self.stdout.write(self.style.SUCCESS("Import complete!"))
        self.stdout.write(f"  Recipes created: {stats['recipes_created']}")
        self.stdout.write(f"  Categories created: {stats['categories_created']}")
        self.stdout.write(f"  Ingredients created: {stats['ingredients_created']}")
        self.stdout.write(f"  Recipe-ingredient links: {stats['links_created']}")

    def parse_csv(self, csv_path: Path) -> dict:
        """
        Parse CSV into grouped recipe data.

        Returns dict keyed by recipe name with:
        - page: int
        - method_parts: list of strings
        - garnish_parts: list of strings
        - ingredients: list of dicts with name, category, order
        """
        recipes = defaultdict(lambda: {
            "page": None,
            "method_parts": [],
            "garnish_parts": [],
            "ingredients": [],
        })

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            current_recipe = None
            order = 0

            for row in reader:
                if len(row) < 6:
                    continue

                # CSV columns: _, name, ingredient, _, category, page, method, garnish
                recipe_name = row[1].strip() if len(row) > 1 else ""
                ingredient_name = row[2].strip() if len(row) > 2 else ""
                category = row[4].strip() if len(row) > 4 else ""
                page = row[5].strip() if len(row) > 5 else ""
                method = row[6].strip() if len(row) > 6 else ""
                garnish = row[7].strip() if len(row) > 7 else ""

                # Skip empty rows
                if not recipe_name and not ingredient_name:
                    continue

                # Skip section headers (recipe name but no ingredient)
                if recipe_name and not ingredient_name:
                    continue

                # Track recipe changes for ordering
                if recipe_name and recipe_name != current_recipe:
                    current_recipe = recipe_name
                    order = 0

                # Add ingredient to recipe
                if ingredient_name and current_recipe:
                    recipes[current_recipe]["ingredients"].append({
                        "name": ingredient_name,
                        "category": category,
                        "order": order,
                    })
                    order += 1

                    # Set page (first occurrence wins)
                    if page and recipes[current_recipe]["page"] is None:
                        with contextlib.suppress(ValueError):
                            recipes[current_recipe]["page"] = int(page)

                # Collect method and garnish parts
                if method and current_recipe:
                    recipes[current_recipe]["method_parts"].append(method)
                if garnish and current_recipe:
                    # Garnish often appears in the first row
                    if garnish.startswith("GARNISH:"):
                        recipes[current_recipe]["garnish_parts"].insert(0, garnish)
                    else:
                        recipes[current_recipe]["garnish_parts"].append(garnish)

        return dict(recipes)

    def parse_category(self, cat_str: str) -> tuple[str, str | None]:
        """
        Parse category string into (parent, child).

        Examples:
            "GIN (LONDON DRY)" -> ("GIN", "LONDON DRY")
            "SODA" -> ("SODA", None)
            "WHISKEY (SCOTCH CAMPBELTOWN)" -> ("WHISKEY", "SCOTCH CAMPBELTOWN")
        """
        if not cat_str:
            return (None, None)

        match = re.match(r"([^(]+)\s*(?:\(([^)]+)\))?", cat_str)
        if not match:
            return (cat_str.strip(), None)

        parent = match.group(1).strip()
        child = match.group(2).strip() if match.group(2) else None
        return (parent, child)

    def get_or_create_category(
        self,
        parent_name: str,
        child_name: str | None,
        stats: dict,
    ) -> IngredientCategory:
        """
        Get or create category with proper closure table maintenance.
        """
        # Create or get parent category
        parent_slug = slugify(parent_name)
        parent, parent_created = IngredientCategory.objects.get_or_create(
            slug=parent_slug,
            defaults={"name": parent_name},
        )
        if parent_created:
            stats["categories_created"] += 1
            # Create self-link for parent
            IngredientCategoryAncestor.objects.get_or_create(
                category=parent,
                ancestor=parent,
                defaults={"depth": 0},
            )

        if not child_name:
            return parent

        # Create or get child category
        child_slug = slugify(child_name)
        child, child_created = IngredientCategory.objects.get_or_create(
            slug=child_slug,
            defaults={"name": child_name},
        )
        if child_created:
            stats["categories_created"] += 1
            # Create self-link for child
            IngredientCategoryAncestor.objects.get_or_create(
                category=child,
                ancestor=child,
                defaults={"depth": 0},
            )
            # Create link to parent
            IngredientCategoryAncestor.objects.get_or_create(
                category=child,
                ancestor=parent,
                defaults={"depth": 1},
            )
            # Copy parent's ancestors (excluding self) with depth+1
            for ancestor_link in parent.ancestor_links.exclude(ancestor=parent):
                IngredientCategoryAncestor.objects.get_or_create(
                    category=child,
                    ancestor=ancestor_link.ancestor,
                    defaults={"depth": ancestor_link.depth + 1},
                )

        return child

    def get_or_create_ingredient(
        self,
        name: str,
        category: IngredientCategory | None,
        stats: dict,
    ) -> Ingredient:
        """Get or create ingredient, adding to category."""
        slug = slugify(name)
        # Handle potential slug collisions by truncating
        if len(slug) > 50:
            slug = slug[:50]

        ingredient, created = Ingredient.objects.get_or_create(
            slug=slug,
            defaults={"name": name},
        )
        if created:
            stats["ingredients_created"] += 1

        # Add category if provided and not already linked
        if category and not ingredient.categories.filter(pk=category.pk).exists():
            ingredient.categories.add(category)

        return ingredient

    def import_recipes(self, recipes_data: dict) -> dict:
        """Import all recipes to database."""
        stats = {
            "recipes_created": 0,
            "categories_created": 0,
            "ingredients_created": 0,
            "links_created": 0,
        }

        for recipe_name, data in recipes_data.items():
            # Create recipe
            slug = slugify(recipe_name)
            if len(slug) > 50:
                slug = slug[:50]

            method = " ".join(data["method_parts"])
            garnish = " ".join(data["garnish_parts"])

            recipe, recipe_created = Recipe.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": recipe_name,
                    "source": "Death & Co",
                    "page": data["page"],
                    "method": method,
                    "garnish": garnish,
                },
            )
            if recipe_created:
                stats["recipes_created"] += 1

                # Add ingredients
                for ing_data in data["ingredients"]:
                    parent_name, child_name = self.parse_category(ing_data["category"])

                    category = None
                    if parent_name:
                        category = self.get_or_create_category(
                            parent_name, child_name, stats
                        )

                    ingredient = self.get_or_create_ingredient(
                        ing_data["name"],
                        category,
                        stats,
                    )

                    RecipeIngredient.objects.create(
                        recipe=recipe,
                        ingredient=ingredient,
                        order=ing_data["order"],
                        # amount is null - CSV doesn't have amounts
                    )
                    stats["links_created"] += 1

        return stats

    def print_summary(self, recipes_data: dict):
        """Print summary for dry run."""
        all_categories = set()
        all_ingredients = set()

        for _recipe_name, data in recipes_data.items():
            for ing in data["ingredients"]:
                all_ingredients.add(ing["name"])
                if ing["category"]:
                    parent, child = self.parse_category(ing["category"])
                    if parent:
                        all_categories.add(parent)
                    if child:
                        all_categories.add(child)

        self.stdout.write("\nSummary:")
        self.stdout.write(f"  Recipes: {len(recipes_data)}")
        self.stdout.write(f"  Unique categories: {len(all_categories)}")
        self.stdout.write(f"  Unique ingredients: {len(all_ingredients)}")

        # Sample recipes
        self.stdout.write("\nSample recipes:")
        for name in list(recipes_data.keys())[:5]:
            data = recipes_data[name]
            ing_count = len(data["ingredients"])
            self.stdout.write(f"  {name} (p.{data['page']}): {ing_count} ingredients")
