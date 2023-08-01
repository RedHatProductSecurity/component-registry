from django.db import migrations
from packageurl import PackageURL

from corgi.core.constants import RED_HAT_MAVEN_REPOSITORY


def fix_maven_namespaces(apps, schema_editor):
    """Set namespace to REDHAT for Maven components with "redhat" in their version strings"""
    Component = apps.get_model("core", "Component")
    ComponentNode = apps.get_model("core", "ComponentNode")

    # Values in current data are always -redhat or .redhat
    maven_components = Component.objects.filter(
        type="MAVEN", namespace="UPSTREAM", version__contains="redhat"
    )

    # Fix all Components and ComponentNodes (custom code in .save() does not work here)
    for component in maven_components.iterator():
        component.namespace = "REDHAT"
        old_purl = component.purl
        component.purl = fix_maven_purl(component)
        component.save()

        ComponentNode.objects.filter(purl=old_purl).update(purl=component.purl)


def fix_maven_purl(component):
    """Helper method copied from corgi.core.models.Component._build_maven_purl()
    because custom code isn't accessible when running migrations"""
    # We only call this function for Maven components in the REDHAT namespace
    # So the repository_url should always be set to the Red Hat Maven server
    qualifiers = {"repository_url": RED_HAT_MAVEN_REPOSITORY}

    classifier = component.meta_attr.get("classifier")
    if classifier:
        qualifiers["classifier"] = classifier

    extension = component.meta_attr.get("type")
    if extension:
        qualifiers["type"] = extension

    purl_data = {
        "type": str(component.type).lower(),
        "name": component.name,
        "version": component.version,
        "qualifiers": qualifiers,
    }
    group_id = component.meta_attr.get("group_id")
    if group_id:
        purl_data["namespace"] = group_id

    purl_obj = PackageURL(**purl_data)
    return purl_obj.to_string()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0080_adjust_product_cpe_fields"),
    ]

    operations = [migrations.RunPython(fix_maven_namespaces)]
