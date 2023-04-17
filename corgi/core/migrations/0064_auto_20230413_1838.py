# Generated by Django 3.2.18 on 2023-04-13 18:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0063_alter_softwarebuild_build_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productcomponentrelation",
            name="build_type",
            field=models.CharField(
                choices=[
                    ("BREW", "Brew"),
                    ("KOJI", "Koji"),
                    ("CENTOS", "Centos"),
                    ("APP_INTERFACE", "App Interface"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="productcomponentrelation",
            name="type",
            field=models.CharField(
                choices=[
                    ("ERRATA", "Errata"),
                    ("COMPOSE", "Compose"),
                    ("BREW_TAG", "Brew Tag"),
                    ("CDN_REPO", "Cdn Repo"),
                    ("YUM_REPO", "Yum Repo"),
                    ("APP_INTERFACE", "App Interface"),
                ],
                max_length=50,
            ),
        ),
        migrations.AlterField(
            model_name="softwarebuild",
            name="build_type",
            field=models.CharField(
                choices=[
                    ("BREW", "Brew"),
                    ("KOJI", "Koji"),
                    ("CENTOS", "Centos"),
                    ("APP_INTERFACE", "App Interface"),
                ],
                max_length=20,
            ),
        ),
    ]