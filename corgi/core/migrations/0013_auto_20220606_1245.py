# Generated by Django 3.2.13 on 2022-06-06 12:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_auto_20220601_0918"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="ofuri",
            field=models.CharField(default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="productstream",
            name="ofuri",
            field=models.CharField(default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="productvariant",
            name="ofuri",
            field=models.CharField(default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="productversion",
            name="ofuri",
            field=models.CharField(default="", max_length=1024),
        ),
    ]
