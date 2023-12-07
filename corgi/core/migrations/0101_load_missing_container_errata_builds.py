from django.db import migrations

from corgi.tasks.errata_tool import (
    slow_load_stream_errata as current_load_stream_errata,
)
from corgi.tasks.tagging import NO_MANIFEST_TAG


def load_missing_container_errata_build(apps, schema_editor):
    ProductStream = apps.get_model("core", "ProductStream")

    for stream_name in (
        ProductStream.objects.exclude(components__isnull=True)
        .exclude(tags__name=NO_MANIFEST_TAG)
        .exclude(brew_tags={})
        .values_list("name", flat=True)
    ):
        current_load_stream_errata.delay(stream_name, container_only=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0100_fix_duplicate_sbomer_components"),
    ]

    operations = [
        migrations.RunPython(load_missing_container_errata_build),
    ]
