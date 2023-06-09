# Generated by Django 3.2.18 on 2023-06-06 15:42

from django.db import migrations
from django.db.models import F, Value, functions


def fix_rpm_filenames(apps, schema_editor):
    """Set filenames for RPMs using the NEVRA"""
    Component = apps.get_model("core", "Component")

    # Filenames for non-RPM components are set with data from build system / meta_attr
    # Filenames for RPMs should just be the NEVRA plus ".rpm"
    Component.objects.filter(type="RPM").update(
        filename=functions.Concat(F("nevra"), Value(".rpm"))
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0068_fix_missing_go_component_type"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="component",
            name="release_arr",
        ),
        migrations.RemoveField(
            model_name="component",
            name="version_arr",
        ),
        migrations.RunPython(fix_rpm_filenames),
    ]