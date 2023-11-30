# Generated by Django 3.2.22 on 2023-11-30 04:30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0108_save_container_taxonomy"),
    ]

    operations = [
        migrations.AddField(
            model_name="productnode",
            name="node_type",
            field=models.CharField(
                choices=[("DIRECT", "Direct"), ("INFERRED", "Inferred")],
                default="DIRECT",
                max_length=20,
            ),
        )
    ]
