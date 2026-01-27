"""Tests for the measurement system."""

from decimal import Decimal

import pytest

from recipes.measurements import (
    MeasurementUnit,
    convert_to_ml,
    convert_unit,
    format_amount_imperial,
    format_amount_metric,
    is_convertible,
    is_count_based,
    is_imprecise,
)


class TestFormatAmountImperial:
    """Tests for imperial fraction formatting."""

    def test_whole_number(self):
        assert format_amount_imperial(Decimal("2")) == "2"

    def test_half(self):
        assert format_amount_imperial(Decimal("0.5")) == "1/2"

    def test_quarter(self):
        assert format_amount_imperial(Decimal("0.25")) == "1/4"

    def test_three_quarters(self):
        assert format_amount_imperial(Decimal("0.75")) == "3/4"

    def test_one_and_half(self):
        assert format_amount_imperial(Decimal("1.5")) == "1 1/2"

    def test_two_and_quarter(self):
        assert format_amount_imperial(Decimal("2.25")) == "2 1/4"

    def test_third(self):
        # 0.333... should round to 1/3
        assert format_amount_imperial(Decimal("0.333")) == "1/3"

    def test_two_thirds(self):
        assert format_amount_imperial(Decimal("0.667")) == "2/3"

    def test_none(self):
        assert format_amount_imperial(None) == ""


class TestFormatAmountMetric:
    """Tests for metric decimal formatting."""

    def test_whole_number(self):
        assert format_amount_metric(Decimal("45")) == "45"

    def test_decimal(self):
        assert format_amount_metric(Decimal("1.5")) == "1.5"

    def test_none(self):
        assert format_amount_metric(None) == ""


class TestConvertToMl:
    """Tests for converting to milliliters."""

    def test_oz_to_ml(self):
        result = convert_to_ml(Decimal("1"), MeasurementUnit.OZ)
        assert result == Decimal("29.5735")

    def test_tsp_to_ml(self):
        result = convert_to_ml(Decimal("1"), MeasurementUnit.TSP)
        assert result == Decimal("4.929")

    def test_ml_to_ml(self):
        result = convert_to_ml(Decimal("30"), MeasurementUnit.ML)
        assert result == Decimal("30")

    def test_unconvertible_unit(self):
        result = convert_to_ml(Decimal("2"), MeasurementUnit.DASH)
        assert result is None


class TestConvertUnit:
    """Tests for unit-to-unit conversion."""

    def test_oz_to_ml(self):
        result = convert_unit(Decimal("1"), MeasurementUnit.OZ, MeasurementUnit.ML)
        assert result == Decimal("29.5735")

    def test_ml_to_oz(self):
        result = convert_unit(
            Decimal("29.5735"), MeasurementUnit.ML, MeasurementUnit.OZ
        )
        assert result == Decimal("1")

    def test_oz_to_cl(self):
        result = convert_unit(Decimal("1"), MeasurementUnit.OZ, MeasurementUnit.CL)
        assert result == Decimal("2.95735")  # 29.5735 / 10

    def test_unconvertible_source(self):
        result = convert_unit(Decimal("2"), MeasurementUnit.DASH, MeasurementUnit.ML)
        assert result is None

    def test_unconvertible_target(self):
        result = convert_unit(Decimal("30"), MeasurementUnit.ML, MeasurementUnit.DASH)
        assert result is None


class TestUnitCategories:
    """Tests for unit classification."""

    def test_convertible_units(self):
        assert is_convertible(MeasurementUnit.OZ)
        assert is_convertible(MeasurementUnit.ML)
        assert is_convertible(MeasurementUnit.TSP)
        assert not is_convertible(MeasurementUnit.DASH)
        assert not is_convertible(MeasurementUnit.WHOLE)

    def test_imprecise_units(self):
        assert is_imprecise(MeasurementUnit.DASH)
        assert is_imprecise(MeasurementUnit.SPLASH)
        assert is_imprecise(MeasurementUnit.RINSE)
        assert not is_imprecise(MeasurementUnit.OZ)

    def test_count_units(self):
        assert is_count_based(MeasurementUnit.WHOLE)
        assert is_count_based(MeasurementUnit.SLICE)
        assert is_count_based(MeasurementUnit.SPRIG)
        assert not is_count_based(MeasurementUnit.OZ)


class TestMeasurementUnitEnum:
    """Tests for the MeasurementUnit enum."""

    def test_all_units_have_labels(self):
        for unit in MeasurementUnit:
            assert unit.label is not None
            assert len(unit.label) > 0

    def test_unit_count(self):
        # 6 convertible + 6 imprecise + 6 count = 18 total
        assert len(MeasurementUnit) == 18
