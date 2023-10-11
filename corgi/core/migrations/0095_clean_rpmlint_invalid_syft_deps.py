from django.db import migrations


def clean_rpmlint_invalid_syft_deps(apps, schema_editor):
    Component = apps.get_model("core", "Component")

    for rpmlint_src in Component.objects.filter(name="rpmlint", type="RPM", arch="src"):
        # Delete any provide which wasn't detected by koji.listRPMs (the noarch rpm only)
        rpmlint_src.provides.exclude(meta_attr__source__contains="koji.listRPMs").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0094_fix_stored_proc_inactive_filter"),
    ]

    operations = [
        migrations.RunPython(clean_rpmlint_invalid_syft_deps),
    ]
