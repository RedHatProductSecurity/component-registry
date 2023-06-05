from django.db import migrations, transaction
from django.db.utils import IntegrityError


def fix_vendor_in_go_package_names(apps, schema_editor):
    """Remove vendor/ from Go package names that are not linked to modules"""
    Component = apps.get_model("core", "Component")
    ComponentNode = apps.get_model("core", "ComponentNode")

    # 628 go-packages were saved with "vendor/" in their name, due to a typo
    # We strip this value for Go packages that are linked to modules
    # We should, but didn't, strip it for Go packages that are direct dependents
    # not linked to any module (all stdlib packages)
    with transaction.atomic():
        for component in Component.objects.filter(
            type="GOLANG", name__startswith="vendor/"
        ).iterator():
            component.name = component.name.replace("vendor/", "", 1)
            # We can't rely on custom .save() code to update the nevra / purl??
            component.nevra = component.nevra.replace("vendor/", "", 1)
            component.purl = component.purl.replace("vendor/", "", 1)
            with transaction.atomic():
                try:
                    component.save()
                except IntegrityError:
                    # The Component is a duplicate and can't be updated
                    # Relinking all the cnodes to the other Component also fails
                    # Fixing the duplicate nodes without losing data is non-trivial
                    # Punt for now, clean all this mess up in CORGI-566
                    pass
        # We also can't access GenericForeignKeys / component.cnodes in a migration
        for node in ComponentNode.objects.filter(purl__startswith="pkg:golang/vendor/").iterator():
            new_purl = node.purl.replace("vendor/", "", 1)
            node.obj = Component.objects.get(purl=new_purl)
            node.purl = new_purl
            with transaction.atomic():
                try:
                    node.save()
                except IntegrityError:
                    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0068_fix_missing_go_component_type"),
    ]

    operations = [
        migrations.RunPython(fix_vendor_in_go_package_names),
    ]
