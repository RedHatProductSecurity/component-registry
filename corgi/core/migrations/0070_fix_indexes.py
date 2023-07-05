from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = (("core", "0069_auto_20230606_1542"),)

    operations = (
        # Below were originally added in 0037_custom_indexes.py
        # These were manually created by us to improve performance
        # If you want to add custom indexes, update models.py
        # Do not run custom SQL, or Postgres and Django will disagree
        # about the current state of our tables / models
        migrations.RunSQL("DROP INDEX core_componentnode_tree_parent_lft_idx"),
        migrations.RunSQL("DROP INDEX core_cn_tree_lft_purl_parent_idx"),
        # Already removed in 0059
        # migrations.RunSQL("DROP INDEX core_cn_lft_tree_idx"),
        migrations.RunSQL("DROP INDEX core_cn_lft_rght_tree_idx"),
        # Add them back so the model definition is in sync with the DB
        migrations.AddIndex(
            model_name="componentnode",
            index=models.Index(
                fields=("tree_id", "parent_id", "lft"), name="core_cn_tree_parent_lft_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="componentnode",
            index=models.Index(
                fields=("tree_id", "lft", "purl", "parent_id"),
                name="core_cn_tree_lft_purl_prnt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="componentnode",
            index=models.Index(fields=("lft", "tree_id"), name="core_cn_lft_tree_idx"),
        ),
        migrations.AddIndex(
            model_name="componentnode",
            index=models.Index(fields=("lft", "rght", "tree_id"), name="core_cn_lft_rght_tree_idx"),
        ),
        # Below are default indexes to support foreign keys / constraints
        # These were automatically created by Django, then removed in 0059
        # If you want to delete them, update models.py
        # Do not run custom SQL, or Postgres and Django will disagree
        # about the current state of our tables / models
        migrations.RunSQL(
            "CREATE INDEX core_componentnode_parent_id_be93cab7 "
            "ON core_componentnode USING btree (parent_id)"
        ),
        migrations.RunSQL(
            "CREATE INDEX core_componentnode_content_type_id_334077a8 "
            "ON core_componentnode USING btree (content_type_id)"
        ),
        migrations.RunSQL(
            "CREATE INDEX core_component_channels_component_id_9c8754af "
            "ON core_component_channels USING btree (component_id)"
        ),
    )
