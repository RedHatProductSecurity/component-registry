# Generated by Django 3.2.15 on 2022-09-02 07:49

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_get_or_create_unique_constraints_for_null_parent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="componentnode",
            name="tree_id",
            field=models.PositiveIntegerField(db_index=True, editable=False),
        ),
        migrations.AlterField(
            model_name="productnode",
            name="tree_id",
            field=models.PositiveIntegerField(db_index=True, editable=False),
        ),
    ]
