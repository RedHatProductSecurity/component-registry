from django.db import migrations

# Non-historical model, i.e. if you change save_product_taxonomy() tomorrow
# the migration will not run the same code as before (at the time the migration was written)
from corgi.tasks.common import slow_save_taxonomy as current_save_taxonomy


def set_build_fk_relations(apps, schema_editor):
    ProductComponentRelation = apps.get_model("core", "ProductComponentRelation")
    SoftwareBuild = apps.get_model("core", "SoftwareBuild")

    updated_builds = set()

    for relation in ProductComponentRelation.objects.filter(software_build=None).iterator():
        try:
            build = SoftwareBuild.objects.get(
                build_id=relation.build_id, build_type=relation.build_type
            )
            relation.software_build = build
            relation.save()
            updated_builds.add(
                (
                    relation.build_id,
                    relation.build_type,
                )
            )
        except SoftwareBuild.DoesNotExist:
            continue

    for updated_build in updated_builds:
        current_save_taxonomy.delay(updated_build[0], updated_build[1])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0082_clean_cdn_relations"),
    ]

    operations = [
        migrations.RunPython(set_build_fk_relations),
    ]
