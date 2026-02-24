"""Tests for the recipe import approval workflow."""

import pytest

from recipes.models import Recipe, RecipeImport, TasteTag
from recipes.services.import_processor import approve_import

SINGLE_RECIPE_DATA = {
    "recipes": [
        {
            "name": "Gin Sour",
            "method": "Shake and strain",
            "garnish": "Lemon wheel",
            "ingredients": [
                {"name": "Gin", "amount": "2", "unit": "oz"},
                {"name": "Lemon Juice", "amount": "3/4", "unit": "oz"},
                {"name": "Simple Syrup", "amount": "1/2", "unit": "oz"},
            ],
        }
    ]
}

MULTI_RECIPE_DATA = {
    "recipes": [
        {
            "name": "Negroni",
            "method": "Stir and strain",
            "garnish": "Orange peel",
            "ingredients": [
                {"name": "Gin", "amount": "1", "unit": "oz"},
                {"name": "Sweet Vermouth", "amount": "1", "unit": "oz"},
                {"name": "Campari", "amount": "1", "unit": "oz"},
            ],
        },
        {
            "name": "Manhattan",
            "method": "Stir and strain",
            "garnish": "Cherry",
            "ingredients": [
                {"name": "Rye Whiskey", "amount": "2", "unit": "oz"},
                {"name": "Sweet Vermouth", "amount": "1", "unit": "oz"},
                {"name": "Angostura Bitters", "amount": "2", "unit": "dash"},
            ],
        },
    ]
}


@pytest.fixture
def import_single(db):
    """A parsed RecipeImport containing one recipe."""
    return RecipeImport.objects.create(
        source_image="",
        status=RecipeImport.Status.PARSED,
        parsed_data=SINGLE_RECIPE_DATA,
    )


@pytest.fixture
def import_multi(db):
    """A parsed RecipeImport containing two recipes."""
    return RecipeImport.objects.create(
        source_image="",
        status=RecipeImport.Status.PARSED,
        parsed_data=MULTI_RECIPE_DATA,
    )


class TestApproveImportCreatesRecipes:
    def test_single_recipe_returned(self, import_single):
        recipes = approve_import(import_single)
        assert len(recipes) == 1
        assert recipes[0].name == "Gin Sour"

    def test_single_recipe_persisted(self, import_single):
        approve_import(import_single)
        assert Recipe.objects.filter(name="Gin Sour").exists()

    def test_single_recipe_ingredients_created(self, import_single):
        recipes = approve_import(import_single)
        names = set(
            recipes[0].recipe_ingredients.values_list("ingredient__name", flat=True)
        )
        assert names == {"Gin", "Lemon Juice", "Simple Syrup"}

    def test_multiple_recipes_all_returned(self, import_multi):
        recipes = approve_import(import_multi)
        assert len(recipes) == 2
        assert {r.name for r in recipes} == {"Negroni", "Manhattan"}

    def test_multiple_recipes_all_persisted(self, import_multi):
        approve_import(import_multi)
        assert Recipe.objects.filter(name="Negroni").exists()
        assert Recipe.objects.filter(name="Manhattan").exists()

    def test_multiple_recipes_all_linked_to_import(self, import_multi):
        approve_import(import_multi)
        linked_names = set(import_multi.recipes.values_list("name", flat=True))
        assert linked_names == {"Negroni", "Manhattan"}

    def test_import_status_becomes_approved(self, import_single):
        approve_import(import_single)
        import_single.refresh_from_db()
        assert import_single.status == RecipeImport.Status.APPROVED

    def test_import_approved_at_set(self, import_single):
        approve_import(import_single)
        import_single.refresh_from_db()
        assert import_single.approved_at is not None

    def test_source_applied_to_new_recipe(self, import_single):
        recipes = approve_import(import_single, source="Death & Co")
        assert recipes[0].source == "Death & Co"


class TestFindMatchingRecipe:
    """Tests for the slug-based fallback in find_matching_recipe."""

    def test_matches_by_exact_name(self, db):
        from recipes.services.import_processor import find_matching_recipe

        Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        assert find_matching_recipe("Gin Sour") is not None

    def test_matches_case_insensitive(self, db):
        from recipes.services.import_processor import find_matching_recipe

        Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        assert find_matching_recipe("gin sour") is not None

    def test_matches_unicode_apostrophe_variant(self, db):
        """Curly ' and straight ' both slugify to the same slug."""
        from recipes.services.import_processor import find_matching_recipe

        # Original stored with curly right single quote (U+2019)
        Recipe.objects.create(name="Bee\u2019s Knees", slug="bees-knees")
        # Import produces straight apostrophe
        result = find_matching_recipe("Bee's Knees")
        assert result is not None
        assert result.slug == "bees-knees"

    def test_no_match_returns_none(self, db):
        from recipes.services.import_processor import find_matching_recipe

        assert find_matching_recipe("Nonexistent Recipe") is None


class TestApproveImportUpdatesExistingRecipes:
    def test_existing_recipe_updated_not_duplicated(self, db, import_single):
        Recipe.objects.create(name="Gin Sour", slug="gin-sour", method="Old method")
        approve_import(import_single)
        assert Recipe.objects.filter(name="Gin Sour").count() == 1
        assert Recipe.objects.get(name="Gin Sour").method == "Shake and strain"

    def test_existing_recipe_ingredients_replaced(self, db, import_single):
        from ingredients.models import Ingredient
        from recipes.models import RecipeIngredient

        existing = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        stale = Ingredient.objects.create(name="Stale Ingredient", slug="stale")
        RecipeIngredient.objects.create(recipe=existing, ingredient=stale, order=0)

        approve_import(import_single)

        existing.refresh_from_db()
        names = set(
            existing.recipe_ingredients.values_list("ingredient__name", flat=True)
        )
        assert "Stale Ingredient" not in names
        assert names == {"Gin", "Lemon Juice", "Simple Syrup"}

    def test_source_not_overwritten_on_update(self, db, import_single):
        Recipe.objects.create(
            name="Gin Sour", slug="gin-sour", source="Original Source"
        )
        approve_import(import_single, source="New Source")
        assert Recipe.objects.get(name="Gin Sour").source == "Original Source"

    def test_unicode_apostrophe_variant_updates_not_duplicates(self, db):
        """Import with straight apostrophe should update the curly-apostrophe original."""
        Recipe.objects.create(name="Bee\u2019s Knees", slug="bees-knees", method="Old")
        ri = RecipeImport.objects.create(
            source_image="",
            status=RecipeImport.Status.PARSED,
            parsed_data={
                "recipes": [
                    {
                        "name": "Bee's Knees",  # straight apostrophe from OCR
                        "method": "Shake and strain",
                        "ingredients": [{"name": "Gin", "amount": "2", "unit": "oz"}],
                    }
                ]
            },
        )
        approve_import(ri)
        assert Recipe.objects.filter(slug__startswith="bees-knees").count() == 1
        assert Recipe.objects.get(slug="bees-knees").method == "Shake and strain"

    def test_multi_import_updates_existing_and_creates_new(self, db, import_multi):
        Recipe.objects.create(name="Negroni", slug="negroni", method="Old method")
        approve_import(import_multi)

        assert Recipe.objects.filter(name="Negroni").count() == 1
        assert Recipe.objects.get(name="Negroni").method == "Stir and strain"
        assert Recipe.objects.filter(name="Manhattan").exists()


class TestApproveImportTasteTags:
    def test_taste_tags_applied_to_single_recipe(self, db, import_single):
        tag = TasteTag.objects.create(name="Sour", slug="sour")
        import_single.suggested_taste_tags.add(tag)

        recipes = approve_import(import_single)
        assert tag in recipes[0].taste_tags.all()

    def test_taste_tags_applied_to_all_recipes_in_multi_import(self, db, import_multi):
        tag = TasteTag.objects.create(name="Stirred", slug="stirred")
        import_multi.suggested_taste_tags.add(tag)

        recipes = approve_import(import_multi)
        for recipe in recipes:
            assert tag in recipe.taste_tags.all()


class TestApproveImportErrors:
    def test_raises_if_already_approved(self, import_single):
        approve_import(import_single)
        with pytest.raises(ValueError, match="already approved"):
            approve_import(import_single)

    def test_raises_if_no_parsed_data(self, db):
        ri = RecipeImport.objects.create(
            source_image="",
            status=RecipeImport.Status.PARSED,
            parsed_data=None,
        )
        with pytest.raises(ValueError, match="No parsed data"):
            approve_import(ri)

    def test_raises_if_recipes_list_empty(self, db):
        ri = RecipeImport.objects.create(
            source_image="",
            status=RecipeImport.Status.PARSED,
            parsed_data={"recipes": []},
        )
        with pytest.raises(ValueError, match="No recipes"):
            approve_import(ri)

    def test_error_leaves_import_status_unchanged(self, db):
        ri = RecipeImport.objects.create(
            source_image="",
            status=RecipeImport.Status.PARSED,
            parsed_data=None,
        )
        with pytest.raises(ValueError):
            approve_import(ri)
        ri.refresh_from_db()
        assert ri.status == RecipeImport.Status.PARSED


class TestReapproveImport:
    """Tests for the re-approve flow (resetting status and re-running approval)."""

    def test_reapprove_processes_all_recipes(self, db, import_multi):
        approve_import(import_multi)

        # Simulate admin re-approve action
        import_multi.status = RecipeImport.Status.PARSED
        import_multi.save(update_fields=["status"])

        recipes = approve_import(import_multi)
        assert len(recipes) == 2

    def test_reapprove_does_not_duplicate_recipes(self, db, import_multi):
        approve_import(import_multi)

        import_multi.status = RecipeImport.Status.PARSED
        import_multi.save(update_fields=["status"])
        approve_import(import_multi)

        assert Recipe.objects.filter(name="Negroni").count() == 1
        assert Recipe.objects.filter(name="Manhattan").count() == 1

    def test_reapprove_updates_linked_recipes(self, db, import_multi):
        approve_import(import_multi)

        import_multi.status = RecipeImport.Status.PARSED
        import_multi.save(update_fields=["status"])
        approve_import(import_multi)

        linked_names = set(import_multi.recipes.values_list("name", flat=True))
        assert linked_names == {"Negroni", "Manhattan"}

    def test_reapprove_blocked_without_status_reset(self, import_single):
        """Calling approve without resetting status raises an error."""
        approve_import(import_single)
        with pytest.raises(ValueError, match="already approved"):
            approve_import(import_single)