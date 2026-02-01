"""Ollama integration for parsing recipe images."""

import base64
import json
import logging
import re
from pathlib import Path

import httpx
import ollama
from django.conf import settings

logger = logging.getLogger(__name__)

# System message for better instruction following
SYSTEM_MESSAGE = """\
You are an OCR assistant. Read and transcribe text from images accurately.
Only transcribe what you can actually see - never guess or invent content.
"""

RECIPE_PARSE_PROMPT = """\
Read this cocktail recipe page. List every recipe you can see.

For each recipe, write:
RECIPE: [NAME]
PAGE: [number]
- [amount] [unit] [ingredient]
METHOD: [instructions]
GARNISH: [garnish]
---

IMPORTANT for amounts:
- Write "1.5 oz" not "1 1/2 oz" (convert fractions to decimals)
- Write "0.75 oz" not "3/4 oz"
- Write "0.5 oz" not "1/2 oz"
- Write "0.25 oz" not "1/4 oz"
- Write "tsp" not "teaspoon", "oz" not "ounce"

Keep ingredient on ONE LINE. Example: "- 1.5 oz Beefeater Gin"

Read the actual text in the image carefully."""

# DeepSeek-OCR specific prompt - keep it simple, model is sensitive to length
DEEPSEEK_OCR_PROMPT = "<|grounding|>OCR this image."


class ParseError(Exception):
    """Raised when image parsing fails."""

    pass


def parse_recipe_image(image_path: str | Path) -> dict:
    """
    Use Ollama vision model to extract recipe data from image.

    Args:
        image_path: Path to the image file.

    Returns:
        Parsed recipe data dict with structure:
        {
            "recipes": [
                {
                    "name": str,
                    "page": int | None,
                    "ingredients": [{"amount": str, "unit": str, "name": str}],
                    "method": str,
                    "garnish": str | None
                }
            ]
        }

    Raises:
        ParseError: If parsing fails or returns invalid data.
    """
    image_path = Path(image_path)

    if not image_path.exists():
        raise ParseError(f"Image file not found: {image_path}")

    host = getattr(settings, "OLLAMA_HOST", "http://localhost:11434")
    model = getattr(settings, "OLLAMA_MODEL", "deepseek-ocr")

    logger.info(f"Parsing image with Ollama model {model}: {image_path}")

    # Use model-specific prompts
    if "deepseek-ocr" in model:
        prompt = DEEPSEEK_OCR_PROMPT
        system_msg = ""
    else:
        prompt = RECIPE_PARSE_PROMPT
        system_msg = SYSTEM_MESSAGE

    try:
        # DeepSeek-OCR needs direct API call (ollama lib has issues)
        if "deepseek-ocr" in model:
            with open(image_path, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode()

            with httpx.Client(timeout=180) as http_client:
                response = http_client.post(
                    f"{host}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "images": [img_base64],
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 4096},
                    },
                )
                response.raise_for_status()
                content = response.json()["response"]
        else:
            client = ollama.Client(host=host)
            # Build messages for chat API
            messages = []
            if system_msg:
                messages.append({"role": "system", "content": system_msg})
            messages.append({
                "role": "user",
                "content": prompt,
                "images": [str(image_path)],
            })

            response = client.chat(
                model=model,
                messages=messages,
                options={
                    "temperature": 0.1,
                    "num_predict": 4096,
                },
            )
            content = response["message"]["content"]
    except ollama.ResponseError as e:
        raise ParseError(f"Ollama API error: {e}") from e
    except httpx.HTTPStatusError as e:
        raise ParseError(f"Ollama HTTP error: {e}") from e
    except Exception as e:
        raise ParseError(f"Failed to connect to Ollama: {e}") from e

    logger.info(f"Ollama raw response ({len(content)} chars): {content[:200]}...")
    logger.debug(f"Ollama full response: {content}")

    # Try to parse - check for deepseek grounding format first
    if "<|ref|>" in content:
        parsed = _parse_deepseek_ocr(content)
    else:
        try:
            parsed = _extract_json(content)
        except ParseError:
            # Try text format parsing
            parsed = _parse_text_format(content)

    # Validate structure
    if not isinstance(parsed, dict):
        raise ParseError(f"Expected dict, got {type(parsed).__name__}")

    if "recipes" not in parsed:
        raise ParseError("Response missing 'recipes' key")

    if not isinstance(parsed["recipes"], list):
        raise ParseError("'recipes' must be a list")

    # Validate each recipe
    for i, recipe in enumerate(parsed["recipes"]):
        _validate_recipe(recipe, i)

    return parsed


def _extract_json(content: str) -> dict:
    """Extract JSON from LLM response, handling various formats."""
    original_content = content

    # Try to find JSON in code blocks
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if json_match:
        content = json_match.group(1)

    # Strip whitespace
    content = content.strip()

    # Try direct parse first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object in the content
    # Look for {"recipes": pattern
    json_match = re.search(r'(\{"recipes"\s*:\s*\[[\s\S]*\]\s*\})', content)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object starting with {
    start = content.find("{")
    if start != -1:
        # Find matching closing brace
        depth = 0
        for i, char in enumerate(content[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(content[start : i + 1])
                    except json.JSONDecodeError:
                        break

    msg = f"Could not extract JSON from response.\nContent: {original_content[:500]}"
    raise ParseError(msg)


def _parse_deepseek_ocr(content: str) -> dict:
    """Parse DeepSeek-OCR grounding format output."""
    # Extract all text regions with their bounding boxes
    pattern = (
        r"<\|ref\|>([^<]+)<\|/ref\|><\|det\|>"
        r"\[\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\]"
    )
    matches = re.findall(pattern, content)

    if not matches:
        raise ParseError("No text regions found in DeepSeek-OCR output")

    # Convert to list of (text, x1, y1, x2, y2)
    regions = [
        (text, int(x1), int(y1), int(x2), int(y2))
        for text, x1, y1, x2, y2 in matches
    ]

    # Detect column layout - find midpoint
    x_coords = [r[1] for r in regions]
    midpoint = (min(x_coords) + max(x_coords)) // 2

    # Separate into left (x < midpoint) and right (x >= midpoint) columns
    left_col = [(t, x, y, x2, y2) for t, x, y, x2, y2 in regions if x < midpoint]
    right_col = [(t, x, y, x2, y2) for t, x, y, x2, y2 in regions if x >= midpoint]

    # Sort each column by y position
    left_col.sort(key=lambda r: r[2])
    right_col.sort(key=lambda r: r[2])

    # Process columns separately, right column first (has most recipes)
    all_regions = right_col + left_col

    # Extract page number
    page_num = None
    for text, *_ in all_regions:
        page_match = re.search(r"\|\s*(\d+)\s*$", text)
        if page_match:
            page_num = int(page_match.group(1))
            break

    # Find recipe titles (uppercase names that aren't ingredient parts)
    skip_words = {
        "OUNCE", "OUNCES", "TEASPOON", "TEASPOONS", "TSP", "TBSP",
        "GARNISH", "SHAKE", "STIR", "PAGE", "SPECS", "CLASSIC", "VINTAGE",
        "DE CACAO", "DRY CHAMPAGNE", "THE",
    }
    recipe_titles = []
    for text, x1, y1, _x2, _y2 in all_regions:
        clean = text.strip()
        if (
            clean.isupper()
            and 3 < len(clean) < 25
            and not clean.startswith(("1/", "2/", "3/", "1 ", "2 ", "3 "))
            and not any(skip in clean for skip in skip_words)
        ):
            recipe_titles.append((clean, x1, y1))

    titles = [t[0] for t in recipe_titles]
    logger.info(f"Found {len(recipe_titles)} recipe titles: {titles}")

    # Group regions by recipe based on column and y-position
    recipes = []
    for i, (title, title_x, title_y) in enumerate(recipe_titles):
        # Determine which column this title is in
        title_is_right = title_x >= midpoint
        col_regions = right_col if title_is_right else left_col

        # Find next title in same column
        next_title_y = 9999
        for j in range(i + 1, len(recipe_titles)):
            next_t, next_x, next_y = recipe_titles[j]
            if (next_x >= midpoint) == title_is_right:
                next_title_y = next_y
                break

        recipe = {
            "name": title,
            "page": page_num,
            "ingredients": [],
            "method": "",
            "garnish": None,
        }

        # Find all regions belonging to this recipe in the same column
        for text, _x1, y1, _x2, _y2 in col_regions:
            if title_y < y1 < next_title_y:
                text = text.strip()

                # Check for ingredient line
                ing_match = re.match(
                    r"^(\d+(?:/\d+)?)\s*"
                    r"(OUNCES?|TEASPOONS?|TSP|TBSP|DASH(?:ES)?|DROP(?:S)?|BARSPOON)?\s+"
                    r"(.+)$",
                    text,
                    re.IGNORECASE,
                )
                if ing_match:
                    amount_str, unit, name = ing_match.groups()
                    amount = _convert_fraction(amount_str)
                    unit = _normalize_unit(unit or "")
                    recipe["ingredients"].append({
                        "amount": amount,
                        "unit": unit,
                        "name": _clean_ingredient_name(name),
                    })
                elif text.upper().startswith("GARNISH:"):
                    recipe["garnish"] = text.split(":", 1)[1].strip()
                elif "shake" in text.lower() or "stir" in text.lower():
                    if not recipe["method"]:
                        recipe["method"] = text

        # Add recipe even without ingredients - user can fix in admin
        recipes.append(recipe)

    # Filter to recipes with at least a valid name
    recipes = [r for r in recipes if r["name"] and len(r["name"]) > 2]

    if not recipes:
        # Log more details for debugging
        logger.warning(f"No recipe titles found. Regions: {len(regions)}, "
                      f"Left col: {len(left_col)}, Right col: {len(right_col)}")
        raise ParseError(f"Could not extract recipes from OCR output:\n{content[:500]}")

    return {"recipes": recipes}


def _convert_fraction(amount_str: str) -> str:
    """Convert fraction string to decimal."""
    amount_str = amount_str.strip()
    if "/" in amount_str:
        parts = amount_str.split("/")
        if len(parts) == 2:
            try:
                return str(round(int(parts[0]) / int(parts[1]), 3))
            except (ValueError, ZeroDivisionError):
                pass
    return amount_str


def _normalize_unit(unit: str) -> str:
    """Normalize unit strings."""
    unit = unit.lower().strip()
    if unit in ("ounce", "ounces"):
        return "oz"
    if unit in ("teaspoon", "teaspoons"):
        return "tsp"
    if unit in ("tablespoon", "tablespoons"):
        return "tbsp"
    if unit == "dashes":
        return "dash"
    if unit == "drops":
        return "drop"
    if unit == "barspoons":
        return "barspoon"
    return unit


def _clean_ingredient_name(name: str) -> str:
    """Clean up ingredient name by removing page references etc."""
    # Remove page references like "(page 277)" or "(PAGE 221)"
    name = re.sub(r"\s*\(page\s*\d+\)", "", name, flags=re.IGNORECASE)
    return name.strip()


def _parse_text_format(content: str) -> dict:
    """Parse various text-based recipe formats into JSON structure."""
    recipes = []
    current_recipe = None

    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("–"):  # Skip empty or degenerate lines
            continue

        # Check for recipe start - multiple formats
        # Format 1: "RECIPE: NAME"
        # Format 2: "**Recipe 1: Name**" or "**Name**"
        recipe_match = re.match(
            r"^(?:\*\*)?(?:Recipe\s*\d*:?\s*)?([A-Z][A-Z\s\d]+)(?:\*\*)?$",
            line,
            re.IGNORECASE,
        )
        if line.upper().startswith("RECIPE:"):
            if current_recipe and current_recipe.get("name"):
                recipes.append(current_recipe)
            name = line.split(":", 1)[1].strip().strip("*")
            current_recipe = {
                "name": name.upper(),
                "page": None,
                "ingredients": [],
                "method": "",
                "garnish": None,
            }
        elif recipe_match and not line.upper().startswith(
            ("PAGE", "METHOD", "GARNISH")
        ):
            # Check if this looks like a recipe title (all caps, or markdown bold)
            name = recipe_match.group(1).strip().strip("*")
            if len(name) > 2 and not any(
                name.upper().startswith(x) for x in ["OZ", "TSP", "ML", "DASH"]
            ):
                if current_recipe and current_recipe.get("name"):
                    recipes.append(current_recipe)
                current_recipe = {
                    "name": name.upper(),
                    "page": None,
                    "ingredients": [],
                    "method": "",
                    "garnish": None,
                }
                continue
        if line.upper().startswith("PAGE:") and current_recipe:
            try:
                page_str = line.split(":", 1)[1].strip()
                match = re.search(r"\d+", page_str)
                if match:
                    current_recipe["page"] = int(match.group())
            except (ValueError, AttributeError):
                pass
        elif line.upper().startswith("METHOD:") and current_recipe:
            current_recipe["method"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("GARNISH:") and current_recipe:
            garnish = line.split(":", 1)[1].strip()
            current_recipe["garnish"] = None if garnish.lower() == "none" else garnish
        elif line.startswith("-") and current_recipe:
            # Parse ingredient line: "- 1.5 oz Beefeater London Dry Gin"
            ing_text = line[1:].strip()
            ingredient = _parse_ingredient_line(ing_text)
            if ingredient:
                current_recipe["ingredients"].append(ingredient)
        elif line in ("---", "***"):
            if current_recipe and current_recipe.get("name"):
                recipes.append(current_recipe)
            current_recipe = None

    # Don't forget the last recipe
    if current_recipe and current_recipe.get("name"):
        recipes.append(current_recipe)

    if not recipes:
        raise ParseError(f"Could not parse any recipes from text:\n{content[:500]}")

    return {"recipes": recipes}


def _parse_ingredient_line(text: str) -> dict | None:
    """Parse an ingredient line like '1.5 oz Beefeater London Dry Gin'."""
    # Pattern: amount unit name
    # Examples: "1.5 oz Gin", "2 dashes Angostura", "0.5 tsp Sugar"
    match = re.match(
        r"^([\d.]+)\s*"  # amount (decimal)
        r"(oz|tsp|tbsp|dash|dashes|drop|drops|ml|cl|barspoon|barspoons)?\s*"  # unit
        r"(.+)$",  # name
        text,
        re.IGNORECASE,
    )
    if match:
        amount, unit, name = match.groups()
        return {
            "amount": amount,
            "unit": (unit or "").lower(),
            "name": name.strip(),
        }

    # If no match, try to extract just the name (for items like "Dry Champagne")
    if text and not text[0].isdigit():
        return {"amount": "", "unit": "", "name": text}

    return None


def _validate_recipe(recipe: dict, index: int) -> None:
    """Validate a single recipe dict."""
    if not isinstance(recipe, dict):
        raise ParseError(f"Recipe {index}: expected dict, got {type(recipe).__name__}")

    if "name" not in recipe:
        raise ParseError(f"Recipe {index}: missing 'name'")

    if "ingredients" not in recipe:
        raise ParseError(f"Recipe {index}: missing 'ingredients'")

    if not isinstance(recipe["ingredients"], list):
        raise ParseError(f"Recipe {index}: 'ingredients' must be a list")

    for j, ing in enumerate(recipe["ingredients"]):
        if not isinstance(ing, dict):
            type_name = type(ing).__name__
            raise ParseError(
                f"Recipe {index}, ingredient {j}: expected dict, got {type_name}"
            )
        if "name" not in ing:
            raise ParseError(f"Recipe {index}, ingredient {j}: missing 'name'")
