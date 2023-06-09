import random
from datetime import datetime
from random import choice, randint

import factory
from django.utils import timezone

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

    # TODO improve this to generate md5 only for PNC build type
    # factory.sequence(lambda n: n + 100) for other types
    build_id = factory.Faker("md5")
    build_type = random.choice(models.SoftwareBuild.Type.values)
    name = factory.Faker("word")
    tag = factory.RelatedFactory(SoftwareBuildTagFactory, factory_related_name="tagged_model")
    completion_time = timezone.now()


class ProductTagFactory(TagFactory):
    class Meta:
        model = models.ProductTag


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Product

    name = factory.Faker("word")
    version = ""
    description = factory.Faker("word")
    tag = factory.RelatedFactory(ProductTagFactory, factory_related_name="tagged_model")


class ProductVersionTagFactory(TagFactory):
    class Meta:
        model = models.ProductVersionTag


class ProductVersionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductVersion

    name = factory.Faker("word")
    version = "8"
    description = factory.Faker("word")
    # link model using relationship to parent model
    products = factory.SubFactory(
        ProductFactory,
        name=factory.SelfAttribute("..name"),
        description=factory.SelfAttribute("..description"),
    )
    # link model using reverse relationship to child models
    tag = factory.RelatedFactory(ProductVersionTagFactory, factory_related_name="tagged_model")


class ProductStreamTagFactory(TagFactory):
    class Meta:
        model = models.ProductStreamTag


class ProductStreamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductStream

    name = factory.Faker("word")
    version = "8.2.0.z"
    description = factory.Faker("word")
    cpe = "cpe:/o:redhat:enterprise_linux:8"
    # link model using relationship to parent model
    products = factory.SubFactory(
        ProductFactory,
        name=factory.SelfAttribute("..name"),
        description=factory.SelfAttribute("..description"),
    )
    productversions = factory.SubFactory(
        ProductVersionFactory,
        name=factory.SelfAttribute("..name"),
        description=factory.SelfAttribute("..description"),
        products=factory.SelfAttribute("..products"),
    )
    # link model using reverse relationship to child models
    tag = factory.RelatedFactory(ProductStreamTagFactory, factory_related_name="tagged_model")
    active = True


class ProductVariantTagFactory(TagFactory):
    class Meta:
        model = models.ProductVariantTag


class ProductVariantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductVariant

    name = factory.Faker("word")
    # TODO: Fix below
    version = ""
    description = factory.Faker("word")
    # TODO: Use dynamic sane value for below
    #  factories and model properties logic could be improved
    #  maybe roll some of this into an abstract ProductModelFactory
    cpe = "cpe:/o:redhat:enterprise_linux:8"
    # link model using relationship to parent model
    # parent model will reuse properties defined here
    products = factory.SubFactory(
        ProductFactory,
        name=factory.SelfAttribute("..name"),
        description=factory.SelfAttribute("..description"),
    )
    productversions = factory.SubFactory(
        ProductVersionFactory,
        name=factory.SelfAttribute("..name"),
        description=factory.SelfAttribute("..description"),
        products=factory.SelfAttribute("..products"),
    )
    productstreams = factory.SubFactory(
        ProductStreamFactory,
        name=factory.SelfAttribute("..name"),
        description=factory.SelfAttribute("..description"),
        products=factory.SelfAttribute("..products"),
        productversions=factory.SelfAttribute("..productversions"),
    )
    # link model using reverse relationship to child models
    tag = factory.RelatedFactory(ProductVariantTagFactory, factory_related_name="tagged_model")


def random_erratum_name(n):
    return f'{choice(("rhsa", "rhba", "rhea"))}-{randint(2006, 2020)}:{n + 5000}'


class ProductComponentRelationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductComponentRelation

    external_system_id = factory.sequence(random_erratum_name)
    build_type = random.choice(models.SoftwareBuild.Type.values)


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

    type = random.choice(models.Component.Type.values)
    namespace = random.choice(models.Component.Namespace.values)
    name = factory.Faker("word")
    version = ".".join(str(n) for n in random.sample(range(10), 3))
    release = str(random.randint(0, 10))
    arch = random.choice(("src", "noarch", "s390", "ppc", "x86_64"))
    # These values use nonstandard SPDX license identifiers to match the real data
    license_declared_raw = "BSD-3-Clause or (GPLv3+ with exceptions and LGPLv3+) and Public Domain"
    license_concluded_raw = "(MIT and (ASL 2.0 or GPLv3+ with exceptions)) or LGPLv3+"

    software_build = factory.SubFactory(SoftwareBuildFactory)
    tag = factory.RelatedFactory(ComponentTagFactory, factory_related_name="tagged_model")

    meta_attr: dict = {}


class SrpmComponentFactory(ComponentFactory):
    type = models.Component.Type.RPM
    namespace = models.Component.Namespace.REDHAT
    arch = "src"


class ContainerImageComponentFactory(ComponentFactory):
    type = models.Component.Type.CONTAINER_IMAGE
    namespace = ""
    arch = "noarch"


class LifeCycleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.AppStreamLifeCycle

    name = "bzip2-devel"
    type = "package"
    lifecycle = 10
    acg = 2
    end_date = datetime.strptime("20320311", "%Y%m%d")
    product = "RHEL"
    initial_product_version = "9.0"
    stream = "1.0.8"
    private = False
    source = "default"
