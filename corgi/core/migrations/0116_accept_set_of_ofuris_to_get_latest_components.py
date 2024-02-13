from django.db import migrations

from corgi.core.constants import GET_LATEST_COMPONENTS_STOREDPROC_SQL


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0115_add_et_product_attr_to_variant"),
    ]

    operations = [
        migrations.RunSQL(GET_LATEST_COMPONENTS_STOREDPROC_SQL),
    ]
