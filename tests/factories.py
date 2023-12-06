import random
from datetime import datetime
from random import choice, randint

import factory
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from factory.fuzzy import FuzzyInteger

from corgi.core import models
from corgi.core.models import ProductNode


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

    version = FuzzyInteger(20)
    name = factory.LazyAttribute(lambda o: f"{o.description}-{o.version}")
    description = factory.Faker("word")
    # link model using reverse relationship to child models
    tag = factory.RelatedFactory(ProductVersionTagFactory, factory_related_name="tagged_model")


class ProductStreamTagFactory(TagFactory):
    class Meta:
        model = models.ProductStreamTag


class ProductStreamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ProductStream

    version = FuzzyInteger(20)
    name = factory.LazyAttribute(lambda o: f"{o.description}-{o.version}")
    description = factory.Faker("word")

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
    version = ""
    description = factory.Faker("word")

    cpe = "cpe:/o:redhat:enterprise_linux:8"

    # link model using reverse relationship to child models
    tag = factory.RelatedFactory(ProductVariantTagFactory, factory_related_name="tagged_model")

    @factory.post_generation
    def productstreams(self, create, extracted, **kwargs):
        if not create or not extracted:
            return

        # Add the iterable of groups using bulk addition
        self.productstreams.add(*extracted)


class NodeFactory(factory.django.DjangoModelFactory):
    object_id = factory.SelfAttribute("obj.pk")
    content_type = factory.LazyAttribute(lambda o: ContentType.objects.get_for_model(o.obj))

    class Meta:
        exclude = ["obj"]
        abstract = True


class ProductModelFactory(NodeFactory):
    parent = None

    @factory.post_generation
    def ofuri(obj, create, extracted, **kwargs):
        if not create:
            return
        obj.obj.save_product_taxonomy()

    class Meta:
        model = ProductNode


class ProductNodeFactory(ProductModelFactory):
    obj = factory.SubFactory(ProductFactory)


class ProductVersionNodeFactory(ProductModelFactory):
    parent = factory.SubFactory(ProductNodeFactory)
    obj = factory.SubFactory(ProductVersionFactory)


class ProductStreamNodeFactory(ProductModelFactory):
    parent = factory.SubFactory(ProductVersionNodeFactory)
    obj = factory.SubFactory(ProductStreamFactory)


class ProductVariantNodeFactory(ProductModelFactory):
    parent = factory.SubFactory(ProductStreamNodeFactory)
    obj = factory.SubFactory(ProductVariantFactory)


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
    epoch = random.randint(0, 10)


class BinaryRpmComponentFactory(SrpmComponentFactory):
    arch = random.choice(("noarch", "s390", "ppc", "x86_64"))
    software_build = None


class UpstreamComponentFactory(ComponentFactory):
    type = random.choice(models.Component.REMOTE_SOURCE_COMPONENT_TYPES)
    namespace = models.Component.Namespace.UPSTREAM
    release = ""
    arch = "noarch"
    software_build = None


class ContainerImageComponentFactory(ComponentFactory):
    type = models.Component.Type.CONTAINER_IMAGE
    namespace = models.Component.Namespace.REDHAT
    arch = "noarch"


class ChildContainerImageComponentFactory(ComponentFactory):
    type = models.Component.Type.CONTAINER_IMAGE
    namespace = models.Component.Namespace.REDHAT
    arch = random.choice(("s390", "ppc", "x86_64"))
    software_build = None


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
