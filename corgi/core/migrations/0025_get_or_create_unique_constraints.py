# Generated by Django 3.2.15 on 2022-08-26 22:42

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_pnode_cnode_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="componentnode",
            index=models.Index(
                fields=["type", "parent", "purl"], name="core_compon_type_1f6187_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productnode",
            index=models.Index(
                fields=["object_id", "parent"], name="core_produc_object__d4eb72_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="componentnode",
            constraint=models.UniqueConstraint(
                fields=("type", "parent", "purl"), name="unique_cnode_get_or_create"
            ),
        ),
        migrations.AddConstraint(
            model_name="productnode",
            constraint=models.UniqueConstraint(
                fields=("object_id", "parent"), name="unique_pnode_get_or_create"
            ),
        ),
    ]
