from django.db import migrations
from django.db.models import F, Q

# Must use non-historical model which supports cnodes
from corgi.core.models import Component


def find_duplicate_remote_source_components(apps, schema_editor) -> None:
    """Find duplicate Maven, NPM, PyPI, etc. nodes with bad purls"""
    ComponentNode = apps.get_model("core", "ComponentNode")

    # We could do this without the for loop, but checking type / arch pairs individually
    # means the query hits an index and should be faster
    for component_type in Component.REMOTE_SOURCE_COMPONENT_TYPES:
        if component_type == "GOLANG":
            # Too many components to check without crashing the DB
            continue
        # All remote-source component types use "noarch"
        components_for_type = Component.objects.filter(type=component_type, arch="noarch")

        # Fix node purls which don't match their linked component
        for component in components_for_type.filter(~Q(cnodes__purl=F("purl"))).iterator():
            # There are too many reasons why a purl may differ between a component and its node
            # So we can't just directly update to the correct value like we did in other migrations
            # Instead, try to fix each node's purl atomically, but handle duplicates if any
            # TODO: Recheck all of this, verify data in stage
            for bad_node in component.cnodes.exclude(purl=component.purl).iterator():
                bad_node_qs = ComponentNode.objects.filter(
                    type=bad_node.type, parent=bad_node.parent, purl=bad_node.purl
                )
                if ComponentNode.objects.filter(
                    type=bad_node.type, parent=bad_node.parent, purl=component.purl
                ).exists():
                    # if a duplicate exists with the good purl, delete the node with the bad purl
                    bad_node_qs.delete()
                else:
                    # No duplicate exists, so we can just fix the bad node's purl directly
                    bad_node_qs.update(purl=component.purl)


class Migration(migrations.Migration):
    # Code above should be safe to run outside a transaction / already atomic
    # and will take forever, so we need to pick up where we left off after timeouts
    atomic = False
    dependencies = [
        ("core", "0102_fix_duplicate_rpms"),
    ]

    operations = [
        migrations.RunPython(find_duplicate_remote_source_components),
    ]
