"""Seed predefined TasteTag records."""

from django.db import migrations

TASTE_TAGS = [
    ("citrusy", "Citrusy"),
    ("smoky", "Smoky"),
    ("strong", "Strong"),
    ("low_abv", "Low ABV"),
    ("bitter", "Bitter"),
    ("refreshing", "Refreshing"),
    ("sweet", "Sweet"),
]


def seed_taste_tags(apps, schema_editor):
    TasteTag = apps.get_model("recipes", "TasteTag")
    for slug, name in TASTE_TAGS:
        TasteTag.objects.get_or_create(slug=slug, defaults={"name": name})


def remove_taste_tags(apps, schema_editor):
    TasteTag = apps.get_model("recipes", "TasteTag")
    TasteTag.objects.filter(slug__in=[slug for slug, _ in TASTE_TAGS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0006_tastetag"),
    ]

    operations = [
        migrations.RunPython(seed_taste_tags, reverse_code=remove_taste_tags),
    ]
