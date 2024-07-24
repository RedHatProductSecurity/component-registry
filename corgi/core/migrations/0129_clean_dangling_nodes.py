from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0119_fix_related_url_on_contains"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "delete from core_componentnode cn "
                "where not exists("
                "   select from core_component "
                "   where uuid = cn.object_id"
                ");"
            ]
        ),
    ]
