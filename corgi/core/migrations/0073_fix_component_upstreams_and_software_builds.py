# Generated by Django 3.2.18 on 2023-06-26 16:56

from django.db import migrations, models

from corgi.core.constants import ROOT_COMPONENTS_CONDITION


def unset_software_build_for_non_root_components(apps, schema_editor):
    """Only set SoftwareBuild for root components
    This makes deleting a build in Corgi (when deleted in Brew) simpler
    We can avoid checking which child components are linked to the build
    and if they are also provided by other builds"""
    Component = apps.get_model("core", "Component")

    Component.objects.exclude(ROOT_COMPONENTS_CONDITION).exclude(type="RPMMOD").exclude(
        software_build__isnull=True
    ).update(software_build=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0072_productcomponentrelation_sb_fk_data"),
    ]

    operations = [
        migrations.AlterField(
            model_name="component",
            name="upstreams",
            field=models.ManyToManyField(related_name="downstreams", to="core.Component"),
        ),
        migrations.RunPython(unset_software_build_for_non_root_components),
    ]
