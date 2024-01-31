from django.db import migrations

from corgi.core.constants import GET_LATEST_COMPONENT_STOREDPROC_SQL


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0115_add_et_product_attr_to_variant"),
    ]

    operations = [
        # explicitly remove previous (different functional signature) get_latest_component
        migrations.RunSQL(
            "DROP FUNCTION get_latest_component(text,text,text,text,text,text,boolean);"
        ),
        migrations.RunSQL(GET_LATEST_COMPONENT_STOREDPROC_SQL),
    ]
