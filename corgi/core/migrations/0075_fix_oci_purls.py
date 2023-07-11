from django.db import IntegrityError, migrations, transaction
from django.db.models import Value, functions


def fix_oci_purls(apps, schema_editor):
    """Remove "redhat/" namespace from all "pkg:oci/" purls"""
    Component = apps.get_model("core", "Component")
    ComponentNode = apps.get_model("core", "ComponentNode")

    oci_purl = "pkg:oci/"
    redhat_oci_purl = f"{oci_purl}redhat/"

    Component.objects.filter(purl__startswith=redhat_oci_purl).update(
        purl=functions.Replace("purl", Value(redhat_oci_purl), Value(oci_purl))
    )
    for node in ComponentNode.objects.filter(purl__startswith=redhat_oci_purl).iterator():
        # Can't do .update() here since some type + parent combos have two nodes
        # One node purl starts with "pkg:oci/name" and one starts with "pkg:oci/redhat/name"
        # type + parent + purl combos must be unique, so we can't remove redhat/ from the 2nd purl
        # just delete the 2nd node with "pkg:oci/redhat/name" purl instead
        # A list of build IDs to reingest has already been added to CORGI-566
        try:
            with transaction.atomic():
                node.purl = node.purl.replace(redhat_oci_purl, oci_purl, 1)
                node.save()
        except IntegrityError:
            node.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0074_remove_cdn_repo_relations"),
    ]

    operations = [migrations.RunPython(fix_oci_purls)]
