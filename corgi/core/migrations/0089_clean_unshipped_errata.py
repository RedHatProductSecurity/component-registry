# Generated by Django 3.2.18 on 2023-09-15 02:51
from django.db import migrations
from requests.exceptions import HTTPError

# Non-historical model, i.e. if you change save_product_taxonomy() tomorrow
# the migration will not run the same code as before (at the time the migration was written)
from corgi.collectors.errata_tool import ErrataTool as CurrentET


def removed_unshipped_errata_relations(apps, schema_editor):
    ProductComponentRelation = apps.get_model("core", "ProductComponentRelation")
    # Allow this migration to be re-run in case of timeout or other failure
    # Only get the errata we haven't processed yet
    unchecked_errata = (
        ProductComponentRelation.objects.filter(type="ERRATA")
        .exclude(meta_attr__has_key="ship_checked")
        .values_list("external_system_id", flat=True)
        .distinct()
    )
    et = CurrentET()

    for erratum_id in unchecked_errata:
        # This is prone to remote disconnection error
        try:
            _, shipped_live = et.get_errata_key_details(erratum_id)
        except HTTPError as e:
            if e.response.status_code == 403:
                # Must be embargoed, therefore it's not live yet
                shipped_live = False
            else:
                # Error out with exception and retry migration
                raise e
        errata_relations = ProductComponentRelation.objects.filter(
            type="ERRATA", external_system_id=erratum_id
        )
        if not shipped_live:
            errata_relations.delete()
        else:
            # Set these errata so they are not re-processed next run
            checked_relations = []
            for relation in errata_relations:
                relation.meta_attr["ship_checked"] = True
                checked_relations.append(relation)
            ProductComponentRelation.objects.bulk_update(checked_relations, ["meta_attr"])

    # remove all the temporary ship_checked meta_attr keys
    cleaned_relations = []
    for relation in ProductComponentRelation.objects.filter(meta_attr__ship_checked=True):
        del relation.meta_attr["ship_checked"]
        cleaned_relations.append(relation)
    ProductComponentRelation.objects.bulk_update(cleaned_relations, ["meta_attr"])


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("core", "0088_alter_productcomponentrelation_type"),
    ]

    operations = [
        migrations.RunPython(removed_unshipped_errata_relations),
    ]