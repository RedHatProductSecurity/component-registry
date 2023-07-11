from django.db import migrations
from django.db.models import Value, functions


def fix_oci_purls(apps, schema_editor):
    """Remove "redhat/" namespace from all "pkg:oci/" purls"""
    Component = apps.get_model("core", "Component")

    oci_purl = "pkg:oci/"
    redhat_oci_purl = f"{oci_purl}redhat/"

    Component.objects.filter(purl__startswith=redhat_oci_purl).update(
        purl=functions.Replace("purl", Value(redhat_oci_purl), Value(oci_purl))
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0074_remove_cdn_repo_relations"),
    ]

    operations = [migrations.RunPython(fix_oci_purls)]
