# Generated by Django 3.2.12 on 2022-05-11 00:39

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_auto_20220505_1745"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="component",
            index=models.Index(
                fields=["name", "type", "arch", "version", "release"],
                name="core_compon_name_ffe890_idx",
            ),
        ),
    ]
