# Cocktails App - Data Model Planning Document

## Overview

This document outlines the proposed data models for the Cocktails application, focusing on:
1. Hierarchical ingredient taxonomy
2. Recipe storage
3. Query patterns for ingredient matching at various granularity levels

## Core Design Challenge

Ingredients exist at multiple levels of granularity:
```
Base Spirit → Gin → London Dry Gin → Tanqueray London Dry Gin
Bitters → Aromatic Bitters → Angostura Bitters
```

Recipes may specify ingredients at ANY of these levels:
- "20th Century" calls for `GIN (LONDON DRY)` - any London Dry will work
- "Aviation" calls for `GIN (PLYMOUTH)` - specifically Plymouth-style
- Some recipes might just call for "Gin" (any style)
- Others specify exact brands

---

## Proposed Data Models

### Option A: Adjacency List (Self-Referential)

```python
class IngredientCategory(models.Model):
    name = models.CharField(max_length=100)  # "London Dry Gin"
    parent = models.ForeignKey('self', null=True, blank=True,
                               on_delete=models.CASCADE,
                               related_name='children')

    # Denormalized for query performance
    depth = models.PositiveIntegerField(default=0)  # 0=root, 1, 2, 3...

class Ingredient(models.Model):
    """Specific products like 'Tanqueray London Dry Gin'"""
    name = models.CharField(max_length=200)
    category = models.ForeignKey(IngredientCategory, on_delete=models.PROTECT)
```

**Pros:**
- Simple, standard Django pattern
- Easy to add/modify hierarchy

**Cons:**
- Queries for "all descendants" require recursive CTEs or multiple queries
- "Find all recipes I can make with Tanqueray" requires walking up tree

---

### Option B: Materialized Path

```python
class IngredientCategory(models.Model):
    name = models.CharField(max_length=100)
    path = models.CharField(max_length=500, db_index=True)
    # e.g., "spirit/gin/london_dry"
    depth = models.PositiveIntegerField()

class Ingredient(models.Model):
    name = models.CharField(max_length=200)
    category = models.ForeignKey(IngredientCategory, on_delete=models.PROTECT)
```

**Query: "All gins"**
```python
IngredientCategory.objects.filter(path__startswith='spirit/gin/')
```

**Pros:**
- Single query for all descendants
- Simple string comparison

**Cons:**
- Path updates cascade through children
- Path length limits depth

---

### Option C: Closure Table (Recommended)

```python
class IngredientCategory(models.Model):
    """
    Hierarchical categories like: Spirit > Liqueur > Amaro > Aperitivo
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

class IngredientCategoryAncestor(models.Model):
    """
    Stores ALL ancestor relationships for efficient hierarchy queries.
    E.g., if Aperitivo's parent is Amaro, parent is Liqueur, parent is Spirit:
    - (Aperitivo, Aperitivo, 0)  # self
    - (Aperitivo, Amaro, 1)      # parent
    - (Aperitivo, Liqueur, 2)    # grandparent
    - (Aperitivo, Spirit, 3)     # great-grandparent
    """
    category = models.ForeignKey(IngredientCategory,
                                 on_delete=models.CASCADE,
                                 related_name='ancestor_links')
    ancestor = models.ForeignKey(IngredientCategory,
                                 on_delete=models.CASCADE,
                                 related_name='descendant_links')
    depth = models.PositiveIntegerField()  # 0 = self, 1 = parent, etc.

    class Meta:
        unique_together = ['category', 'ancestor']

class Ingredient(models.Model):
    """
    Specific products like 'Campari', 'Tanqueray London Dry Gin'.

    KEY INSIGHT: Ingredients can belong to MULTIPLE categories.
    - Campari: [Amaro, Aperitivo, Bitter Liqueur]
    - Chartreuse Green: [Herbal Liqueur, Digestif]
    """
    name = models.CharField(max_length=200, unique=True)
    categories = models.ManyToManyField(IngredientCategory,
                                        related_name='ingredients')
    description = models.TextField(blank=True)
```

**Pros:**
- Single query for ancestors OR descendants of any category
- Ingredients can belong to multiple category trees (Campari = Amaro + Aperitivo)
- "What categories does Tanqueray satisfy?" → union of all its categories' ancestors
- Flexible and performant

**Cons:**
- More rows in closure table
- Hierarchy changes require updating closure table
- Many-to-many adds slight complexity

---

## Recipe Models

```python
class Recipe(models.Model):
    name = models.CharField(max_length=200)
    source = models.CharField(max_length=200, blank=True)  # "Death & Co"
    page = models.PositiveIntegerField(null=True, blank=True)
    method = models.TextField(blank=True)
    garnish = models.TextField(blank=True)
    notes = models.TextField(blank=True)

class RecipeIngredient(models.Model):
    """
    Links recipes to their ingredients.

    Design decision: Store the SPECIFIC ingredient from the source recipe.
    The category hierarchy enables substitution queries.

    Example: "20th Century" calls for "Beefeater London Dry Gin"
    - ingredient = Ingredient(name="Beefeater London Dry Gin")
    - Beefeater's categories include "London Dry Gin"
    - London Dry Gin's ancestors include "Gin", "Spirit"
    - Query "recipes using gin" will find this recipe
    """
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE,
                               related_name='ingredients')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT)

    amount = models.CharField(max_length=50, blank=True)  # "2 oz", "dash"
    unit = models.CharField(max_length=20, blank=True)    # "oz", "dash", "barspoon"
    order = models.PositiveIntegerField(default=0)
    optional = models.BooleanField(default=False)

    class Meta:
        ordering = ['order']
```

---

## Query Patterns

### 1. "What can I make with what I have?"

User has: `[Tanqueray London Dry Gin, Angostura Bitters, Lemon Juice, Simple Syrup]`

```python
# For each ingredient the user has, find all categories it satisfies
# (via many-to-many categories + closure table ancestors)

user_ingredients = Ingredient.objects.filter(name__in=[...])

# Get all category IDs satisfied by user's ingredients
satisfied_category_ids = set()
for ing in user_ingredients:
    for cat in ing.categories.all():
        # Add the category and all its ancestors
        ancestor_ids = cat.ancestor_links.values_list('ancestor_id', flat=True)
        satisfied_category_ids.update(ancestor_ids)

# Find recipes where ALL ingredients can be satisfied
# This is the complex query - will need careful design
# Approach: annotate recipes with count of satisfiable ingredients
```

### 2. "What recipes use Gin?"

```python
gin_category = IngredientCategory.objects.get(slug='gin')

# Get gin and all its descendants (London Dry, Plymouth, etc.)
gin_and_descendants = gin_category.descendant_links.values_list('category_id', flat=True)

# Find all ingredients that belong to gin or any gin subcategory
gin_ingredients = Ingredient.objects.filter(
    categories__in=gin_and_descendants
).distinct()

# Find recipes using those ingredients
recipes = Recipe.objects.filter(
    ingredients__ingredient__in=gin_ingredients
).distinct()
```

### 3. "What do I need to make a Negroni?"

```python
recipe = Recipe.objects.get(name='Negroni')
for ri in recipe.ingredients.select_related('ingredient').all():
    ingredient = ri.ingredient
    categories = ingredient.categories.all()
    print(f"{ri.amount} {ingredient.name}")
    print(f"  Categories: {[c.name for c in categories]}")
```

### 4. "Can I substitute X for Y in this recipe?"

```python
# Future feature: Check if X and Y share common ancestor categories
# E.g., Can I use Plymouth Gin instead of Beefeater?
# Both → London Dry Gin → Gin → Spirit (same ancestors = compatible)
```

---

## Ingredient Hierarchy (from CSV analysis)

Based on the Death & Co data, here's the proposed hierarchy structure:

```
SPIRITS
├── GIN
│   ├── London Dry
│   ├── Plymouth
│   ├── Genever
│   ├── Old Tom
│   ├── American
│   └── Sloe
├── WHISKEY
│   ├── Bourbon
│   ├── Rye
│   ├── Scotch
│   │   ├── Blended
│   │   ├── Campbeltown
│   │   ├── Highlands
│   │   ├── Islay
│   │   └── Speyside
│   ├── Irish
│   ├── Japanese
│   └── Other (Wheat, Oat)
├── RUM
│   ├── Spanish (Light)
│   ├── Spanish White
│   ├── English
│   ├── English White
│   ├── Jamaican
│   ├── Jamaican White
│   ├── Demerara
│   ├── Demerara White
│   ├── Demerara Overproof
│   ├── Agricole Blanc
│   └── Agricole Ambre
├── AGAVE
│   ├── Tequila Blanco
│   ├── Tequila Reposado
│   ├── Tequila Anejo
│   └── Mezcal
├── BRANDY
│   ├── Grape (Cognac, etc.)
│   ├── Apple
│   ├── Pear
│   ├── Cherry
│   └── Pisco
├── VODKA
│   └── Flavored (Chocolate, etc.)
└── OTHER SPIRITS
    ├── Absinthe
    ├── Aquavit
    ├── Cachaça
    └── Batavia Arrack

LIQUEURS
├── Amaro
│   ├── Mild
│   ├── Medium
│   ├── Aperitivo
│   ├── Fernet
│   └── Cynar
├── Chartreuse
│   ├── Green
│   └── Yellow
├── Cherry (Maraschino, Heering)
├── Orange
├── Coffee
├── Crème de Cacao
├── Crème Yvette
└── [many more...]

FORTIFIED WINES
├── Vermouth
│   ├── Dry
│   ├── Sweet
│   └── Blanc
├── Sherry
│   ├── Fino
│   ├── Manzanilla
│   ├── Amontillado
│   ├── Oloroso
│   └── Cream
├── Port
└── Aperitif Wines (Lillet, Cocchi, etc.)

BITTERS
├── Aromatic
├── Orange
├── Citrus (Grapefruit, Lemon)
├── Spice (Allspice, Chocolate)
├── Herbal (Celery, Lavender)
└── [specialty bitters]

SWEETENERS
├── Syrups
│   ├── Simple
│   ├── Rich
│   ├── Demerara
│   ├── Honey
│   ├── Maple
│   ├── Orgeat
│   ├── Grenadine
│   └── [flavored syrups...]
├── Sugar (cube, granulated)
└── Agave Nectar

CITRUS
├── Juices
│   ├── Lemon
│   ├── Lime
│   ├── Orange
│   ├── Grapefruit
│   └── [other citrus...]
├── Cordials (e.g., Lime Cordial - also in Sweeteners!)
└── Twists/Peels (garnish, oils)

SODAS & CARBONATED
├── Club Soda
├── Tonic
├── Ginger Beer
├── Ginger Ale
├── Cola
└── [specialty sodas...]

FOAMERS
├── Egg White
├── Aquafaba
└── Foaming Agents

FATS & RICHNESS
├── Cream (heavy, light)
├── Egg Yolk
├── Whole Egg
├── Coconut Cream
└── Butter (for fat-washed spirits)

OTHER MIXERS
├── Fruit (muddled, garnish)
├── Vegetables (cucumber, celery)
├── Herbs (mint, basil)
└── Spices (nutmeg, cinnamon)
```

---

## Design Decisions (Confirmed)

1. **Ingredient-Category relationship**: Many-to-Many
   - Campari can belong to [Amaro, Aperitivo, Bitter Liqueur]
   - Chartreuse can belong to [Herbal Liqueur, Digestif]
   - This enables flexible categorization and querying

2. **Recipe storage**: Store specific ingredients from source
   - "Beefeater London Dry Gin" stored as the ingredient
   - Category hierarchy enables substitution queries
   - Preserves original recipe fidelity

3. **Explicit substitutions**: Later feature
   - For now, rely on category hierarchy for "compatible" ingredients
   - Future: Add SubstitutionRule model for non-hierarchical substitutes

## Open Questions

1. **Hierarchy depth**: Flexible - not enforced. Some branches may be 2 levels, others 4+.

2. **Multi-functional ingredients**: The M2M design handles these elegantly:
   - **Lime Cordial** → belongs to BOTH [Citrus > Cordials] AND [Sweeteners]
   - Could potentially substitute for lime juice AND simple syrup in some recipes
   - Query pattern: "Recipes where Lime Cordial satisfies multiple ingredient requirements"

3. **User inventory with "fuzz" matching** (future feature):
   - User stores: "I have Tanqueray London Dry Gin"
   - **Exact match**: Recipes calling for "Tanqueray London Dry Gin"
   - **Category match**: Recipes calling for "London Dry Gin" (any brand)
   - **Broader match**: Recipes calling for "Gin" (any style)
   - **Broadest match**: Recipes calling for "Spirit" (any base spirit)

   The closure table enables all these "fuzz levels" with the same query pattern,
   just varying how far up the ancestor chain we look for matches.

4. **Multi-ingredient substitution**: Lime Cordial example
   - If a recipe calls for [Lime Juice, Simple Syrup]
   - And user has [Lime Cordial]
   - Could Lime Cordial satisfy BOTH requirements?
   - This is an advanced query pattern for future consideration

---

## Recommended Approach

**Use Closure Table (Option C) with Many-to-Many Ingredient-Category** because:
1. Queries like "find all descendants of Gin" are single queries
2. Queries like "what categories does Tanqueray satisfy" are single queries
3. Ingredients can belong to multiple category hierarchies (Campari = Amaro + Aperitivo)
4. These are the core queries for recipe matching
5. Hierarchy changes are infrequent after initial data load

## Summary: Final Model

```python
# Category hierarchy with closure table for efficient ancestor/descendant queries
IngredientCategory (name, slug)
IngredientCategoryAncestor (category, ancestor, depth)

# Ingredients with many-to-many category membership
Ingredient (name, description, categories M2M)

# Recipes with specific ingredients (hierarchy enables substitution)
Recipe (name, source, page, method, garnish, notes)
RecipeIngredient (recipe, ingredient, amount, unit, order, optional)
```

## Phase 1 Next Steps (Documentation Only)

1. ✅ Propose data models (this document)
2. ⬜ Review and refine hierarchy structure
3. ⬜ Identify edge cases from CSV data
4. ⬜ Finalize model design
5. ⬜ Plan data ingestion approach

## Phase 2 (Future - Code Implementation)

1. Create Django app `ingredients`
2. Create Django app `recipes`
3. Implement models
4. Create admin interface
5. Build CSV ingestion management command
