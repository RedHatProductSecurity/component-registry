from django.db import migrations

# Must use non-historical models which support cnodes and get_descendants()
from corgi.core.models import Component, ComponentNode
from corgi.tasks.brew import slow_fetch_brew_build

purl_prefix = "pkg:oci/"
red_hat_purl_prefix = f"{purl_prefix}redhat/"


def fix_container_data(apps, schema_editor):
    """Clean up duplicate containers / nodes with bad purls
    and ensure the non-duplicated containers / nodes have all the same data"""
    # Fix index containers which have an incorrect REDHAT namespace in their purl
    # Also fix non-index / arch-specific child containers with the same issue
    # This code left behind just in case prod has bad data, but at least in stage it's not needed
    for bad_container in Component.objects.filter(purl__startswith=red_hat_purl_prefix).iterator():
        good_purl = bad_container.purl.replace(red_hat_purl_prefix, purl_prefix, 1)
        good_container = Component.objects.filter(purl=good_purl).first()
        # .delete() and .update() functions on querysets are atomic, even outside transactions
        bad_container_qs = Component.objects.filter(purl=bad_container.purl)
        bad_node_qs = bad_container.cnodes.get_queryset()

        # No duplicate container is present
        # So we can just fix the bad container / node purls
        # As well as the bad children, after we check for duplicates
        if not good_container:
            bad_container_qs.update(purl=good_purl)
            bad_node_qs.update(purl=good_purl)
            for bad_node in bad_node_qs.iterator():
                fix_bad_children(bad_node)
            continue

        # No nodes means no descendants to check for dupes / missing data
        # So we can just delete the bad container, we won't lose any data
        bad_node = bad_node_qs.first()
        if not bad_node:
            bad_container_qs.delete()
            continue

        # Else we have a duplicate / good container
        # and the old / bad container has nodes we need to check
        good_node = good_container.cnodes.get()
        bad_descendants = (
            bad_node.get_descendants().values_list("purl", flat=True).distinct().iterator()
        )
        good_descendants = set(
            good_node.get_descendants().values_list("purl", flat=True).distinct().iterator()
        )

        # Check to make sure the new / good container
        # has the same (or more) data as the old / bad one
        # If not, reprocess the good container to complete its data
        for descendant_purl in bad_descendants:
            if descendant_purl not in good_descendants:
                slow_fetch_brew_build.delay(
                    good_node.get_root().obj.software_build.build_id, force_process=True
                )
                break
        # Either both containers have all the same data
        # Or the good container will have the same data, after it finishes reprocessing
        bad_container_qs.delete()

    # Fix component nodes for index containers
    # which have an incorrect REDHAT namespace in their purl (several hundred thousand)
    # Duplicate data prevents us from saving these nodes / updating their purls
    # in most cases, whenever another node already exists with the correct / updated purl
    # Find the duplicate node with the good purl
    # Then reprocess the linked ndex container, if needed, so that it has a complete set of data
    for bad_node in ComponentNode.objects.filter(
        type="SOURCE", parent=None, purl__startswith=red_hat_purl_prefix
    ).iterator():
        good_purl = bad_node.purl.replace(red_hat_purl_prefix, purl_prefix, 1)
        good_node = ComponentNode.objects.filter(type="SOURCE", parent=None, purl=good_purl).first()
        # .delete() and .update() functions on querysets are atomic, even outside transactions
        bad_node_qs = ComponentNode.objects.filter(type="SOURCE", parent=None, purl=bad_node.purl)

        # No duplicate node is present
        # So we can just fix the bad root node's purl
        # We can also fix bad child node purls either by deleting them,
        # then reprocessing the build, if the bad children have duplicates
        # or updating them, only if they don't have any duplicates
        if not good_node:
            bad_node_qs.update(purl=good_purl)
            # Fix component nodes for non-index / arch-specific child containers
            # which have an incorrect REDHAT namespace in their purl, if any
            # The child containers themselves were fixed in the first loop above
            fix_bad_children(bad_node)
            continue

        bad_descendants = (
            bad_node.get_descendants().values_list("purl", flat=True).distinct().iterator()
        )
        good_descendants = set(
            good_node.get_descendants().values_list("purl", flat=True).distinct().iterator()
        )

        # Check to make sure the new / good index container
        # has the same (or more) data as the old / bad one
        # If not, reprocess the good index container to complete its data
        for descendant_purl in bad_descendants:
            if descendant_purl not in good_descendants:
                slow_fetch_brew_build.delay(
                    good_node.obj.software_build.build_id, force_process=True
                )
                break
        # Either both root nodes / index containers have all the same children
        # Or the good node will have the same children, after it finishes reprocessing
        # This includes bad OCI-type children of the root node with "/redhat/" in their purls
        # The good node will have the same child nodes, but without "/redhat/" in their purls
        bad_node_qs.delete()


def fix_bad_children(bad_node):
    """Helper method to find and clean up child containers with bad purls"""
    bad_children = bad_node.get_children().filter(purl__startswith=red_hat_purl_prefix)
    for bad_child in bad_children:
        good_child_purl = bad_child.purl.replace(red_hat_purl_prefix, purl_prefix, 1)
        if ComponentNode.objects.filter(
            type=bad_child.type, parent=bad_child.parent, purl=good_child_purl
        ).exists():
            # Duplicate exists, just delete the old node
            # then reprocess this container to avoid data loss
            bad_children.filter(purl=bad_child.purl).delete()
            slow_fetch_brew_build.delay(bad_node.obj.software_build.build_id, force_process=True)
        else:
            # No duplicate exists, so we can just update the current node
            # No need to delete or reprocess anything
            bad_children.filter(purl=bad_child.purl).update(purl=good_child_purl)


class Migration(migrations.Migration):
    # Code above should be safe to run outside a transaction / already atomic
    # and will take forever, so we need to pick up where we left off after timeouts
    atomic = False
    dependencies = [
        ("core", "0097_install_gin_indexes"),
    ]

    operations = [
        migrations.RunPython(fix_container_data),
    ]
