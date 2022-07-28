import logging
import re
import uuid as uuid
from collections import defaultdict

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres import fields
from django.db import models
from django.db.models import Q, QuerySet
from mptt.models import MPTTModel, TreeForeignKey
from packageurl import PackageURL

from corgi.core.constants import CONTAINER_DIGEST_FORMATS
from corgi.core.files import ProductManifestFile
from corgi.core.mixins import TimeStampedModel

logger = logging.getLogger(__name__)


class ProductNode(MPTTModel, TimeStampedModel):
    """Product taxonomy node."""

    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, related_name="children")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    obj = GenericForeignKey(
        "content_type",
        "object_id",
    )

    class MPTTMeta:
        level_attr = "level"

    # Tree-traversal methods for product-related models below.
    #
    # - Each of these methods accepts a single tree node and traverses its ascendants and
    #   descendants looking for a specific type of model. Since our hierarchy is strict (i.e. a
    #   Product will always be an ascendant of a Product Version), we can generalize the MPTT
    #   filter to get_family() and not have to write two different queries for fetching one
    #   node type from two different levels (e.g. get me all ProductVersions that are descendants
    #   of a Product, but also all ProductVersions that are ascendants of a ProductVariant).
    #
    # - Each method spits out all unique "name" attributes of all found node objects.
    #
    # - Each method assumes that an object model will always link to a single ProductNode (thus
    #   the use of `pnodes.first()`).
    #
    # - The `values_list()` query relies on the GenericRelation of each model's pnodes
    #   attribute's related query name.

    @staticmethod
    def get_products(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(Product))
            .values_list("product__name", flat=True)
            .distinct()
        )

    @staticmethod
    def get_product_versions(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(ProductVersion))
            .values_list("product_version__name", flat=True)
            .distinct()
        )

    @staticmethod
    def get_product_streams(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(ProductStream))
            .values_list("product_stream__name", flat=True)
            .distinct()
        )

    @staticmethod
    def get_product_variants(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(ProductVariant))
            .values_list("product_variant__name", flat=True)
            .distinct()
        )

    @staticmethod
    def get_channels(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(Channel))
            .values_list("channel__name", flat=True)
            .distinct()
        )


class ComponentNode(MPTTModel, TimeStampedModel):
    """Component taxonomy node."""

    class ComponentNodeType(models.TextChoices):
        SOURCE = "SOURCE"
        REQUIRES = "REQUIRES"
        PROVIDES = "PROVIDES"  # including bundled provides
        # eg. dev dependencies from Cachito builds
        # https://github.com/containerbuildsystem/cachito/#feature-definitions
        PROVIDES_DEV = "PROVIDES_DEV"

    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, related_name="children")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    obj = GenericForeignKey(
        "content_type",
        "object_id",
    )
    # TODO: This shadows built-in name "type" and creates a warning when updating openapi.yml
    type = models.CharField(
        choices=ComponentNodeType.choices, default=ComponentNodeType.SOURCE, max_length=20
    )
    # Saves an expensive django dereference into node object
    purl = models.CharField(max_length=1024, default="")

    class MPTTMeta:
        level_attr = "level"

    @property
    def name(self):
        return self.obj.name

    @property
    def desc(self):
        return self.desc.name

    def save(self, *args, **kwargs):
        if not self.purl:
            self.purl = self.obj.purl
        super().save(*args, **kwargs)


class Tag(models.Model):
    name = models.SlugField(
        max_length=200
    )  # Must not be empty; enforced by check constrain in child models.
    value = models.CharField(max_length=1024, default="")

    class Meta:
        abstract = True

    def __str__(self):
        if self.value:
            return f"{self.name}={self.value}"
        return self.name


class SoftwareBuild(TimeStampedModel):
    """Software build model

    This model represents metadata related to the process of building software artifacts
    (components) from a set of source code files.
    """

    class Type(models.TextChoices):
        BREW = "BREW"  # Red Hat Brew build system
        PNC = "PNC"  # Red Hat PNC (Project Newcastle) build system
        KOJI = "KOJI"  # Fedora's Koji build system, the upstream equivalent of Red Hat's Brew

    build_id = models.IntegerField(primary_key=True)
    type = models.CharField(choices=Type.choices, max_length=20)
    name = models.TextField()  # Arbitrary identifier for a build
    source = models.TextField()  # Source code reference for build
    # Store meta attributes relevant to different build system types.
    meta_attr = models.JSONField(default=dict)

    @property
    def components(self):
        return self.component_set.values_list("purl", flat=True)

    def save_datascore(self):
        for component in Component.objects.filter(software_build__build_id=self.build_id):
            component.save_datascore()
        return None

    def save_component_taxonomy(self):
        """it is only possible to update ('materialize') component taxonomy when all
        components (from a build) have loaded"""
        for component in Component.objects.filter(software_build__build_id=self.build_id):
            for cnode in component.cnodes.all():
                for d in cnode.get_descendants(include_self=True):
                    d.obj.save_component_taxonomy()
        return None

    def save_product_taxonomy(self):
        """update ('materialize') product taxonomy on all build components"""
        variant_ids = list(
            ProductComponentRelation.objects.filter(build_id=self.build_id)
            .filter(type=ProductComponentRelation.Type.ERRATA)
            .values_list("product_ref", flat=True)
            .distinct()
        )

        stream_ids = list(
            ProductComponentRelation.objects.filter(build_id=self.build_id)
            .filter(type=ProductComponentRelation.Type.COMPOSE)
            .values_list("product_ref", flat=True)
            .distinct()
        )

        product_details = get_product_details(variant_ids, stream_ids)

        for component in Component.objects.filter(software_build__build_id=self.build_id):
            # This is needed for container image builds which pull in components not
            # built at Red Hat, and therefore not assigned a build_id
            for d in component.cnodes.all().get_descendants(include_self=True):
                if not d.obj:
                    continue
                d.obj.product_variants = d.obj.get_product_variants()
                for a in ["products", "product_versions", "product_streams"]:
                    # Since we're only setting the product details for a specific build id we need
                    # to ensure we are only updating, not replacing the existing product details.
                    interim_set = set(getattr(d.obj, a))
                    interim_set.update(product_details[a])
                    setattr(d.obj, a, list(interim_set))
                d.obj.channels = d.obj.get_channels()
                d.obj.save()

        return None


class SoftwareBuildTag(Tag, TimeStampedModel):
    software_build = models.ForeignKey(SoftwareBuild, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "software_build")
            ),
        ]


class ProductModel(models.Model):
    """Abstract model that defines common fields for all product-related models."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    description = models.TextField(default="")
    version = models.CharField(max_length=1024, default="")
    meta_attr = models.JSONField(default=dict)
    ofuri = models.CharField(max_length=1024, default="")
    lifecycle_url = models.CharField(max_length=1024, default="")

    # Override each of the below on that model, e.g. products = None on the Product model
    products = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_streams = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_variants = fields.ArrayField(models.CharField(max_length=200), default=list)
    channels = fields.ArrayField(models.CharField(max_length=200), default=list)

    @staticmethod
    def get_related_components(build_ids: QuerySet, sort_field: str) -> QuerySet["Component"]:
        """Returns unique components with matching build_id, ordered by sort_field"""
        if build_ids:
            query = Q()
            for build_id in build_ids:
                query |= Q(software_build=build_id)
            # TODO get component descendants as well
            return Component.objects.filter(query).distinct().order_by(sort_field)
        # Else self.builds is an empty QuerySet
        return Component.objects.none()

    @staticmethod
    def get_product_component_relations(
        variant_ids: list[str], sort_field="external_system_id", only_errata=False
    ) -> QuerySet["ProductComponentRelation"]:
        """Returns unique productcomponentrelations with at least 1 matching variant_id,
        ordered by sort_field. if only_errata is True, only return PCRs with Type=ERRATA
        """
        if variant_ids:
            query = Q()
            for variant_id in variant_ids:
                query |= Q(product_ref__contains=variant_id)
            result = ProductComponentRelation.objects.filter(query).distinct().order_by(sort_field)
            if only_errata:
                result = result.filter(type=ProductComponentRelation.Type.ERRATA)
            return result
        # Else self.product_variants is an empty QuerySet
        return ProductComponentRelation.objects.none()

    @property
    def builds(self) -> QuerySet:
        """Return unique build IDs for errata with variant_ids matching self.product_variants"""
        return (
            self.get_product_component_relations(
                self.product_variants, "build_id", only_errata=True
            )
            .values_list("build_id", flat=True)
            .distinct()
        )

    @property
    def components(self) -> QuerySet["Component"]:
        """Return unique components with build_ids matching self.builds, ordered by purl"""
        # Return list of objs, not IDs like other props, so template can use obj props
        return self.get_related_components(self.builds, "purl")

    @property
    def errata(self) -> QuerySet:
        """Return unique errata IDs with variant_ids matching self.product_variants"""
        return (
            self.get_product_component_relations(self.product_variants, only_errata=True)
            .values_list("external_system_id", flat=True)
            .distinct()
        )

    @property
    def manifest(self) -> str:
        """Return an SPDX-style manifest in JSON format."""
        return ProductManifestFile(self).render_content()

    @property
    def upstream(self) -> QuerySet:
        """Return unique upstream values for components with build_ids matching self.builds"""
        # TODO: Need to add @extend_schema_field to fix drf-spectacular warning
        #  'unable to resolve type hint for function "upstream".
        #  Consider using a type hint or @extend_schema_field. Defaulting to string.'
        #  list or Iterable[str] work for drf-spectacular, but break mypy
        #  Union[anything, QuerySet] or QuerySet work for mypy, but not drf-spectacular
        # for RPMS its sibling, SRPM its child
        return (
            self.get_related_components(self.builds, "upstreams")
            .values_list("upstreams", flat=True)
            .distinct()
        )

    @property
    def coverage(self):
        if not self.pnodes.exists():
            return 0
        pnode_children = self.pnodes.first().get_children()
        if not pnode_children.exists():
            return 0
        has_build = 0
        for pn in pnode_children:
            if pn.obj.builds.exists():
                has_build += 1
        return round(has_build / pnode_children.count(), 2)

    def save_product_taxonomy(self):
        self.product_variants = ProductNode.get_product_variants(self)
        self.product_streams = ProductNode.get_product_streams(self)
        self.product_versions = ProductNode.get_product_versions(self)
        self.products = ProductNode.get_products(self)
        self.channels = ProductNode.get_channels(self)
        self.save()

    def save(self, *args, **kwargs):
        self.ofuri = self.get_ofuri()
        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self) -> str:
        return str(self.name)


class Product(ProductModel, TimeStampedModel):

    # Inherit product_versions, product_streams, and product_variants from ProductModel
    # Override only products which doesn't make sense for this model
    products = None  # type: ignore
    pnodes = GenericRelation(ProductNode, related_query_name="product")

    def get_ofuri(self) -> str:
        """Return product URI

        Ex.: o:redhat:rhel
        """
        return f"o:redhat:{self.name}"

    @property
    def cpes(self):
        cpes = []
        for p in self.pnodes.all().get_descendants():
            if hasattr(p.obj, "cpe"):
                cpes.append(p.obj.cpe)
        return list(set(cpes))


class ProductTag(Tag):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(name="unique_%(class)s", fields=("name", "value", "product")),
        ]


class ProductVersion(ProductModel, TimeStampedModel):

    product_versions = None  # type: ignore
    pnodes = GenericRelation(ProductNode, related_query_name="product_version")

    def get_ofuri(self) -> str:
        """Return product version URI.

        Ex.: o:redhat:rhel:8
        """
        version_name = re.sub(r"(-|_|)" + self.version + "$", "", self.name)
        return f"o:redhat:{version_name}:{self.version}"

    @property
    def cpes(self):
        cpes = []
        for p in self.pnodes.all().get_descendants():
            if hasattr(p.obj, "cpe"):
                cpes.append(p.obj.cpe)
        return list(set(cpes))


class ProductVersionTag(Tag):
    product_version = models.ForeignKey(
        ProductVersion, on_delete=models.CASCADE, related_name="tags"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "product_version")
            ),
        ]


class ProductStream(ProductModel, TimeStampedModel):

    cpe = models.CharField(max_length=1000, default="")

    product_streams = None  # type: ignore
    pnodes = GenericRelation(ProductNode, related_query_name="product_stream")

    @property
    def cpes(self) -> list[str]:
        return [self.cpe]

    def get_ofuri(self) -> str:
        """Return product stream URI

        Ex.: o:redhat:rhel:8.2.eus

        TODO: name embeds more then version ... need discussion
        """
        stream_name = re.sub(r"(-|_|)" + self.version + "$", "", self.name)
        return f"o:redhat:{stream_name}:{self.version}"


class ProductStreamTag(Tag):
    product_stream = models.ForeignKey(ProductStream, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "product_stream")
            ),
        ]


class ProductVariant(ProductModel, TimeStampedModel):
    """Product Variant model

    This directly relates to Errata Tool Variants which are mapped then mapped to CDN
    repositories for content that is shipped as RPMs.
    """

    cpe = models.CharField(max_length=1000, default="")

    product_variants = None  # type: ignore
    pnodes = GenericRelation(ProductNode, related_query_name="product_variant")

    @property
    def cpes(self) -> list[str]:
        return [self.cpe]

    def get_ofuri(self) -> str:
        """Return product variant URI

        Ex.: o:redhat:rhel:8.2.eus
        """

        return f"o:redhat:{self.name}:{self.version}"

    @property
    def errata(self) -> QuerySet:
        """Return unique errata IDs with variant_ids matching this variant's name"""
        return (
            self.get_product_component_relations([self.name], only_errata=True)
            .values_list("external_system_id", flat=True)
            .distinct()
        )

    @property
    def builds(self) -> QuerySet:
        """Return unique build IDs for errata with variant_ids matching this variant's name"""
        return (
            self.get_product_component_relations([self.name], "build_id", only_errata=True)
            .values_list("build_id", flat=True)
            .distinct()
        )


class ProductVariantTag(Tag):
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="tags"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "product_variant")
            ),
        ]


class Channel(TimeStampedModel):
    """A model that represents a specific type of delivery channel.

    A Channel is essentially the "location" of where a specific artifact is available from to
    customers.
    """

    class Type(models.TextChoices):
        CDN_REPO = "CDN_REPO"  # Main delivery channel for RPMs
        CONTAINER_REGISTRY = "CONTAINER_REGISTRY"  # Registries, e.g.: registry.redhat.io, quay.io

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    type = models.CharField(choices=Type.choices, max_length=50)
    description = models.TextField(default="")
    meta_attr = models.JSONField(default=dict)
    pnodes = GenericRelation(ProductNode, related_query_name="channel")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return str(self.name)


class ProductComponentRelation(TimeStampedModel):
    """class to be used for linking taxonomies"""

    class Type(models.TextChoices):
        ERRATA = "ERRATA"
        COMPOSE = "COMPOSE"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(choices=Type.choices, max_length=50)
    meta_attr = models.JSONField(default=dict)

    external_system_id = models.CharField(
        max_length=200, default=""
    )  # ex. if Errata this would be errata_id
    # eg. product_variant for ERRATA, or product_stream for COMPOSE
    product_ref = models.CharField(max_length=200, default="")
    build_id = models.CharField(max_length=200, default="")

    class Meta:
        ordering = ("external_system_id",)
        constraints = [
            models.UniqueConstraint(
                name="unique_productcomponentrelation",
                fields=("external_system_id", "product_ref", "build_id"),
            ),
        ]
        indexes = [models.Index(fields=("external_system_id", "product_ref", "build_id"))]


def get_product_streams_from_variants(variant_ids: list[str]):
    product_variants = ProductVariant.objects.filter(name__in=variant_ids)
    product_streams = []
    for pv in product_variants:
        product_streams.extend(ProductNode.get_product_streams(pv))
    return list(set(product_streams))


def get_product_details(variant_ids: list[str], stream_ids: list[str]) -> dict[str, set[str]]:
    if variant_ids:
        stream_ids.extend(get_product_streams_from_variants(variant_ids))
    product_streams = ProductStream.objects.filter(name__in=stream_ids)
    product_details = defaultdict(set)
    for product_stream in product_streams:
        for ancestor in product_stream.pnodes.all().get_ancestors(include_self=True):
            if type(ancestor.obj) is Product:
                product_details["products"].add(ancestor.obj.ofuri)
            if type(ancestor.obj) is ProductVersion:
                product_details["product_versions"].add(ancestor.obj.ofuri)
            if type(ancestor.obj) is ProductStream:
                product_details["product_streams"].add(ancestor.obj.ofuri)
    return product_details


class Component(TimeStampedModel):
    class Type(models.TextChoices):
        CONTAINER_IMAGE = "CONTAINER_IMAGE"
        GOLANG = "GOLANG"
        MAVEN = "MAVEN"
        NPM = "NPM"
        RHEL_MODULE = "RHEL_MODULE"
        RPM = "RPM"
        SRPM = "SRPM"
        PYPI = "PYPI"
        UNKNOWN = "UNKNOWN"
        UPSTREAM = "UPSTREAM"

    class Namespace(models.TextChoices):
        UPSTREAM = "UPSTREAM"
        REDHAT = "REDHAT"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    description = models.TextField()
    meta_attr = models.JSONField(default=dict)

    type = models.CharField(choices=Type.choices, max_length=20)
    version = models.CharField(max_length=1024)
    release = models.CharField(max_length=1024, default="")
    arch = models.CharField(max_length=1024, default="")

    purl = models.CharField(max_length=1024, default="")
    nvr = models.CharField(max_length=1024, default="")
    nevra = models.CharField(max_length=1024, default="")

    products = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_streams = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_variants = fields.ArrayField(models.CharField(max_length=200), default=list)
    channels = fields.ArrayField(models.CharField(max_length=200), default=list)
    provides = fields.ArrayField(models.CharField(max_length=1024), default=list)
    sources = fields.ArrayField(models.CharField(max_length=1024), default=list)
    upstreams = fields.ArrayField(models.CharField(max_length=1024), default=list)

    cnodes = GenericRelation(ComponentNode)
    software_build = models.ForeignKey(SoftwareBuild, on_delete=models.CASCADE, null=True)
    # The raw "summary license string" without any transformations
    # This information comes from the build system via an RPM spec file, Maven POM file, etc.
    # Use the license_expression property for almost any usage, not this field directly
    license = models.TextField(default="")
    # The filename of the source rpm package, or similar, from the meta_attr / build system
    filename = models.TextField(default="")

    related_url = models.CharField(max_length=1024, default="", null=True)

    data_score = models.IntegerField(default=0)
    data_report = fields.ArrayField(models.CharField(max_length=200), default=list)

    class Meta:
        verbose_name = f"Component({type})"
        ordering = (
            "type",
            "name",
        )
        constraints = [
            models.UniqueConstraint(
                name="unique_components",
                fields=("name", "type", "arch", "version", "release"),
            ),
        ]
        indexes = [
            models.Index(fields=("name", "type", "arch", "version", "release")),
        ]

    def __str__(self) -> str:
        """return name"""
        return str(self.name)

    def get_purl(self):
        if self.type in (Component.Type.RPM, Component.Type.SRPM):
            # SRPM/RPM
            evr = ""
            qualifiers = {
                "arch": self.arch,
            }
            if self.epoch:
                evr = f"{self.epoch}:"
                qualifiers["epoch"] = str(self.epoch)
            evr = f"{evr}{self.version}-{self.release}"
            return PackageURL(
                type=self.type,
                namespace=self.Namespace.REDHAT.lower(),
                name=self.name,
                version=evr,
                qualifiers=qualifiers,
            )
        elif self.type == Component.Type.CONTAINER_IMAGE:
            digest = None
            if self.meta_attr.get("digests"):
                for digest_fmt in CONTAINER_DIGEST_FORMATS:
                    digest = self.meta_attr["digests"].get(digest_fmt)
                    if digest:
                        break
            return PackageURL(
                type="container",
                namespace=self.Namespace.REDHAT.lower(),
                name=self.name,
                version=f"{self.version}",
                qualifiers={
                    "arch": self.arch,
                    "digest": digest,
                },
                subpath=None,
            )
        elif self.type == Component.Type.UPSTREAM:
            ns = self.meta_attr.get("url", "")
            if "//" in ns:
                ns = ns.split("//")[1]
                # TODO: below does not use ns??
            return PackageURL(
                type="upstream",
                name=self.name,
                version=f"{self.version}",
                subpath=None,
            )
        else:
            # unknown
            return PackageURL(
                type=self.type,
                name=self.name,
                version=f"{self.version}",
            )

    def save_component_taxonomy(self):
        self.upstreams = self.get_upstreams()
        self.provides = self.get_provides()
        self.sources = self.get_source()
        self.save()

    def get_nvr(self) -> str:
        return f"{self.name}-{self.version}-{self.release}"

    def get_nevra(self) -> str:
        return (
            f"{self.name}{f':{self.epoch}' if self.epoch else ''}"
            f"-{self.version}-{self.release}.{self.arch}"
        )

    def save(self, *args, **kwargs):
        self.nvr = self.get_nvr()
        self.nevra = self.get_nevra()
        purl = self.get_purl()
        self.purl = purl.to_string() if purl else ""
        super().save(*args, **kwargs)

    @property
    def get_roots(self) -> list[ComponentNode]:
        """Return component root entities."""
        roots = []

        for cnode in self.cnodes.all():
            root = cnode.get_root()
            if root.obj.type == Component.Type.CONTAINER_IMAGE:
                # TODO if we change the CONTAINER->RPM ComponentNode.type to something other than
                # 'PROVIDES' we would check for that type here to prevent 'hardcoding' the
                # container -> rpm relationship here.

                # RPMs are included as children of Containers as well as SRPMs
                # We don't want to include Containers in the RPMs roots.
                # Partly because RPMs in containers can have unprocesses SRPMs
                # And partly because we use roots to find upstream components,
                # and it's not true to say that rpms share upstreams with containers
                rpm_desendant = False
                for ancestor in cnode.get_ancestors(include_self=True):
                    if ancestor.obj.type == Component.Type.RPM:
                        rpm_desendant = True
                if not rpm_desendant:
                    roots.append(root)
            else:
                roots.append(root)
        return roots

    @property
    def build_meta(self):
        return self.software_build.meta_attr

    @property
    def errata(self) -> list[str]:
        """Return errata that contain component."""
        if not self.software_build:
            return []
        component_name = ""
        if self.type in [Component.Type.RPM, Component.Type.SRPM]:
            component_name = f"{self.nevra}.rpm"
        errata_qs = (
            ProductComponentRelation.objects.filter(
                type=ProductComponentRelation.Type.ERRATA,
                build_id=self.software_build.build_id,
                meta_attr__components__contains=component_name,
            )
            .values_list("external_system_id", flat=True)
            .distinct()
        )
        return list(erratum for erratum in errata_qs if erratum)

    @property
    def license_expression(self) -> str:
        """Return the "summary license string" formatted as an SPDX license expression
        This is almost the same as above, but operators (AND, OR) + license IDs are uppercased"""
        return self.license.upper()

    @property
    def license_list(self) -> list[str]:
        """Return a list of any possibly-relevant licenses. No information is given about which apply
        To see if all apply or if you may choose between them, parse the license expression above"""
        # "words".split("not present in string") will return ["words"]
        # AKA below will always add one level of nesting to the array
        license_parts = self.license_expression.split(" AND ")
        # Flatten it back to a list[str] in one line to fix mypy errors
        license_parts = [nested for part in license_parts for nested in part.split(" OR ")]

        return [part.strip("()") for part in license_parts]

    @property
    def epoch(self) -> str:
        return self.meta_attr.get("epoch", "")

    def get_product_variants(self):
        build_ids = [
            a.obj.software_build.build_id
            for a in self.cnodes.all().get_ancestors(include_self=True)
            if a.obj.software_build
        ]
        return list(
            ProductComponentRelation.objects.filter(build_id__in=set(build_ids))
            .filter(type=ProductComponentRelation.Type.ERRATA)
            .values_list("product_ref", flat=True)
            .distinct()
        )

    def get_product_streams(self):
        build_ids = [
            a.obj.software_build.build_id
            for a in self.cnodes.all().get_ancestors(include_self=True)
            if a.obj.software_build
        ]
        return list(
            ProductComponentRelation.objects.filter(build_id__in=set(build_ids))
            .filter(type=ProductComponentRelation.Type.COMPOSE)
            .values_list("product_ref", flat=True)
            .distinct()
        )

    def get_channels(self):
        variant_ids = self.product_variants
        if not variant_ids:
            return []
        query = Q()
        for variant_id in variant_ids:
            query = query | Q(name__contains=variant_id)
        product_variants = ProductVariant.objects.filter(query)
        channels = []
        for product_variant in product_variants:
            for descendant in product_variant.pnodes.all().get_descendants():
                if type(descendant.obj) in [Channel]:
                    channels.append(descendant.obj.name)
        return list(set(channels))

    # TODO remove this? Force the use of SoftwareBuild.save_product_taxnonomy instead
    def save_product_taxonomy(self):
        variant_ids = self.get_product_variants()
        stream_ids = self.get_product_streams()

        if not variant_ids and not stream_ids:
            return []

        product_details = get_product_details(variant_ids, stream_ids)

        # Since we have all variant ids for this component, we can replace any existing product
        # details
        self.products = list(set(product_details["products"]))
        self.product_versions = list(set(product_details["product_versions"]))
        self.product_streams = list(set(product_details["product_streams"]))
        self.channels = self.get_channels()
        self.product_variants = list(set(variant_ids))
        self.save()

    def get_provides(self, include_dev=True):
        """return all descendants with PROVIDES ComponentNode type"""
        provides = []
        type_list = [ComponentNode.ComponentNodeType.PROVIDES]
        if include_dev:
            type_list.append(ComponentNode.ComponentNodeType.PROVIDES_DEV)
        for descendant in self.cnodes.all().get_descendants().filter(type__in=type_list):
            provides.append(descendant.purl)
        return list(set(provides))

    def get_source(self):
        """return all root nodes"""
        # this uses the mptt get_root function, not the get_root property defined on Component
        return [cnode.get_root().purl for cnode in self.cnodes.all()]

    def get_upstreams(self):
        """return upstreams component ancestors in family trees"""
        roots = self.get_roots
        if not roots:
            return []
        upstreams = []
        for root in roots:
            # For SRRPM/RPMS, and noarch containers, these are the cnodes we want.
            source_children = [
                c for c in root.get_children() if c.type == ComponentNode.ComponentNodeType.SOURCE
            ]
            # Cachito builds nest components under the relevant source component for that
            # container build, eg. buildID=1911112. In that case we need to walk up the tree from
            # the current node to find its relevant source
            if (
                root.obj.type == Component.Type.CONTAINER_IMAGE
                and root.obj.arch == "noarch"
                and len(source_children) > 1
            ):
                upstreams.extend(
                    [
                        a.purl
                        for a in self.cnodes.all().get_ancestors(include_self=True)
                        if a.type == ComponentNode.ComponentNodeType.SOURCE
                        and a.obj.type == Component.Type.UPSTREAM
                    ]
                )
            else:
                upstreams.extend(c.purl for c in source_children)
        return list(set(upstreams))

    def save_datascore(self):
        score = 0
        report = set()
        if self.type == Component.Type.UPSTREAM:
            if not self.related_url:
                score += 20
                report.add("upstream has no related_url")
        if not self.description:
            score += 10
            report.add("no description")
        if not self.version:
            score += 10
            report.add("no version")
        if not self.license:
            score += 10
            report.add("no license")
        if not self.products:
            score += 10
            report.add("no products")
        if not self.product_versions:
            score += 10
            report.add("no product versions")
        if not self.product_streams:
            score += 10
            report.add("no product streams")
        if not self.product_variants:
            score += 20
            report.add("no product variants")
        if not self.sources:
            score += 10
            report.add("no sources")
        if not self.provides:
            score += 10
            report.add("no provides")
        if not self.upstreams:
            score += 10
            report.add("no upstream")
        if not self.software_build:
            score += 10
            report.add("no software build")
        self.data_score = score
        self.data_report = list(report)
        self.save()


class ComponentTag(Tag):
    component = models.ForeignKey(Component, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(name="unique_%(class)s", fields=("name", "value", "component")),
        ]


class AppStreamLifeCycle(TimeStampedModel):
    """LifeCycle model based on lifecycle-defs repo in CEE Gitlab"""

    class LifeCycleType(models.TextChoices):
        MODULE = "module"
        PACKAGE = "package"
        SCL = "scl"

    class LifeCycleSource(models.TextChoices):
        DEFAULT = "default"
        PREVIOUS_RELEASE = "previous_release"
        PRP = "prp"
        CONFIRMED = "confirmed"
        OVERRIDE = "override"

    # Name could be a module, a package, or a collection (as seen in the compose)
    name = models.TextField()
    type = models.CharField(
        choices=LifeCycleType.choices, default=LifeCycleType.PACKAGE, max_length=50
    )
    lifecycle = models.IntegerField()
    # Application Compatibility Guide level (1-4)
    # See docs in the lifecycle-defs repo
    acg = models.IntegerField()
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)
    # FIXME: ideally 'product', 'initial_product_version', 'stream' should be
    # foreignkey to corresponding models, however we don't have enough data yet.
    # Data records from `lifecycle-defs` repo could not be used to populate
    # product/version/stream as the repo is not a reliable source: there
    # are inconsistent entries which would result in dirty data if relations
    # are created based on the data there.
    # product = models.ForeignKey('Product', on_delete=models.CASCADE)
    # initial_product_version = models.ForeignKey('ProductVersion', on_delete=models.CASCADE)
    # stream = models.ForeignKey('ProductStream', on_delete=models.CASCADE)
    product = models.TextField()
    initial_product_version = models.TextField()
    stream = models.TextField()
    source = models.CharField(choices=LifeCycleSource.choices, max_length=50)
    # True if the resulting definition should be omitted from public view
    private = models.BooleanField()
    meta_attr = models.JSONField(default=dict)

    def is_rolling_appstream(self):
        return self.lifecycle == 0

    def is_dependent_appstream(self):
        return self.lifecycle == -1

    def __str__(self) -> str:
        return f"{self.name}@{self.initial_product_version}.{self.stream}"

    class Meta:
        constraints = [
            # FIXME: value range is what the 'lifecycle-defs' repo suggests, but there are
            # exceptional cases in the yaml which fail the range constraint.
            # models.CheckConstraint(
            #     check=models.Q(acg__gte=1) & models.Q(acg__lte=4),
            #     name="An acg value is valid between 1 and 4",
            # ),
            # models.CheckConstraint(
            #     check=models.Q(lifecycle__gte=-1) & models.Q(lifecycle__lte=10),
            #     name="A lifecycle value is valid between -1 and 10",
            # ),
            models.UniqueConstraint(
                # assuming below fields are 'unique_together'.
                fields=["name", "type", "product", "initial_product_version", "stream"],
                name="unique_lifecycle_entity",
            )
        ]
        ordering = ["name"]
