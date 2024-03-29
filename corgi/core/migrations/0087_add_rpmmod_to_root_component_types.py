# Generated by Django 3.2.18 on 2023-08-28 18:59

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0086_clean_brew_tag_variant_relations"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="component",
            name="compon_latest_name_type_idx",
        ),
        migrations.RemoveIndex(
            model_name="component",
            name="compon_latest_type_name_idx",
        ),
        migrations.RemoveIndex(
            model_name="component",
            name="compon_latest_idx",
        ),
        migrations.AddIndex(
            model_name="component",
            index=models.Index(
                condition=models.Q(
                    models.Q(("arch", "src"), ("type", "RPM")),
                    ("type", "RPMMOD"),
                    models.Q(("arch", "noarch"), ("type", "OCI")),
                    models.Q(("arch", "noarch"), ("namespace", "REDHAT"), ("type", "GITHUB")),
                    _connector="OR",
                ),
                fields=["type", "name", "arch"],
                name="compon_latest_name_type_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="component",
            index=models.Index(
                condition=models.Q(
                    models.Q(("arch", "src"), ("type", "RPM")),
                    ("type", "RPMMOD"),
                    models.Q(("arch", "noarch"), ("type", "OCI")),
                    models.Q(("arch", "noarch"), ("namespace", "REDHAT"), ("type", "GITHUB")),
                    _connector="OR",
                ),
                fields=["name", "type", "arch"],
                name="compon_latest_type_name_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="component",
            index=models.Index(
                condition=models.Q(
                    models.Q(("arch", "src"), ("type", "RPM")),
                    ("type", "RPMMOD"),
                    models.Q(("arch", "noarch"), ("type", "OCI")),
                    models.Q(("arch", "noarch"), ("namespace", "REDHAT"), ("type", "GITHUB")),
                    _connector="OR",
                ),
                fields=["uuid", "software_build_id", "type", "name", "arch"],
                name="compon_latest_idx",
            ),
        ),
    ]
