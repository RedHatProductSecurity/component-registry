# Generated by Django 3.2.18 on 2023-06-23 14:51

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0077_clear_component_channels"),
    ]

    operations = [
        migrations.AddField(
            model_name="componentnode",
            name="component",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cnodes",
                to="core.component",
            ),
        ),
        migrations.AddIndex(
            model_name="componentnode",
            index=models.Index(
                fields=["component", "parent"], name="core_compon_compone_aa17a4_idx"
            ),
        ),
    ]
