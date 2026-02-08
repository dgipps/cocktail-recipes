"""Ingredient services."""

from .categorizer import CategorizationError, categorize_ingredient

__all__ = ["categorize_ingredient", "CategorizationError"]
