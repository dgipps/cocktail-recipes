"""Tests for the ingredients app - categories and hierarchy."""

import pytest

from ingredients.models import (
    Ingredient,
    IngredientCategory,
    IngredientCategoryAncestor,
)


@pytest.fixture
def category_hierarchy(db):
    """
    Create a test category hierarchy:
    SPIRITS
    └── GIN
        └── LONDON DRY
    """
    spirits = IngredientCategory.objects.create(name="SPIRITS", slug="spirits")
    gin = IngredientCategory.objects.create(name="GIN", slug="gin")
    london_dry = IngredientCategory.objects.create(name="LONDON DRY", slug="london-dry")

    # Create closure table entries for SPIRITS (self only - top level)
    IngredientCategoryAncestor.objects.create(
        category=spirits, ancestor=spirits, depth=0
    )

    # Create closure table entries for GIN
    IngredientCategoryAncestor.objects.create(category=gin, ancestor=gin, depth=0)
    IngredientCategoryAncestor.objects.create(category=gin, ancestor=spirits, depth=1)

    # Create closure table entries for LONDON DRY
    IngredientCategoryAncestor.objects.create(
        category=london_dry, ancestor=london_dry, depth=0
    )
    IngredientCategoryAncestor.objects.create(
        category=london_dry, ancestor=gin, depth=1
    )
    IngredientCategoryAncestor.objects.create(
        category=london_dry, ancestor=spirits, depth=2
    )

    return {"spirits": spirits, "gin": gin, "london_dry": london_dry}


@pytest.fixture
def ingredient_with_category(db, category_hierarchy):
    """Create an ingredient assigned to LONDON DRY category."""
    ingredient = Ingredient.objects.create(
        name="Beefeater London Dry Gin",
        slug="beefeater-london-dry-gin",
    )
    ingredient.categories.add(category_hierarchy["london_dry"])
    return ingredient


class TestCategoryHierarchy:
    """Tests for category ancestor/descendant queries."""

    def test_get_ancestors_includes_self(self, category_hierarchy):
        london_dry = category_hierarchy["london_dry"]
        ancestors = list(london_dry.get_ancestors(include_self=True))

        assert len(ancestors) == 3
        assert ancestors[0].name == "LONDON DRY"
        assert ancestors[1].name == "GIN"
        assert ancestors[2].name == "SPIRITS"

    def test_get_ancestors_excludes_self(self, category_hierarchy):
        london_dry = category_hierarchy["london_dry"]
        ancestors = list(london_dry.get_ancestors(include_self=False))

        assert len(ancestors) == 2
        assert ancestors[0].name == "GIN"
        assert ancestors[1].name == "SPIRITS"

    def test_get_ancestors_top_level(self, category_hierarchy):
        spirits = category_hierarchy["spirits"]
        ancestors = list(spirits.get_ancestors(include_self=False))

        assert len(ancestors) == 0

    def test_get_descendants_includes_self(self, category_hierarchy):
        spirits = category_hierarchy["spirits"]
        descendants = list(spirits.get_descendants(include_self=True))

        names = {d.name for d in descendants}
        assert names == {"SPIRITS", "GIN", "LONDON DRY"}

    def test_get_descendants_excludes_self(self, category_hierarchy):
        spirits = category_hierarchy["spirits"]
        descendants = list(spirits.get_descendants(include_self=False))

        names = {d.name for d in descendants}
        assert names == {"GIN", "LONDON DRY"}

    def test_get_descendants_leaf_node(self, category_hierarchy):
        london_dry = category_hierarchy["london_dry"]
        descendants = list(london_dry.get_descendants(include_self=False))

        assert len(descendants) == 0


class TestIngredientCategories:
    """Tests for ingredient category relationships."""

    def test_ingredient_direct_categories(
        self, ingredient_with_category, category_hierarchy
    ):
        """Ingredient should have only its directly assigned category."""
        categories = list(ingredient_with_category.categories.all())

        assert len(categories) == 1
        assert categories[0].name == "LONDON DRY"

    def test_ingredient_get_all_categories(
        self, ingredient_with_category, category_hierarchy
    ):
        """get_all_categories should return category and all ancestors."""
        all_categories = list(ingredient_with_category.get_all_categories())

        names = {c.name for c in all_categories}
        assert names == {"LONDON DRY", "GIN", "SPIRITS"}

    def test_ingredient_multiple_categories(self, db, category_hierarchy):
        """Ingredient can belong to multiple category trees."""
        # Create another category tree
        liqueurs = IngredientCategory.objects.create(name="LIQUEURS", slug="liqueurs")
        IngredientCategoryAncestor.objects.create(
            category=liqueurs, ancestor=liqueurs, depth=0
        )

        amaro = IngredientCategory.objects.create(name="AMARO", slug="amaro")
        IngredientCategoryAncestor.objects.create(
            category=amaro, ancestor=amaro, depth=0
        )
        IngredientCategoryAncestor.objects.create(
            category=amaro, ancestor=liqueurs, depth=1
        )

        # Campari belongs to both AMARO and another category
        campari = Ingredient.objects.create(name="Campari", slug="campari")
        campari.categories.add(amaro)

        all_categories = campari.get_all_categories()
        names = {c.name for c in all_categories}
        assert names == {"AMARO", "LIQUEURS"}


class TestCategoryIngredientQueries:
    """Tests for finding ingredients in a category and subcategories."""

    def test_find_ingredients_in_category_direct(
        self, ingredient_with_category, category_hierarchy
    ):
        """Find ingredient directly in LONDON DRY."""
        london_dry = category_hierarchy["london_dry"]
        ingredients = london_dry.ingredients.all()

        assert ingredients.count() == 1
        assert ingredients[0].name == "Beefeater London Dry Gin"

    def test_find_ingredients_in_parent_category(
        self, ingredient_with_category, category_hierarchy
    ):
        """GIN category should find ingredients in LONDON DRY subcategory."""
        gin = category_hierarchy["gin"]

        # Direct query won't find it
        assert gin.ingredients.count() == 0

        # Query through descendants will find it
        descendants = gin.get_descendants(include_self=True)
        ingredients = Ingredient.objects.filter(
            categories__in=descendants
        ).distinct()

        assert ingredients.count() == 1
        assert ingredients[0].name == "Beefeater London Dry Gin"

    def test_find_ingredients_in_grandparent_category(
        self, ingredient_with_category, category_hierarchy
    ):
        """SPIRITS category should find ingredients in LONDON DRY subcategory."""
        spirits = category_hierarchy["spirits"]

        descendants = spirits.get_descendants(include_self=True)
        ingredients = Ingredient.objects.filter(
            categories__in=descendants
        ).distinct()

        assert ingredients.count() == 1
        assert ingredients[0].name == "Beefeater London Dry Gin"
