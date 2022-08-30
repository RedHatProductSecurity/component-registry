# Generated by Django 3.2.15 on 2022-08-29 18:23

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_get_or_create_unique_constraints"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="componentnode",
            name="unique_cnode_get_or_create",
        ),
        migrations.RemoveConstraint(
            model_name="productnode",
            name="unique_pnode_get_or_create",
        ),
        migrations.AddConstraint(
            model_name="componentnode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("parent__isnull", False)),
                fields=("type", "parent", "purl"),
                name="unique_cnode_get_or_create",
            ),
        ),
        migrations.AddConstraint(
            model_name="componentnode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("parent__isnull", True)),
                fields=("type", "purl"),
                name="unique_cnode_get_or_create_for_null_parent",
            ),
        ),
        migrations.AddConstraint(
            model_name="productnode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("parent__isnull", False)),
                fields=("object_id", "parent"),
                name="unique_pnode_get_or_create",
            ),
        ),
        migrations.AddConstraint(
            model_name="productnode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("parent__isnull", True)),
                fields=("object_id",),
                name="unique_pnode_get_or_create_for_null_parent",
            ),
        ),
    ]
