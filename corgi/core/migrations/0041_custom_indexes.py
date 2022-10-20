# custom django migrations for setting pg indexes for 'latest view'

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_auto_20221020_1057"),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX core_compon_latest_name_type_idx ON public.core_component \
             USING btree (name, type) WHERE (((type)::text = 'SRPM'::text) OR \
             (((arch)::text = 'noarch'::text) AND ((type)::text = 'CONTAINER_IMAGE'::text)) \
             OR ((type)::text = 'RHEL_MODULE'::text))"
        ),
        migrations.RunSQL(
            "CREATE INDEX core_compon_latest_type_name_idx ON public.core_component \
             USING btree (type, name) WHERE (((type)::text = 'SRPM'::text) OR \
             (((arch)::text = 'noarch'::text) AND ((type)::text = 'CONTAINER_IMAGE'::text)) \
             OR ((type)::text = 'RHEL_MODULE'::text))"
        ),
        migrations.RunSQL(
            "CREATE INDEX core_compon_latest_idx ON public.core_component \
            USING btree (uuid, software_build_id, type, name, product_streams) \
            WHERE (((type)::text = 'SRPM'::text) OR (((arch)::text = 'noarch'::text) \
            AND ((type)::text = 'CONTAINER_IMAGE'::text)) OR ((type)::text = 'RHEL_MODULE'::text))"
        ),
    ]
