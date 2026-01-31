"""
Fix category hierarchy to match planned structure.

The CSV import created 2-level hierarchies like GIN → LONDON DRY,
but we need 3+ levels like SPIRITS → GIN → LONDON DRY.

This command creates top-level parent categories and reparents
existing categories under them.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from ingredients.models import IngredientCategory, IngredientCategoryAncestor

# Mapping of top-level categories to their children
# Based on docs/data-model-planning.md
CATEGORY_HIERARCHY = {
    "SPIRITS": [
        "GIN",
        "WHISKEY",
        "RUM",
        "AGAVE",
        "BRANDY",
        "VODKA",
        # Individual spirits that should be under SPIRITS
        "ABSINTHE",
        "AQUAVIT",
        "CACHAÇA",
        "BATAVIA ARRACK",
    ],
    "LIQUEURS": [
        "LIQUEUR",  # Parent of many subtypes
        "AMARO",
        "CHARTREUSE",
        "CHERRY HEERING",
        "DRAMBUIE",  # If it exists as top-level
    ],
    "FORTIFIED WINES": [
        "VERMOUTH",
        "SHERRY",
        "PORT",
        "APERITIF",
        "APPERITIF",  # Typo in data
        "MADEIRA",
        "WINE",
    ],
    "BITTERS": [
        # BITTERS is already a category with children
        # We'll make it a top-level and keep its children
    ],
    "SWEETENERS": [
        "SYRUP",
        "CORDIAL",
        "SUGAR",
    ],
    "CITRUS": [
        "JUICE",
    ],
    "SODAS": [
        "SODA",
        "TONIC",
        "BEER",
        "CIDER",
    ],
    "DAIRY & EGGS": [
        "EGG",
        "CREAM",
        "BUTTER",
    ],
    "PRODUCE": [
        "FRUIT",
        "VEGETABLE",
        "LEAF",
        "SPICE",
        "MINT",
    ],
    "OTHER": [
        "MIX",
        "RIM",
        "PUREE",
        "AU CHOIX",
        "OTHER",
    ],
}


class Command(BaseCommand):
    help = "Fix category hierarchy to add top-level parent categories"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without saving",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run - no changes saved"))

        with transaction.atomic():
            stats = self.fix_hierarchy(dry_run)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS("Done!"))
        self.stdout.write(f"  Top-level categories created: {stats['created']}")
        self.stdout.write(f"  Categories reparented: {stats['reparented']}")
        self.stdout.write(f"  Closure table entries added: {stats['closure_added']}")

    def fix_hierarchy(self, dry_run: bool) -> dict:
        stats = {"created": 0, "reparented": 0, "closure_added": 0}

        for parent_name, children_names in CATEGORY_HIERARCHY.items():
            # Create or get the top-level parent
            parent = self.get_or_create_top_level(parent_name, stats)

            # Reparent each child category
            for child_name in children_names:
                child = IngredientCategory.objects.filter(
                    name__iexact=child_name
                ).first()

                if child and child != parent:
                    self.reparent_category(child, parent, stats, dry_run)

        return stats

    def get_or_create_top_level(self, name: str, stats: dict) -> IngredientCategory:
        """Create a top-level category if it doesn't exist."""
        slug = slugify(name)
        cat, created = IngredientCategory.objects.get_or_create(
            slug=slug,
            defaults={"name": name},
        )

        if created:
            stats["created"] += 1
            # Create self-link
            IngredientCategoryAncestor.objects.get_or_create(
                category=cat,
                ancestor=cat,
                defaults={"depth": 0},
            )
            self.stdout.write(f"  Created top-level: {name}")

        return cat

    def reparent_category(
        self,
        child: IngredientCategory,
        new_parent: IngredientCategory,
        stats: dict,
        dry_run: bool,
    ):
        """
        Reparent a category under a new parent.

        This updates the closure table for the child and all its descendants.
        """
        # Check if already has this parent
        existing_link = IngredientCategoryAncestor.objects.filter(
            category=child,
            ancestor=new_parent,
            depth=1,
        ).exists()

        if existing_link:
            return  # Already correctly parented

        self.stdout.write(f"  Reparenting: {child.name} → {new_parent.name}")
        stats["reparented"] += 1

        # Get all descendants of the child (including itself)
        descendants = list(child.get_descendants(include_self=True))

        # For each descendant, add links to new_parent and new_parent's ancestors
        parent_ancestors = list(new_parent.get_ancestors(include_self=True))

        for descendant in descendants:
            # Get current depth from descendant to child
            desc_to_child = IngredientCategoryAncestor.objects.filter(
                category=descendant,
                ancestor=child,
            ).first()
            base_depth = desc_to_child.depth if desc_to_child else 0

            for parent_ancestor in parent_ancestors:
                # Get depth from new_parent to this ancestor
                parent_to_anc = IngredientCategoryAncestor.objects.filter(
                    category=new_parent,
                    ancestor=parent_ancestor,
                ).first()
                parent_depth = parent_to_anc.depth if parent_to_anc else 0

                # New depth = descendant→child + 1 (child→parent) + parent→ancestor
                new_depth = base_depth + 1 + parent_depth

                _, created = IngredientCategoryAncestor.objects.get_or_create(
                    category=descendant,
                    ancestor=parent_ancestor,
                    defaults={"depth": new_depth},
                )
                if created:
                    stats["closure_added"] += 1
