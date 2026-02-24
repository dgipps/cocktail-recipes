from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recipes", "0008_replace_recipe_fk_with_recipes_m2m"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipe",
            name="glassware",
            field=models.CharField(
                blank=True,
                choices=[
                    ("highball_ice", "Highball (ice)"),
                    ("highball_straw", "Highball (straw)"),
                    ("julep_tin", "Julep Tin"),
                    ("coupe", "Coupe"),
                    ("coupe_citrus", "Coupe (citrus)"),
                    ("rocks_big_ice", "Rocks (big ice)"),
                    ("rocks_small_ice", "Rocks (small ice)"),
                    ("nick_nora", "Nick & Nora"),
                    ("tiki", "Tiki"),
                ],
                help_text="Serving vessel (used for icon on recipe cards)",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="recipeimport",
            name="suggested_glassware",
            field=models.CharField(
                blank=True,
                choices=[
                    ("highball_ice", "Highball (ice)"),
                    ("highball_straw", "Highball (straw)"),
                    ("julep_tin", "Julep Tin"),
                    ("coupe", "Coupe"),
                    ("coupe_citrus", "Coupe (citrus)"),
                    ("rocks_big_ice", "Rocks (big ice)"),
                    ("rocks_small_ice", "Rocks (small ice)"),
                    ("nick_nora", "Nick & Nora"),
                    ("tiki", "Tiki"),
                ],
                help_text="LLM-suggested glassware (editable before approval)",
                max_length=20,
            ),
        ),
    ]
