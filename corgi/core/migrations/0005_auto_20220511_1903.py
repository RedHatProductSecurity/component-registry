# Generated by Django 3.2.13 on 2022-05-11 19:03

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_component_core_compon_name_ffe890_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="component",
            name="nevra",
            field=models.CharField(default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="component",
            name="nvr",
            field=models.CharField(default="", max_length=1024),
        ),
    ]
