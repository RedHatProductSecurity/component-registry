from django.db import migrations, transaction


def fix_missing_go_component_type(apps, schema_editor):
    """Set missing go_component_type for container image upstream_go_modules"""
    Component = apps.get_model("core", "Component")

    # These are always upstreams of a container image
    # The first cnode has type SOURCE and a non-NULL parent
    with transaction.atomic():
        for component in Component.objects.filter(
            type="GOLANG", meta_attr__go_component_type__isnull=True, meta_attr__source__isnull=True
        ).iterator():
            component.meta_attr["go_component_type"] = "gomod"
            component.meta_attr["source"] = ["collectors/brew"]
            component.save()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0067_component_epoch"),
    ]

    operations = [
        migrations.RunPython(fix_missing_go_component_type),
    ]
