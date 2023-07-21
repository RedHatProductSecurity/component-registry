# Generated by Django 3.2.18 on 2023-07-13 13:52

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0076_productstream_exclude_components"),
    ]

    # Remove and add back the channels field on Component to clear all 300 million entries
    # Component.channels.through.objects.get_queryset().delete() is too slow
    # We don't want any of the current entries, but we should keep the field
    # We can report which channels ship a component in the future
    # once we find a way to collect this information (CORGI-298)
    operations = [
        migrations.RemoveField(
            model_name="component",
            name="channels",
        ),
        migrations.AddField(
            model_name="component",
            name="channels",
            field=models.ManyToManyField(to="core.Channel"),
        ),
    ]