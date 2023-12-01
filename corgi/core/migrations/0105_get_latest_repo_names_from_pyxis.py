# Generated by Django 3.2.20 on 2023-11-05 23:59

from django.db import migrations

from corgi.core.models import Component, SoftwareBuild
from corgi.tasks.pyxis import slow_update_name_for_container_from_pyxis


def get_latest_repo_names_from_pyxis(apps, schema_editor) -> None:
    brew_container_nvrs = (
        Component.objects.filter(
            type=Component.Type.CONTAINER_IMAGE, software_build__build_type=SoftwareBuild.Type.BREW
        )
        # These containers should have pyxis names set since they were processed after we updated
        # the brew collector to check pyxis for names which differ from Brew
        .exclude(meta_attr__has_key="name_from_label_raw")
        # This allows the migration to be re-run in case of failure.
        .exclude(meta_attr__has_key="name_checked")
        .values_list("nvr", flat=True)
        .iterator()
    )
    for nvr in brew_container_nvrs:
        # Schedule these tasks at the back of the queue
        # so bulk data loading doesn't block more important daily tasks
        slow_update_name_for_container_from_pyxis.apply_async(args=(nvr,), priority=9)

        # Set this so they are not re-processed next run
        containers_to_update = Component.objects.filter(
            type=Component.Type.CONTAINER_IMAGE,
            software_build__build_type=SoftwareBuild.Type.BREW,
            nvr=nvr,
        )
        checked_containers = []
        for container in containers_to_update:
            container.meta_attr["name_checked"] = True
            container.save()
            checked_containers.append(container)
        Component.objects.bulk_update(checked_containers, ["meta_attr"])

    checked_containers = []
    for container in Component.objects.filter(meta_attr__name_checked=True):
        del container.meta_attr["name_checked"]
        checked_containers.append(container)
    Component.objects.bulk_update(checked_containers, ["meta_attr"])


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("core", "0104_fix_root_components_condition")]

    operations = [
        migrations.RunPython(get_latest_repo_names_from_pyxis),
    ]
