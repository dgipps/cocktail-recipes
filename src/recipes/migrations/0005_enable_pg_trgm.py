"""Enable PostgreSQL trigram extension for fuzzy search."""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0004_add_raw_ocr_text"),
    ]

    operations = [
        TrigramExtension(),
    ]
