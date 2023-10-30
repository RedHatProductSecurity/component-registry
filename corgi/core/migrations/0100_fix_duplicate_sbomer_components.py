from django.db import migrations
from django.db.models import F, Value, functions

from corgi.core.constants import RED_HAT_MAVEN_REPOSITORY

LICENSE_DECLARED_RAW_FIELD = F("license_declared_raw")
PURL_FIELD = F("purl")

# Identical except for a trailing / slash we just added
RED_HAT_MAVEN_REPOSITORY_OLD = "https://maven.repository.redhat.com/ga"

SEMICOLON_VALUE = Value(";")
SPDX_OR_VALUE = Value(" OR ")


def find_duplicate_sbomer_components(apps, schema_editor) -> None:
    """Find duplicate quarkus-bom components"""
    Component = apps.get_model("core", "Component")
    ComponentNode = apps.get_model("core", "ComponentNode")

    # We create and link nodes using relationships in SBOMer manifests
    # Maven-type quarkus-bom components are the first in a list of components
    # They have no nodes or links at all, so they must have no relationships in the manifest

    # Generic-type quarkus-bom components are the root (outside the list) in the manifest
    # They have nodes and provide all other components
    # So all the relationships in the manifest must refer to the root component,
    # AKA the generic-type quarkus-bom component which we artificially create,
    # and nothing refers to the first component in the list,
    # AKA the Maven-type quarkus-bom component which is identical to the root

    # Delete the unused / duplicated Maven-type components,
    # so we can make the root component use Maven type instead
    # so the root component purls in Corgi match the CVE-mapping tool / purls in ET
    # so SDEngine can easily find and manifest the Quarkus components
    Component.objects.filter(type="MAVEN", name="quarkus-bom").delete()

    for component in Component.objects.filter(type="GENERIC", name="quarkus-bom").iterator():
        component.type = "MAVEN"
        if ";" in component.license_declared_raw:
            component.license_declared_raw = component.license_declared_raw.replace(";", " OR ")

        if "type" not in component.meta_attr:
            component.meta_attr["type"] = "pom"
        if "group_id" not in component.meta_attr:
            component.meta_attr["group_id"] = "com.redhat.quarkus.platform"

        bad_purl = component.purl
        good_purl = bad_purl.replace("pkg:generic/", "pkg:maven/com.redhat.quarkus.platform/", 1)

        if "type=" not in good_purl:
            if "?" not in good_purl:
                good_purl = f"{good_purl}?type=pom"
            else:
                good_purl = f"{good_purl}&type=pom"
        # Now "?" and "type=" are both guaranteed to be in the purl
        # So prepend the "repository_url=" qualifier (to keep them in alphabetical order)
        good_purl = good_purl.replace("?", f"?repository_url={RED_HAT_MAVEN_REPOSITORY}&", 1)
        component.save()

        ComponentNode.objects.filter(type="SOURCE", parent=None, purl=bad_purl).update(
            purl=good_purl
        )

        component.provides.filter(license_declared_raw__contains=";").update(
            license_declared_raw=functions.Replace(
                LICENSE_DECLARED_RAW_FIELD, SEMICOLON_VALUE, SPDX_OR_VALUE
            )
        )

    # Now that RED_HAT_MAVEN_REPOSITORY has a trailing / slash, fix values without this (if any)
    Component.objects.filter(
        purl__contains=f"repository_url={RED_HAT_MAVEN_REPOSITORY_OLD}"
    ).exclude(purl__contains=f"repository_url={RED_HAT_MAVEN_REPOSITORY}").update(
        purl=functions.Replace(
            PURL_FIELD, Value(RED_HAT_MAVEN_REPOSITORY_OLD), Value(RED_HAT_MAVEN_REPOSITORY)
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0099_add_type_arch_stored_proc_filter"),
    ]

    operations = [
        migrations.RunPython(find_duplicate_sbomer_components),
    ]
