from django.db import migrations

# Non-historical model, i.e. if you change save_product_taxonomy() tomorrow
# the migration will not run the same code as before (at the time the migration was written)
from corgi.tasks.prod_defs import (
    slow_reset_build_product_taxonomy as current_remove_product_from_build,
)


def clean_brew_tag_variant_relations(apps, schema_editor):
    ProductVariant = apps.get_model("core", "ProductVariant")

    # Get variants names and primary keys where parent stream has brew_tags
    brew_tag_variants = ProductVariant.objects.exclude(productstreams__brew_tags={})
    brew_tag_variant_names_dict = {npk.name: str(npk.pk) for npk in brew_tag_variants}

    ProductComponentRelation = apps.get_model("core", "ProductComponentRelation")

    for errata_relation_build_to_product in (
        ProductComponentRelation.objects.filter(type="ERRATA", software_build_id__isnull=False)
        .values_list("software_build_id", "product_ref")
        .distinct()
        .iterator()
    ):
        product_ref = errata_relation_build_to_product[1]

        if product_ref not in brew_tag_variant_names_dict:
            continue

        current_remove_product_from_build.delay(
            str(errata_relation_build_to_product[0]),
            "ProductVariant",
            brew_tag_variant_names_dict[product_ref],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0085_productstream_cpes_from_brew_tag_variants"),
    ]

    operations = [
        migrations.RunPython(clean_brew_tag_variant_relations),
    ]
