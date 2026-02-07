"""Tests for inventory services - get_makeable_recipes logic."""

import pytest
from django.contrib.auth.models import User

from ingredients.models import (
    Ingredient,
    IngredientCategory,
    IngredientCategoryAncestor,
)
from inventory.models import UserInventory
from inventory.services import get_makeable_recipes
from recipes.models import Recipe, RecipeIngredient


@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def gin_hierarchy(db):
    """
    Create gin category hierarchy:
    SPIRITS
    └── GIN
        ├── LONDON DRY
        └── NAVY STRENGTH
    """
    spirits = IngredientCategory.objects.create(name="SPIRITS", slug="spirits")
    gin = IngredientCategory.objects.create(name="GIN", slug="gin")
    london_dry = IngredientCategory.objects.create(name="LONDON DRY", slug="london-dry")
    navy_strength = IngredientCategory.objects.create(
        name="NAVY STRENGTH", slug="navy-strength"
    )

    # Closure table for SPIRITS
    IngredientCategoryAncestor.objects.create(
        category=spirits, ancestor=spirits, depth=0
    )

    # Closure table for GIN
    IngredientCategoryAncestor.objects.create(category=gin, ancestor=gin, depth=0)
    IngredientCategoryAncestor.objects.create(category=gin, ancestor=spirits, depth=1)

    # Closure table for LONDON DRY
    IngredientCategoryAncestor.objects.create(
        category=london_dry, ancestor=london_dry, depth=0
    )
    IngredientCategoryAncestor.objects.create(
        category=london_dry, ancestor=gin, depth=1
    )
    IngredientCategoryAncestor.objects.create(
        category=london_dry, ancestor=spirits, depth=2
    )

    # Closure table for NAVY STRENGTH
    IngredientCategoryAncestor.objects.create(
        category=navy_strength, ancestor=navy_strength, depth=0
    )
    IngredientCategoryAncestor.objects.create(
        category=navy_strength, ancestor=gin, depth=1
    )
    IngredientCategoryAncestor.objects.create(
        category=navy_strength, ancestor=spirits, depth=2
    )

    return {
        "spirits": spirits,
        "gin": gin,
        "london_dry": london_dry,
        "navy_strength": navy_strength,
    }


@pytest.fixture
def gins(db, gin_hierarchy):
    """Create gin ingredients in different subcategories."""
    beefeater = Ingredient.objects.create(
        name="Beefeater London Dry Gin", slug="beefeater"
    )
    beefeater.categories.add(gin_hierarchy["london_dry"])

    tanqueray = Ingredient.objects.create(
        name="Tanqueray London Dry Gin", slug="tanqueray"
    )
    tanqueray.categories.add(gin_hierarchy["london_dry"])

    plymouth = Ingredient.objects.create(name="Plymouth Navy Strength", slug="plymouth")
    plymouth.categories.add(gin_hierarchy["navy_strength"])

    return {"beefeater": beefeater, "tanqueray": tanqueray, "plymouth": plymouth}


@pytest.fixture
def citrus(db):
    """Create citrus ingredients."""
    citrus_cat = IngredientCategory.objects.create(name="CITRUS", slug="citrus")
    IngredientCategoryAncestor.objects.create(
        category=citrus_cat, ancestor=citrus_cat, depth=0
    )

    lemon = Ingredient.objects.create(name="Lemon Juice", slug="lemon-juice")
    lemon.categories.add(citrus_cat)

    lime = Ingredient.objects.create(name="Lime Juice", slug="lime-juice")
    lime.categories.add(citrus_cat)

    return {"category": citrus_cat, "lemon": lemon, "lime": lime}


class TestGetMakeableRecipesExactMatch:
    """Tests for depth=0 (exact ingredient match only)."""

    def test_exact_match_finds_recipe(self, user, gins, citrus):
        """Recipe is found when user has exact ingredients."""
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=citrus["lemon"], order=2
        )

        # User has exact ingredients
        UserInventory.objects.create(
            user=user, ingredient=gins["beefeater"], in_stock=True
        )
        UserInventory.objects.create(
            user=user, ingredient=citrus["lemon"], in_stock=True
        )

        recipes = get_makeable_recipes(user, max_depth=0)
        assert recipes.count() == 1
        assert recipes[0].name == "Gin Sour"

    def test_exact_match_missing_ingredient(self, user, gins, citrus):
        """Recipe not found when missing an ingredient at depth=0."""
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=citrus["lemon"], order=2
        )

        # User only has gin, not lemon
        UserInventory.objects.create(
            user=user, ingredient=gins["beefeater"], in_stock=True
        )

        recipes = get_makeable_recipes(user, max_depth=0)
        assert recipes.count() == 0

    def test_exact_match_different_gin_not_found(self, user, gins, citrus):
        """Recipe calling for Beefeater not found when user has Tanqueray at depth=0."""
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=citrus["lemon"], order=2
        )

        # User has Tanqueray, not Beefeater
        UserInventory.objects.create(
            user=user, ingredient=gins["tanqueray"], in_stock=True
        )
        UserInventory.objects.create(
            user=user, ingredient=citrus["lemon"], in_stock=True
        )

        recipes = get_makeable_recipes(user, max_depth=0)
        assert recipes.count() == 0


class TestGetMakeableRecipesCategoryMatch:
    """Tests for depth=1 (same category match)."""

    def test_same_category_finds_recipe(self, user, gins, citrus):
        """
        Recipe calling for Beefeater IS found when user has Tanqueray at depth=1.
        Both are in LONDON DRY category.
        """
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=citrus["lemon"], order=2
        )

        # User has Tanqueray (same category as Beefeater)
        UserInventory.objects.create(
            user=user, ingredient=gins["tanqueray"], in_stock=True
        )
        UserInventory.objects.create(
            user=user, ingredient=citrus["lemon"], in_stock=True
        )

        recipes = get_makeable_recipes(user, max_depth=1)
        assert recipes.count() == 1
        assert recipes[0].name == "Gin Sour"

    def test_sibling_category_not_found_at_depth_1(self, user, gins, citrus):
        """
        Recipe calling for Beefeater (London Dry) NOT found when user has
        Plymouth (Navy Strength) at depth=1. They are sibling categories.
        """
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=citrus["lemon"], order=2
        )

        # User has Plymouth Navy Strength (different subcategory)
        UserInventory.objects.create(
            user=user, ingredient=gins["plymouth"], in_stock=True
        )
        UserInventory.objects.create(
            user=user, ingredient=citrus["lemon"], in_stock=True
        )

        recipes = get_makeable_recipes(user, max_depth=1)
        assert recipes.count() == 0


class TestGetMakeableRecipesParentCategory:
    """Tests for depth=2 (parent category match)."""

    def test_parent_category_finds_sibling(self, user, gins, citrus):
        """
        At depth=2, recipe calling for Beefeater (London Dry) IS found
        when user has Plymouth (Navy Strength) - both under GIN parent.
        """
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=citrus["lemon"], order=2
        )

        # User has Plymouth Navy Strength
        UserInventory.objects.create(
            user=user, ingredient=gins["plymouth"], in_stock=True
        )
        UserInventory.objects.create(
            user=user, ingredient=citrus["lemon"], in_stock=True
        )

        recipes = get_makeable_recipes(user, max_depth=2)
        assert recipes.count() == 1
        assert recipes[0].name == "Gin Sour"


class TestGetMakeableRecipesEdgeCases:
    """Edge case tests."""

    def test_empty_inventory(self, user, gins):
        """No recipes returned when inventory is empty."""
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1
        )

        recipes = get_makeable_recipes(user, max_depth=1)
        assert recipes.count() == 0

    def test_optional_ingredients_ignored(self, user, gins, citrus):
        """Optional ingredients don't affect recipe availability."""
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1, optional=False
        )
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=citrus["lemon"], order=2, optional=True
        )

        # User only has gin, not lemon (which is optional)
        UserInventory.objects.create(
            user=user, ingredient=gins["beefeater"], in_stock=True
        )

        recipes = get_makeable_recipes(user, max_depth=0)
        assert recipes.count() == 1

    def test_in_stock_false_not_counted(self, user, gins, citrus):
        """Ingredients with in_stock=False are not counted."""
        recipe = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=gins["beefeater"], order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=citrus["lemon"], order=2
        )

        # User has both but lemon is out of stock
        UserInventory.objects.create(
            user=user, ingredient=gins["beefeater"], in_stock=True
        )
        UserInventory.objects.create(
            user=user, ingredient=citrus["lemon"], in_stock=False
        )

        recipes = get_makeable_recipes(user, max_depth=0)
        assert recipes.count() == 0

    def test_ingredient_without_category(self, user, db):
        """Ingredients without categories still match exactly."""
        # Create ingredient with no category
        simple = Ingredient.objects.create(name="Simple Syrup", slug="simple-syrup")

        recipe = Recipe.objects.create(name="Simple Drink", slug="simple-drink")
        RecipeIngredient.objects.create(recipe=recipe, ingredient=simple, order=1)

        UserInventory.objects.create(user=user, ingredient=simple, in_stock=True)

        # Should work at any depth since exact match is always included
        recipes = get_makeable_recipes(user, max_depth=1)
        assert recipes.count() == 1

    def test_multiple_recipes_partial_match(self, user, gins, citrus):
        """Only recipes with ALL ingredients satisfied are returned."""
        recipe1 = Recipe.objects.create(name="Gin Only", slug="gin-only")
        RecipeIngredient.objects.create(
            recipe=recipe1, ingredient=gins["beefeater"], order=1
        )

        recipe2 = Recipe.objects.create(name="Gin Sour", slug="gin-sour")
        RecipeIngredient.objects.create(
            recipe=recipe2, ingredient=gins["beefeater"], order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe2, ingredient=citrus["lemon"], order=2
        )

        # User only has gin
        UserInventory.objects.create(
            user=user, ingredient=gins["beefeater"], in_stock=True
        )

        recipes = get_makeable_recipes(user, max_depth=0)
        assert recipes.count() == 1
        assert recipes[0].name == "Gin Only"
