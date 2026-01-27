"""
Measurement units and conversion utilities for cocktail recipes.

Supports:
- Volume units (oz, ml, cl, tsp, tbsp, barspoon)
- Imprecise units (dash, drop, rinse, float, top, splash)
- Count-based units (whole, piece, slice, wedge, sprig, leaf)

Imperial amounts are displayed as fractions (1/2, 3/4) for bartender-friendliness.
"""

from decimal import Decimal

from django.db import models


class MeasurementUnit(models.TextChoices):
    """Valid measurement units for recipe ingredients."""

    # Volume - Convertible between each other
    OZ = "oz", "ounce"
    ML = "ml", "milliliter"
    CL = "cl", "centiliter"
    TSP = "tsp", "teaspoon"
    TBSP = "tbsp", "tablespoon"
    BARSPOON = "barspoon", "barspoon"

    # Volume - Imprecise (not reliably convertible)
    DASH = "dash", "dash"
    DROP = "drop", "drop"
    RINSE = "rinse", "rinse"
    FLOAT = "float", "float"
    TOP = "top", "top"
    SPLASH = "splash", "splash"

    # Count-based
    WHOLE = "whole", "whole"
    PIECE = "piece", "piece"
    SLICE = "slice", "slice"
    WEDGE = "wedge", "wedge"
    SPRIG = "sprig", "sprig"
    LEAF = "leaf", "leaf"


# ml equivalents for convertible units
ML_CONVERSIONS: dict[str, Decimal] = {
    MeasurementUnit.OZ: Decimal("29.5735"),
    MeasurementUnit.ML: Decimal("1"),
    MeasurementUnit.CL: Decimal("10"),
    MeasurementUnit.TSP: Decimal("4.929"),
    MeasurementUnit.TBSP: Decimal("14.787"),
    MeasurementUnit.BARSPOON: Decimal("5"),
}

# Units that can be converted between each other
CONVERTIBLE_UNITS: set[str] = set(ML_CONVERSIONS.keys())

# Units that are imprecise/contextual
IMPRECISE_UNITS: set[str] = {
    MeasurementUnit.DASH,
    MeasurementUnit.DROP,
    MeasurementUnit.RINSE,
    MeasurementUnit.FLOAT,
    MeasurementUnit.TOP,
    MeasurementUnit.SPLASH,
}

# Count-based units
COUNT_UNITS: set[str] = {
    MeasurementUnit.WHOLE,
    MeasurementUnit.PIECE,
    MeasurementUnit.SLICE,
    MeasurementUnit.WEDGE,
    MeasurementUnit.SPRIG,
    MeasurementUnit.LEAF,
}

# Common fractions in bartending for display
DISPLAY_FRACTIONS: dict[Decimal, str] = {
    Decimal("0.125"): "1/8",
    Decimal("0.25"): "1/4",
    Decimal("0.333"): "1/3",
    Decimal("0.375"): "3/8",
    Decimal("0.5"): "1/2",
    Decimal("0.625"): "5/8",
    Decimal("0.667"): "2/3",
    Decimal("0.75"): "3/4",
    Decimal("0.875"): "7/8",
}


def format_amount_imperial(amount: Decimal | None) -> str:
    """
    Format a decimal amount as a bartender-friendly fraction.

    Examples:
        1.5 -> "1 1/2"
        0.75 -> "3/4"
        2.0 -> "2"
        0.333 -> "1/3"
        None -> ""
    """
    if amount is None:
        return ""

    whole = int(amount)
    frac = amount - whole

    # Round to nearest common fraction
    frac_str = ""
    if frac > Decimal("0.01"):
        # Find closest fraction
        closest = min(
            DISPLAY_FRACTIONS.keys(),
            key=lambda x: abs(x - frac),
        )
        if abs(closest - frac) < Decimal("0.05"):
            frac_str = DISPLAY_FRACTIONS[closest]

    if whole and frac_str:
        return f"{whole} {frac_str}"
    elif whole:
        return str(whole)
    elif frac_str:
        return frac_str
    else:
        # Fallback for unusual values - show reasonable precision
        return f"{amount:.2f}".rstrip("0").rstrip(".")


def format_amount_metric(amount: Decimal | None) -> str:
    """
    Format a decimal amount for metric display (no fractions).

    Examples:
        1.5 -> "1.5"
        2.0 -> "2"
        None -> ""
    """
    if amount is None:
        return ""

    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.1f}"


def convert_to_ml(amount: Decimal, unit: str) -> Decimal | None:
    """
    Convert an amount to milliliters.

    Returns None if the unit is not convertible.
    """
    if unit not in CONVERTIBLE_UNITS:
        return None
    return amount * ML_CONVERSIONS[unit]


def convert_from_ml(ml_amount: Decimal, target_unit: str) -> Decimal | None:
    """
    Convert milliliters to a target unit.

    Returns None if the target unit is not convertible.
    """
    if target_unit not in CONVERTIBLE_UNITS:
        return None
    return ml_amount / ML_CONVERSIONS[target_unit]


def convert_unit(
    amount: Decimal,
    from_unit: str,
    to_unit: str,
) -> Decimal | None:
    """
    Convert an amount from one unit to another.

    Returns None if either unit is not convertible.
    """
    ml = convert_to_ml(amount, from_unit)
    if ml is None:
        return None
    return convert_from_ml(ml, to_unit)


def is_convertible(unit: str) -> bool:
    """Check if a unit can be converted to other volume units."""
    return unit in CONVERTIBLE_UNITS


def is_imprecise(unit: str) -> bool:
    """Check if a unit is imprecise (dash, splash, etc.)."""
    return unit in IMPRECISE_UNITS


def is_count_based(unit: str) -> bool:
    """Check if a unit is count-based (whole, piece, etc.)."""
    return unit in COUNT_UNITS
