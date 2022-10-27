# set table specific pg autovacuum settings

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0041_alter_component_options"),
    ]

    operations = [
        migrations.RunSQL(
            "ALTER TABLE public.core_componentnode SET (autovacuum_vacuum_scale_factor=0.001);"
        ),
        migrations.RunSQL(
            "ALTER TABLE public.core_component SET (autovacuum_vacuum_scale_factor=0.001);"
        ),
    ]
