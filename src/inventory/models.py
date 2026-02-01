from django.conf import settings
from django.db import models

from ingredients.models import Ingredient


class UserInventory(models.Model):
    """
    Tracks which ingredients a user has in stock.

    Each user has one record per ingredient with an in_stock boolean.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inventory",
    )
    ingredient = models.ForeignKey(
        Ingredient,
        on_delete=models.CASCADE,
        related_name="user_inventories",
    )
    in_stock = models.BooleanField(
        default=False,
        help_text="Whether the user currently has this ingredient",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "user inventory"
        verbose_name_plural = "user inventories"
        unique_together = [["user", "ingredient"]]
        ordering = ["ingredient__name"]

    def __str__(self):
        status = "In Stock" if self.in_stock else "Out of Stock"
        return f"{self.user.username}: {self.ingredient.name} [{status}]"
