from decimal import Decimal

from django.db import models

from ingredients.models import Ingredient

from .measurements import (
    CONVERTIBLE_UNITS,
    MeasurementUnit,
    convert_to_ml,
    convert_unit,
    format_amount_imperial,
    format_amount_metric,
)


class Recipe(models.Model):
    """
    A cocktail recipe.

    Stores metadata about the recipe. Ingredients are linked via RecipeIngredient.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    source = models.CharField(
        max_length=200,
        blank=True,
        help_text="Source of the recipe (e.g., 'Death & Co', 'PDT')",
    )
    page = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Page number in source book",
    )
    method = models.TextField(
        blank=True,
        help_text="Preparation instructions",
    )
    garnish = models.TextField(
        blank=True,
        help_text="Garnish description",
    )
    notes = models.TextField(
        blank=True,
        help_text="Additional notes about the recipe",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_scaled_ingredients(self, scale: Decimal = Decimal("1")):
        """
        Return ingredients with amounts scaled.

        scale=2 doubles the recipe, scale=0.5 halves it.
        """
        for ri in self.recipe_ingredients.select_related("ingredient").all():
            yield {
                "ingredient": ri.ingredient,
                "amount": ri.scaled(scale),
                "unit": ri.unit,
                "display": ri.display_amount_scaled(scale),
                "optional": ri.optional,
                "notes": ri.notes,
            }


class RecipeIngredient(models.Model):
    """
    Links a recipe to its ingredients with amount and ordering.

    Amounts are stored as Decimal for precise math (scaling, conversion).
    Units are validated via the MeasurementUnit enum.
    Display formatting converts decimals to bartender-friendly fractions.

    Example: "20th Century" calls for "Beefeater London Dry Gin"
    - ingredient = Ingredient(name="Beefeater London Dry Gin")
    - amount = Decimal("2"), unit = "oz"
    - display_amount() returns "2 oz"
    - display_amount(metric=True) returns "59 ml"
    """

    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.CASCADE,
        related_name="recipe_ingredients",
    )
    ingredient = models.ForeignKey(
        Ingredient,
        on_delete=models.PROTECT,
        related_name="recipe_uses",
    )
    amount = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Numeric amount (e.g., 1.5 for '1 1/2')",
    )
    unit = models.CharField(
        max_length=20,
        choices=MeasurementUnit.choices,
        blank=True,
        help_text="Unit of measurement",
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Order of ingredient in recipe",
    )
    optional = models.BooleanField(
        default=False,
        help_text="Is this ingredient optional?",
    )
    notes = models.CharField(
        max_length=200,
        blank=True,
        help_text="Notes (e.g., '2-3 dashes' when amount is minimum)",
    )

    class Meta:
        ordering = ["order"]
        verbose_name = "recipe ingredient"
        verbose_name_plural = "recipe ingredients"

    def __str__(self):
        display = self.display_amount()
        if display and self.unit:
            return f"{display} {self.unit} {self.ingredient.name}"
        elif display:
            return f"{display} {self.ingredient.name}"
        return self.ingredient.name

    def to_ml(self) -> Decimal | None:
        """
        Convert amount to milliliters (if convertible).

        Returns None if amount is None or unit is not convertible.
        """
        if self.amount is None or self.unit not in CONVERTIBLE_UNITS:
            return None
        return convert_to_ml(self.amount, self.unit)

    def convert_to(self, target_unit: str) -> Decimal | None:
        """
        Convert amount to target unit (if both are convertible).

        Returns None if conversion is not possible.
        """
        if self.amount is None:
            return None
        return convert_unit(self.amount, self.unit, target_unit)

    def scaled(self, factor: Decimal) -> Decimal | None:
        """Return amount scaled by factor."""
        if self.amount is None:
            return None
        return self.amount * factor

    def display_amount(self, metric: bool = False) -> str:
        """
        Return formatted amount for display.

        Args:
            metric: If True, convert oz to ml for display.

        Returns:
            Formatted string like "1 1/2" or "45" (ml).
        """
        if self.amount is None:
            return ""

        # Convert oz to ml if metric requested
        if metric and self.unit == MeasurementUnit.OZ:
            ml = self.to_ml()
            if ml is not None:
                return format_amount_metric(ml)

        # Imperial units (oz, tsp, tbsp) get fraction formatting
        if self.unit in {MeasurementUnit.OZ, MeasurementUnit.TSP, MeasurementUnit.TBSP}:
            return format_amount_imperial(self.amount)

        # Metric units get decimal formatting
        if self.unit in {MeasurementUnit.ML, MeasurementUnit.CL}:
            return format_amount_metric(self.amount)

        # Count and imprecise units - show as integer if whole number
        if self.amount == int(self.amount):
            return str(int(self.amount))
        return str(self.amount)

    def display_amount_scaled(
        self,
        factor: Decimal,
        metric: bool = False,
    ) -> str:
        """
        Return formatted amount scaled by factor.

        Args:
            factor: Scale factor (e.g., 2 for double, 0.5 for half).
            metric: If True, convert oz to ml for display.
        """
        scaled = self.scaled(factor)
        if scaled is None:
            return ""

        # Convert oz to ml if metric requested
        if metric and self.unit == MeasurementUnit.OZ:
            ml = convert_to_ml(scaled, self.unit)
            if ml is not None:
                return format_amount_metric(ml)

        # Imperial units get fraction formatting
        if self.unit in {MeasurementUnit.OZ, MeasurementUnit.TSP, MeasurementUnit.TBSP}:
            return format_amount_imperial(scaled)

        # Metric units get decimal formatting
        if self.unit in {MeasurementUnit.ML, MeasurementUnit.CL}:
            return format_amount_metric(scaled)

        # Count and imprecise units
        if scaled == int(scaled):
            return str(int(scaled))
        return str(scaled)

    def display_full(self, metric: bool = False) -> str:
        """
        Return full display string with amount and unit.

        Examples:
            "1 1/2 oz"
            "2 dashes"
            "45 ml"
        """
        amount_str = self.display_amount(metric=metric)
        if not amount_str:
            return ""

        # For metric conversion of oz, show ml
        if metric and self.unit == MeasurementUnit.OZ:
            return f"{amount_str} ml"

        if self.unit:
            return f"{amount_str} {self.unit}"
        return amount_str
