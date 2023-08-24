# Generated by Django 3.2.18 on 2023-08-24 21:13

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0083_set_build_fk_relations"),
    ]

    operations = [
        migrations.AlterField(
            model_name="component",
            name="software_build",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="components",
                to="core.softwarebuild",
            ),
        ),
    ]
