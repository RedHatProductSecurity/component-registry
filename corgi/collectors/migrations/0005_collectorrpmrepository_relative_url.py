# Generated by Django 3.2.15 on 2022-10-20 19:49

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("collectors", "0004_collector_rhelmodules_pulp"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectorrpmrepository",
            name="relative_url",
            field=models.CharField(default="", max_length=200),
        ),
    ]
