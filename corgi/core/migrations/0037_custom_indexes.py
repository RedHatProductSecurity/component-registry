# custom django migrations for setting pg indexes
# for django-mptt specific performance optimisations on query and sorting

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_productcomponentrelation_core_produc_product_c88392_idx"),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX core_componentnode_tree_parent_lft_idx ON public.core_componentnode \
             USING btree(tree_id, parent_id, lft)"
        ),
        migrations.RunSQL(
            "CREATE INDEX core_cn_tree_lft_purl_parent_idx ON public.core_componentnode \
             USING btree (tree_id, lft, purl, parent_id) WHERE (parent_id IS NULL)"
        ),
        migrations.RunSQL(
            "CREATE INDEX core_cn_lft_tree_idx ON public.core_componentnode \
             USING btree (lft, tree_id)"
        ),
        migrations.RunSQL(
            "CREATE INDEX core_cn_lft_rght_tree_idx ON public.core_componentnode \
             USING btree (lft, rght, tree_id)"
        ),
    ]
