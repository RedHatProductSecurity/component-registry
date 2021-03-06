# Generated by Django 3.2.13 on 2022-06-14 19:48

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_auto_20220608_1047"),
    ]

    operations = [
        migrations.AddField(
            model_name="component",
            name="data_report",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=200), default=list, size=None
            ),
        ),
        migrations.AddField(
            model_name="component",
            name="data_score",
            field=models.IntegerField(default=0),
        ),
    ]
