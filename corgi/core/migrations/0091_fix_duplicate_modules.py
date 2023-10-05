from django.db import migrations

# Must use non-historical model which supports get_descendants()
from corgi.core.models import ComponentNode
from corgi.tasks.brew import slow_fetch_modular_build


def fix_module_data(apps, schema_editor):
    """Clean up duplicate modules / nodes with bad purls
    and ensure the non-duplicated modules / nodes have all the same data"""
    Component = apps.get_model("core", "Component")
    module_purl = "pkg:rpmmod/"
    red_hat_module_purl = f"{module_purl}redhat/"

    # Ignore RPM module components which already have the correct REDHAT namespace in their purl
    # Fix modules which are missing the REDHAT namespace in their purl (only 15)
    # All of them have a duplicate module with the correct purl
    for bad_module in (
        Component.objects.filter(purl__startswith=module_purl)
        .exclude(purl__startswith=red_hat_module_purl)
        .iterator()
    ):
        good_purl = bad_module.purl.replace(module_purl, red_hat_module_purl, 1)
        good_module = Component.objects.get(purl=good_purl)

        # Bad modules have either 0 or 1 cnode, good modules have exactly 1 cnode
        bad_node = ComponentNode.objects.filter(
            type="SOURCE", parent=None, purl=bad_module.purl
        ).first()
        if not bad_node:
            # No nodes means no descendants to check for dupes / missing data
            # So we can just delete it
            bad_module.delete()
            continue
        good_node = ComponentNode.objects.get(type="SOURCE", parent=None, purl=good_purl)

        bad_descendants = (
            bad_node.get_descendants().values_list("purl", flat=True).distinct().iterator()
        )
        good_descendants = set(
            good_node.get_descendants().values_list("purl", flat=True).distinct().iterator()
        )

        for descendant_purl in bad_descendants:
            # Check to make sure the new / good module
            # has the same (or more) data as the old / bad one
            # If not, reprocess the good module to complete its data
            if descendant_purl not in good_descendants:
                slow_fetch_modular_build.delay(
                    good_module.software_build.build_id, force_process=True
                )
                break
        # Either both modules have all the same data
        # Or the good module will have the same data, after it finishes reprocessing
        bad_module.delete()

    # Ignore component nodes for modules
    # which already have the correct REDHAT namespace in their purl
    # Fix nodes which are missing the REDHAT namespace in their purl (several hundred)
    # Duplicate data prevents us from saving these nodes / updating their purls
    # Because another node already exists with the correct / updated purl
    # Find the duplicate node with the good purl
    # Then reprocess the linked module, if needed, so that it has a complete set of data
    for bad_node in (
        ComponentNode.objects.filter(type="SOURCE", parent=None, purl__startswith=module_purl)
        .exclude(purl__startswith=red_hat_module_purl)
        .iterator()
    ):
        good_purl = bad_node.purl.replace(module_purl, red_hat_module_purl, 1)
        good_node = ComponentNode.objects.get(type="SOURCE", parent=None, purl=good_purl)

        bad_descendants = (
            bad_node.get_descendants().values_list("purl", flat=True).distinct().iterator()
        )
        good_descendants = set(
            good_node.get_descendants().values_list("purl", flat=True).distinct().iterator()
        )

        for descendant_purl in bad_descendants:
            # Check to make sure the new / good module
            # has the same (or more) data as the old / bad one
            # If not, reprocess the good module to complete its data
            if descendant_purl not in good_descendants:
                slow_fetch_modular_build.delay(
                    good_node.obj.software_build.build_id, force_process=True
                )
                break
        # Either both root nodes / modules have all the same children
        # Or the good node will have the same children, after it finishes reprocessing
        bad_node.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0090_auto_20230926_1727"),
    ]

    operations = [
        migrations.RunPython(fix_module_data),
    ]
