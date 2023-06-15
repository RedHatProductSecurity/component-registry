from django.db import migrations


def add_skip_manifest_tags_to_streams(apps, schema_editor):
    """Add skip manifest tags to some streams"""
    ProductStream = apps.get_model("core", "ProductStream")
    ProductStreamTag = apps.get_model("core", "ProductStreamTag")

    # Other streams are also skipped, see corgi.tasks.manifests.update_rhel_manifests
    # These are mostly middleware streams where we get a more accurate SBOM from Deptopia until PNC
    # is on-boarded, all stream are using SBOMMER feature of PNC
    # The cost-management stream should be excluded by the managed-services filter but it is missed.
    # See also
    # https://docs.google.com/spreadsheets/d/1dci4mxCi1hlWbmE9drRyjclsTJHV9ySKedKU3_j9paE/edit#gid=0
    for stream in (
        "amq-st-2",
        "cost-management",
        "eap-6.4.23",
        "fsw-6",
        "fuse-6.3.0",
        "jdg-8",
        "jbcs-httpd-2.4",
        "jws-5",
        "nmo-4.10",
        "openjdk-1.8",
        "openjdk-11",
        "openjdk-17",
        "rhai-1",
        "red_hat_discovery-1.0",
        "rhivos-test",
    ):
        ps = ProductStream.objects.get(name=stream)
        ProductStreamTag.objects.create(name="skip_manifest", tagged_model=ps)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0069_auto_20230606_1542"),
    ]

    operations = [
        migrations.RunPython(add_skip_manifest_tags_to_streams),
    ]
