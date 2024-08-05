# Generated by Django 3.2.25 on 2024-08-05 03:25

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0129_clean_dangling_nodes"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="componentnode",
            name="core_cn_tree_lft_purl_prnt_idx",
        ),
        migrations.RunSQL("DROP INDEX core_componentnode_content_type_id_334077a8"),
    ]