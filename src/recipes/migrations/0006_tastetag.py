"""Add TasteTag model and M2M fields on Recipe and RecipeImport."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0005_enable_pg_trgm"),
    ]

    operations = [
        migrations.CreateModel(
            name="TasteTag",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("slug", models.SlugField(unique=True)),
                ("name", models.CharField(max_length=50, unique=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="recipe",
            name="taste_tags",
            field=models.ManyToManyField(
                blank=True,
                related_name="recipes",
                to="recipes.tastetag",
            ),
        ),
        migrations.AddField(
            model_name="recipeimport",
            name="suggested_taste_tags",
            field=models.ManyToManyField(
                blank=True,
                related_name="recipe_imports",
                to="recipes.tastetag",
            ),
        ),
    ]
