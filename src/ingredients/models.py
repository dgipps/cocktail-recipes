from django.conf import settings
from django.db import models
from django.utils import timezone


class IngredientCategory(models.Model):
    """
    Hierarchical categories for ingredients.

    Examples: Spirit > Gin > London Dry, or Sweetener > Syrup > Simple Syrup

    The hierarchy is managed via the IngredientCategoryAncestor closure table
    for efficient ancestor/descendant queries.
    """

    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "ingredient categories"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_ancestors(self, include_self=True):
        """Return all ancestor categories (parents, grandparents, etc.)."""
        min_depth = 0 if include_self else 1
        return IngredientCategory.objects.filter(
            descendant_links__category=self,
            descendant_links__depth__gte=min_depth,
        ).order_by("descendant_links__depth")

    def get_descendants(self, include_self=True):
        """Return all descendant categories (children, grandchildren, etc.)."""
        min_depth = 0 if include_self else 1
        return IngredientCategory.objects.filter(
            ancestor_links__ancestor=self,
            ancestor_links__depth__gte=min_depth,
        ).order_by("ancestor_links__depth")


class IngredientCategoryAncestor(models.Model):
    """
    Closure table storing ALL ancestor relationships for efficient hierarchy queries.

    For a category "London Dry Gin" with parent "Gin" and grandparent "Spirit":
    - (London Dry Gin, London Dry Gin, 0)  # self-reference
    - (London Dry Gin, Gin, 1)              # parent
    - (London Dry Gin, Spirit, 2)           # grandparent

    This enables single-query lookups for:
    - All ancestors of a category
    - All descendants of a category
    """

    category = models.ForeignKey(
        IngredientCategory,
        on_delete=models.CASCADE,
        related_name="ancestor_links",
        help_text="The category whose ancestors are being stored",
    )
    ancestor = models.ForeignKey(
        IngredientCategory,
        on_delete=models.CASCADE,
        related_name="descendant_links",
        help_text="An ancestor of the category (including self at depth 0)",
    )
    depth = models.PositiveIntegerField(
        help_text="Distance from category to ancestor (0 = self, 1 = parent, etc.)"
    )

    class Meta:
        unique_together = ["category", "ancestor"]
        verbose_name = "category ancestor"
        verbose_name_plural = "category ancestors"

    def __str__(self):
        return f"{self.category} -> {self.ancestor} (depth {self.depth})"


class Ingredient(models.Model):
    """
    Specific ingredients/products like 'Tanqueray London Dry Gin' or 'Campari'.

    Ingredients can belong to MULTIPLE categories via M2M relationship.
    Examples:
    - Campari: [Amaro, Aperitivo, Bitter Liqueur]
    - Lime Cordial: [Citrus > Cordials, Sweeteners]
    """

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(unique=True)
    categories = models.ManyToManyField(
        IngredientCategory,
        related_name="ingredients",
        help_text="Categories this ingredient belongs to",
    )
    description = models.TextField(blank=True)
    needs_categorization = models.BooleanField(
        default=False,
        help_text="Flag for ingredients needing category assignment",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_all_categories(self):
        """
        Return all categories this ingredient satisfies, including ancestors.

        If ingredient is "Tanqueray London Dry Gin" with category "London Dry Gin",
        this returns: [London Dry Gin, Gin, Spirit] (all ancestors).
        """
        ancestor_ids = IngredientCategoryAncestor.objects.filter(
            category__in=self.categories.all()
        ).values_list("ancestor_id", flat=True)
        return IngredientCategory.objects.filter(id__in=ancestor_ids).distinct()


class IngredientCategorySuggestion(models.Model):
    """
    Stores LLM-suggested category assignments for admin review.

    Separate from Ingredient.categories to enable approval workflow.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    ingredient = models.ForeignKey(
        Ingredient,
        on_delete=models.CASCADE,
        related_name="category_suggestions",
    )
    suggested_category = models.ForeignKey(
        IngredientCategory,
        on_delete=models.CASCADE,
        related_name="suggestions",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    confidence = models.FloatField(
        help_text="LLM confidence score (0-1)",
    )
    reasoning = models.TextField(
        blank=True,
        help_text="LLM's reasoning for this suggestion",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["ingredient", "suggested_category"],
                condition=models.Q(status="pending"),
                name="unique_pending_suggestion",
            )
        ]

    def __str__(self):
        return f"{self.ingredient.name} -> {self.suggested_category.name}"

    def approve(self, user=None):
        """Apply the suggestion to the ingredient."""
        self.ingredient.categories.add(self.suggested_category)
        self.ingredient.needs_categorization = False
        self.ingredient.save()
        self.status = self.Status.APPROVED
        self.reviewed_at = timezone.now()
        self.reviewed_by = user
        self.save()

    def reject(self, user=None):
        """Reject the suggestion without applying."""
        self.status = self.Status.REJECTED
        self.reviewed_at = timezone.now()
        self.reviewed_by = user
        self.save()
