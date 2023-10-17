from django.db import migrations
from django.db.models import F, Value, functions


def fix_nevra_nvr_data(apps, schema_editor):
    """Clean up components where the NEVRA or NVR ends with a dash, or a dash then dot"""
    Component = apps.get_model("core", "Component")

    # arch is not present in the NEVRA for this component type (whatever it may be)
    # This returns results, but shouldn't, since our code always adds arch to the NEVRA
    # even when arch="noarch", regardless of component type. I don't "fix" this here
    # to preserve the original NEVRA in case there's a reason for this (maybe a Syft NEVRA?)
    Component.objects.filter(
        nevra__endswith="-", epoch=0, version="", release="", arch="noarch"
    ).update(nvr=F("name"), nevra=F("name"))

    # arch is present in the NEVRA for this component type (whatever it may be)
    Component.objects.filter(
        nevra__endswith="-.noarch", epoch=0, version="", release="", arch="noarch"
    ).update(nvr=F("name"), nevra=functions.Concat(F("name"), Value(".noarch")))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0095_clean_rpmlint_invalid_syft_deps"),
    ]

    operations = [
        migrations.RunPython(fix_nevra_nvr_data),
    ]
