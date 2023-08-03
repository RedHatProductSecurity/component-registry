from django.db import migrations

# Non-historical model, i.e. if you change save_product_taxonomy() tomorrow
# the migration will not run the same code as before (at the time the migration was written)
from corgi.tasks.prod_defs import (
    slow_remove_product_from_build as current_remove_product_from_build,
)


def clean_cdn_repo_relations(apps, schema_editor):
    ProductComponentRelation = apps.get_model("core", "ProductComponentRelation")
    ProductVariant = apps.get_model("core", "ProductVariant")
    # Hardcoding the constant because we want to remove this type from the model

    # Prefetch of CDN_REPO relation product_ref variant primary keys to make the following loop more
    # efficient
    cdn_relation_variants = (
        ProductComponentRelation.objects.filter(type="CDN_REPO", software_build_id__isnull=False)
        .values_list("product_ref", flat=True)
        .distinct()
    )

    variant_names_pk = ProductVariant.objects.filter(name__in=cdn_relation_variants).values(
        "name", "pk"
    )
    variant_names_dict = {npk["name"]: str(npk["pk"]) for npk in variant_names_pk}

    for cdn_relation_build_to_product in (
        ProductComponentRelation.objects.filter(type="CDN_REPO", software_build_id__isnull=False)
        .values_list("software_build_id", "product_ref")
        .distinct()
        .iterator()
    ):
        product_ref = cdn_relation_build_to_product[1]

        if product_ref not in variant_names_dict:
            continue

        current_remove_product_from_build.delay(
            str(cdn_relation_build_to_product[0]),
            "ProductVariant",
            variant_names_dict[product_ref],
        )

    ProductComponentRelation.objects.filter(type="CDN_REPO").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0081_fix_build_sources"),
    ]

    operations = [
        migrations.RunPython(clean_cdn_repo_relations),
    ]
