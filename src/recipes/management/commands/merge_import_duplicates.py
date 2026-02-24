"""
Management command to find and merge recipe duplicates created by the import
workflow when find_matching_recipe failed to match an existing recipe (e.g.
due to Unicode apostrophe variants in OCR output).

Duplicates are identified by slug pattern: a recipe with slug "xxx-1" is a
duplicate of "xxx" when slugify(duplicate.name) == "xxx" — i.e. both names
map to the same base slug.

Merge strategy:
  - The original (base slug) is kept as the canonical record.
  - Non-empty fields (method, garnish, notes, page) from the duplicate
    overwrite the original (the duplicate holds newer import data).
  - Ingredients are replaced with the duplicate's ingredients.
  - Taste tags are merged (union).
  - RecipeImport.recipes M2M links are re-pointed to the original.
  - The duplicate is then deleted.

Runs in dry-run mode by default. Pass --apply to commit changes.
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from recipes.models import Recipe, RecipeIngredient

SLUG_SUFFIX_RE = re.compile(r"^(.+)-(\d+)$")


def find_duplicate_pairs():
    """
    Return list of (original, duplicate) Recipe pairs.

    A duplicate is a recipe whose slug matches {base}-{N} where {base} exists
    AND where slugify(duplicate.name) == base (same underlying name).
    """
    pairs = []
    for recipe in Recipe.objects.order_by("slug"):
        m = SLUG_SUFFIX_RE.match(recipe.slug)
        if not m:
            continue
        base_slug = m.group(1)
        original = Recipe.objects.filter(slug=base_slug).first()
        if original and slugify(recipe.name)[:50] == base_slug:
            pairs.append((original, recipe))
    return pairs


@transaction.atomic
def merge_into_original(original: Recipe, duplicate: Recipe, stdout=None) -> None:
    """Merge duplicate's data into original, then delete duplicate."""

    def log(msg):
        if stdout:
            stdout.write(f"    {msg}")

    # Update scalar fields from duplicate if non-empty
    changed = False
    for field in ("method", "garnish", "notes"):
        dup_val = getattr(duplicate, field)
        orig_val = getattr(original, field)
        if dup_val and dup_val != orig_val:
            log(f"{field}: {repr(orig_val)!s:.60} → {repr(dup_val)!s:.60}")
            setattr(original, field, dup_val)
            changed = True
    if duplicate.page and duplicate.page != original.page:
        log(f"page: {original.page} → {duplicate.page}")
        original.page = duplicate.page
        changed = True
    if changed:
        original.save()

    # Replace ingredients
    orig_ing_names = list(
        original.recipe_ingredients.values_list("ingredient__name", flat=True)
    )
    dup_ing_names = list(
        duplicate.recipe_ingredients.values_list("ingredient__name", flat=True)
    )
    if orig_ing_names != dup_ing_names:
        log(f"ingredients: {orig_ing_names} → {dup_ing_names}")
    original.recipe_ingredients.all().delete()
    duplicate.recipe_ingredients.all().update(recipe=original)

    # Merge taste tags
    new_tags = duplicate.taste_tags.exclude(pk__in=original.taste_tags.all())
    if new_tags.exists():
        tag_names = list(new_tags.values_list("name", flat=True))
        log(f"adding taste tags: {tag_names}")
        original.taste_tags.add(*new_tags)

    # Re-point RecipeImport.recipes M2M links
    for recipe_import in duplicate.imports.all():
        recipe_import.recipes.remove(duplicate)
        recipe_import.recipes.add(original)
        log(f"re-linked import #{recipe_import.pk}")

    duplicate.delete()
    log("duplicate deleted")


class Command(BaseCommand):
    help = "Merge recipe duplicates created by the import workflow"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually apply the merges (default is dry-run preview).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        pairs = find_duplicate_pairs()

        if not pairs:
            self.stdout.write(self.style.SUCCESS("No duplicates found."))
            return

        self.stdout.write(f"Found {len(pairs)} duplicate pair(s):\n")

        for original, duplicate in pairs:
            self.stdout.write(
                f"  ORIGINAL:  {repr(original.name)} (slug: {original.slug})"
            )
            self.stdout.write(
                f"  DUPLICATE: {repr(duplicate.name)} (slug: {duplicate.slug})"
            )

            if apply:
                merge_into_original(original, duplicate, stdout=self.stdout)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Merged '{duplicate.name}' into '{original.name}'\n"
                    )
                )
            else:
                self.stdout.write("")

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run — no changes made. Re-run with --apply to merge."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Done. Merged {len(pairs)} duplicate(s).")
            )