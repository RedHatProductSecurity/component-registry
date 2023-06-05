from django.db import migrations, transaction


def fix_vendor_in_go_package_names(apps, schema_editor):
    """Remove vendor/ from Go package names that are not linked to modules"""
    Component = apps.get_model("core", "Component")
    ComponentNode = apps.get_model("core", "ComponentNode")

    # 628 go-packages were saved with "vendor/" in their name, due to a typo
    # We strip this value for Go packages that are linked to modules
    # We should, but didn't, strip it for Go packages that are direct dependents
    # not linked to any module (usually stdlib packages)
    with transaction.atomic():
        for component in Component.objects.filter(
            type="GOLANG", name__startswith="vendor/"
        ).iterator():
            component.name = component.name.replace("vendor/", "", 1)
            # We can't rely on custom .save() code to update the purl
            component.purl = component.purl.replace("vendor/", "", 1)
            component.save()
        # We also can't access GenericForeignKeys / component.cnodes in a migration
        for node in ComponentNode.objects.filter(purl__startswith="pkg:golang/vendor/").iterator():
            node.purl = node.purl.replace("vendor/", "", 1)
            node.save()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0068_fix_missing_go_component_type"),
    ]

    operations = [
        migrations.RunPython(fix_vendor_in_go_package_names),
    ]
