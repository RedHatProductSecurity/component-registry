from datetime import datetime
from random import choice, randint

import factory

from corgi.core import models


class TagFactory(factory.django.DjangoModelFactory):
    class Meta:
        abstract = True

    name = factory.Faker("word")
    value = factory.Faker("word")


class SoftwareBuildTagFactory(TagFactory):
    class Meta:
        model = models.SoftwareBuildTag


class SoftwareBuildFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.SoftwareBuild

    build_id = factory.sequence(lambda n: n + 100)
    name = factory.Faker("word")
    tag = factory.RelatedFactory(SoftwareBuildTagFactory, factory_related_name="software_build")


class ProductTagFactory(TagFactory):
    class Meta:
        model = models.ProductTag


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Product

    name = factory.Faker("word")
    version = ""
    description = factory.Faker("word")
    tag = factory.RelatedFactory(ProductTagFactory, factory_related_name="product")


class ProductVersionTagFactory(TagFactory):
    class Meta:
        model = models.ProductVersionTag


class ProductVersionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductVersion

    name = factory.Faker("word")
    version = "8"
    description = factory.Faker("word")
    tag = factory.RelatedFactory(ProductVersionTagFactory, factory_related_name="product_version")


class ProductStreamTagFactory(TagFactory):
    class Meta:
        model = models.ProductStreamTag


class ProductStreamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductStream

    name = factory.Faker("word")
    version = "8.2.z"
    description = factory.Faker("word")
    cpe = "cpe:/o:redhat:enterprise_linux:9"
    tag = factory.RelatedFactory(ProductStreamTagFactory, factory_related_name="product_stream")


class ProductVariantTagFactory(TagFactory):
    class Meta:
        model = models.ProductVariantTag


class ProductVariantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductVariant

    name = factory.Faker("word")
    version = ""
    description = factory.Faker("word")
    tag = factory.RelatedFactory(ProductVariantTagFactory, factory_related_name="product_variant")


def random_erratum_name(n):
    return f'{choice(("rhsa", "rhba", "rhea"))}-{randint(2006, 2020)}:{n + 5000}'


class ProductComponentRelationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductComponentRelation

    external_system_id = factory.sequence(random_erratum_name)


class ChannelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Channel

    name = factory.Faker("word")


class ComponentTagFactory(TagFactory):
    class Meta:
        model = models.ComponentTag


class ComponentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Component

    type = models.Component.Type.RPM
    name = factory.Faker("word")
    version = "3.2.1"
    release = "1.0.1e"
    arch = "testarch"
    license = "BSD-3-Clause or (GPLv3+ and LGPLv3+)"

    software_build = factory.SubFactory(SoftwareBuildFactory)
    tag = factory.RelatedFactory(ComponentTagFactory, factory_related_name="component")

    meta_attr = {}


class LifeCycleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.AppStreamLifeCycle

    name = "bzip2-devel"
    type = "package"
    lifecycle = 10
    acg = 2
    end_date = datetime.strptime("20320311", "%Y%m%d")
    product = "RHEL"
    initial_product_version: "9.0"
    stream = "1.0.8"
    private = False
    source = "default"
