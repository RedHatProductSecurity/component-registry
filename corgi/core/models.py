import logging
import re
import uuid as uuid_class
from abc import abstractmethod
from typing import Optional, Union

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres import fields
from django.db import models
from django.db.models import F, Func, IntegerField, QuerySet, TextField, Value
from django.utils import timezone
from mptt.managers import TreeManager
from mptt.models import MPTTModel, TreeForeignKey
from packageurl import PackageURL

from corgi.core.constants import (
    CONTAINER_DIGEST_FORMATS,
    MODEL_NODE_LEVEL_MAPPING,
    ROOT_COMPONENTS_CONDITION,
    SRPM_CONDITION,
)
from corgi.core.files import ComponentManifestFile, ProductManifestFile
from corgi.core.mixins import TimeStampedModel

logger = logging.getLogger(__name__)


class NodeManager(TreeManager):
    """Custom manager to remove ordering from TreeQuerySets (cnodes and pnodes)
    to allow calling .distinct() without first calling .order_by() every time
    """

    def get_queryset(self, *args, **kwargs):
        return super().get_queryset(*args, **kwargs).order_by()


class NodeModel(MPTTModel, TimeStampedModel):
    """Generic model for component and product taxonomies
    that factors out some common behavior and adds some helper logic"""

    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, related_name="children")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    obj = GenericForeignKey(
        "content_type",
        "object_id",
    )

    objects = NodeManager()

    class MPTTMeta:
        level_attr = "level"
        root_node_ordering = False

    class Meta:
        abstract = True
        indexes = (
            models.Index(fields=("object_id", "parent")),
            # Add index on foreign-key fields here, to speed up iterating over cnodes / pnodes
            # GenericForeignKey doesn't get these by default, only ForeignKey
            models.Index(fields=("content_type", "object_id")),
        )

    @property
    def name(self):
        return self.obj.name

    @property
    def desc(self):
        return self.obj.description


class ProductNode(NodeModel):
    """Product taxonomy node."""

    class Meta(NodeModel.Meta):
        constraints = (
            # Add unique constraint + index so get_or_create behaves atomically
            # Otherwise duplicate rows may be inserted into DB
            # Second constraint needed for case when parent is NULL
            # https://dba.stackexchange.com/a/9760
            models.UniqueConstraint(
                name="unique_pnode_get_or_create",
                fields=("object_id", "parent"),
                condition=models.Q(parent__isnull=False),
            ),
            models.UniqueConstraint(
                name="unique_pnode_get_or_create_for_null_parent",
                fields=("object_id",),
                condition=models.Q(parent__isnull=True),
            ),
        )

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
    def get_node_pks_for_type(
        qs: QuerySet["ProductNode"], mapping_model: type, lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        """For a given ProductNode queryset, find all nodes with the given type
        and return a lookup on their related objects (primary key by default)"""
        # "ProductVersion" for mapping, "productversion__pk" for field lookup
        mapping_key = mapping_model.__name__
        qs = qs.filter(level=MODEL_NODE_LEVEL_MAPPING[mapping_key])
        if lookup:
            target_model = mapping_key.lower()
            qs = qs.values_list(f"{target_model}{lookup}", flat=True)
        # else client code needs whole Product instances, not their PKs
        # No way to return "obj" / "productstream" field as a model instance here
        # ManyToManyFields can use PKs, but ForeignKeys require model instances
        return qs

    @staticmethod
    def get_node_names_for_type(product_model: "ProductModel", target_model: str) -> QuerySet:
        """For a given ProductModel / Channel, find all related nodes with the given type
        and return the names of their related objects"""
        # "product_version" -> "ProductVersion"
        mapping_model = target_model.title().replace("_", "")
        # No .distinct() since __name on all ProductModel subclasses + Channel is always unique
        return (
            product_model.pnodes.first()
            .get_family()
            .filter(level=MODEL_NODE_LEVEL_MAPPING[mapping_model])
            .values_list(f"{target_model}__name", flat=True)
        )

    @classmethod
    def get_products(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, Product, lookup=lookup)

    @classmethod
    def get_product_versions(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, ProductVersion, lookup=lookup)

    @classmethod
    def get_product_streams(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, ProductStream, lookup=lookup)

    @classmethod
    def get_product_variants(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, ProductVariant, lookup=lookup)

    @classmethod
    def get_channels(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, Channel, lookup=lookup)


class ComponentNode(NodeModel):
    """Component taxonomy node."""

    class ComponentNodeType(models.TextChoices):
        SOURCE = "SOURCE"
        REQUIRES = "REQUIRES"
        PROVIDES = "PROVIDES"  # including bundled provides
        # eg. dev dependencies from Cachito builds
        # https://github.com/containerbuildsystem/cachito/#feature-definitions
        PROVIDES_DEV = "PROVIDES_DEV"

    # TODO: This shadows built-in name "type" and creates a warning when updating openapi.yml
    type = models.CharField(
        choices=ComponentNodeType.choices, default=ComponentNodeType.SOURCE, max_length=20
    )
    # Saves an expensive django dereference into node object
    purl = models.CharField(max_length=1024, default="")

    class Meta(NodeModel.Meta):
        constraints = (
            # Add unique constraint + index so get_or_create behaves atomically
            # Otherwise duplicate rows may be inserted into DB
            # Second constraint needed for case when parent is NULL
            # https://dba.stackexchange.com/a/9760
            models.UniqueConstraint(
                name="unique_cnode_get_or_create",
                fields=("type", "parent", "purl"),
                condition=models.Q(parent__isnull=False),
            ),
            models.UniqueConstraint(
                name="unique_cnode_get_or_create_for_null_parent",
                fields=("type", "purl"),
                condition=models.Q(parent__isnull=True),
            ),
        )
        # 0037_custom_indexes.py contains the following custom performance indexes
        #    core_componentnode_tree_parent_lft_idx
        #    core_cn_tree_lft_purl_parent_idx
        #    core_cn_lft_tree_idx
        #    core_cn_lft_rght_tree_idx
        indexes = (  # type: ignore[assignment]
            models.Index(fields=("type", "parent", "purl")),
            models.Index(fields=("parent",)),
            models.Index(fields=("purl",)),
            *NodeModel.Meta.indexes,
        )

    def save(self, *args, **kwargs):
        self.purl = self.obj.purl
        super().save(*args, **kwargs)


class Tag(TimeStampedModel):
    name = models.SlugField(max_length=200)  # Must not be empty
    value = models.CharField(max_length=1024, default="")

    @property
    @abstractmethod
    def tagged_model(self):
        pass

    class Meta:
        abstract = True
        constraints = (
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "tagged_model")
            ),
        )

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
        KOJI = "KOJI"  # Fedora's Koji build system, the upstream equivalent of Red Hat's Brew

    build_id = models.IntegerField(primary_key=True)
    type = models.CharField(choices=Type.choices, max_length=20)
    name = models.TextField()  # Arbitrary identifier for a build
    source = models.TextField()  # Source code reference for build
    completion_time = models.DateTimeField(null=True)  # meta_attr["completion_time"]
    # Store meta attributes relevant to different build system types.
    meta_attr = models.JSONField(default=dict)
    # implicit field "components" from Component model's ForeignKey
    components: models.Manager["Component"]

    class Meta:
        indexes = (models.Index(fields=("completion_time",)),)

    def save_datascore(self):
        for component in self.components.get_queryset():
            component.save_datascore()
        return None

    def save_product_taxonomy(self, start_time: Optional[timezone.datetime] = None):
        """update ('materialize') product taxonomy on all build components

        This method is defined on SoftwareBuild and not Component,
        because the ProductComponentRelation table refers to build IDs,
        which we use to look up which products a certain component should be linked to
        """
        variant_names = tuple(
            ProductComponentRelation.objects.filter(
                build_id=self.build_id,
                type__in=ProductComponentRelation.VARIANT_TYPES,
            )
            .values_list("product_ref", flat=True)
            .distinct()
        )

        stream_names = list(
            ProductComponentRelation.objects.filter(
                build_id=self.build_id,
                type__in=ProductComponentRelation.STREAM_TYPES,
            )
            .values_list("product_ref", flat=True)
            .distinct()
        )

        product_details = get_product_details(variant_names, stream_names)

        components = set()
        for component in self.components.get_queryset():
            # This is needed for container image builds which pull in components not
            # built at Red Hat, and therefore not assigned a build_id
            for d in component.cnodes.get_queryset().get_descendants(include_self=True):
                if not d.obj:
                    continue
                components.add(d.obj)

        for component in components:
            component.save_product_taxonomy(product_details)

        return None


class SoftwareBuildTag(Tag):
    tagged_model = models.ForeignKey(SoftwareBuild, on_delete=models.CASCADE, related_name="tags")


class ProductModel(TimeStampedModel):
    """Abstract model that defines common fields for all product-related models."""

    uuid = models.UUIDField(primary_key=True, default=uuid_class.uuid4, editable=False)
    name = models.TextField(unique=True)
    description = models.TextField(default="")
    version = models.CharField(max_length=1024, default="")
    meta_attr = models.JSONField(default=dict)
    ofuri = models.CharField(max_length=1024, default="")
    lifecycle_url = models.CharField(max_length=1024, default="")

    pnodes = GenericRelation(ProductNode, related_query_name="%(class)s")

    # below fields are added implicitly to all ProductModel subclasses
    # using Django reverse relations, but mypy needs an explicit type hint
    @property
    @abstractmethod
    def products(self) -> Union["Product", models.ForeignKey]:
        pass

    @property
    @abstractmethod
    def productversions(
        self,
    ) -> Union["ProductVersion", models.ForeignKey, models.Manager["ProductVersion"]]:
        pass

    @property
    @abstractmethod
    def productstreams(
        self,
    ) -> Union["ProductStream", models.ForeignKey, models.Manager["ProductStream"]]:
        pass

    @property
    @abstractmethod
    def productvariants(self) -> Union["ProductVariant", models.Manager["ProductVariant"]]:
        pass

    @property
    @abstractmethod
    def channels(self) -> models.Manager["Channel"]:
        pass

    @property
    @abstractmethod
    def components(self) -> models.Manager["Component"]:
        pass

    @property
    def builds(self) -> QuerySet:
        """Returns unique productcomponentrelations with at least 1 matching variant or stream,
        ordered by build_id.
        """
        # TODO: Can just return self.components.values_list("software_build", flat=True).distinct()
        product_refs = [self.name]
        if isinstance(self, ProductStream):
            # we also want to include child product variants of this product stream
            product_refs.extend(self.productvariants.values_list("name", flat=True))
        elif isinstance(self, ProductVersion) or isinstance(self, Product):
            # we don't store products or product versions in the relations table therefore
            # we only want to include child product streams and product variants in the query
            product_refs.extend(self.productstreams.values_list("name", flat=True))
            product_refs.extend(self.productvariants.values_list("name", flat=True))
        # else it was a product variant, only look up by self.name

        if product_refs:
            return (
                ProductComponentRelation.objects.filter(product_ref__in=product_refs)
                .values_list("build_id", flat=True)
                .distinct()
            )
        # Else no product variants or product streams - should never happen
        return ProductComponentRelation.objects.none()

    @property
    def coverage(self) -> int:
        if not self.pnodes.exists():
            return 0
        pnode_children = self.pnodes.get_queryset().get_descendants()
        if not pnode_children.exists():
            return 0
        has_build = 0
        for pn in pnode_children:
            if pn.obj.builds.exists():
                has_build += 1
        return round(has_build / pnode_children.count(), 2)

    @property
    def cpes(self) -> tuple[str, ...]:
        """Return CPEs for child streams / variants."""
        # include_self=True so we don't have to override this property for streams
        descendants = self.pnodes.get_queryset().get_descendants(include_self=True)
        stream_cpes = (
            descendants.filter(level=MODEL_NODE_LEVEL_MAPPING["ProductStream"])
            .values_list("productstream__cpe", flat=True)
            .distinct()
        )
        variant_cpes = (
            descendants.filter(level=MODEL_NODE_LEVEL_MAPPING["ProductVariant"])
            .values_list("productvariant__cpe", flat=True)
            .distinct()
        )
        # Omit CPEs like "", but GenericForeignKeys only support .filter(), not .exclude()??
        return tuple(cpe for cpe in (*stream_cpes, *variant_cpes) if cpe)

    @abstractmethod
    def get_ofuri(self) -> str:
        pass

    def save_product_taxonomy(self):
        """Save links between related ProductModel subclasses"""
        family = self.pnodes.first().get_family()
        # Get obj from raw nodes - no way to return related __product obj in values_list()
        products = ProductNode.get_products(family, lookup="").first().obj
        productversions = tuple(
            node.obj for node in ProductNode.get_product_versions(family, lookup="")
        )
        productstreams = tuple(
            node.obj for node in ProductNode.get_product_streams(family, lookup="")
        )
        productvariants = tuple(
            node.obj for node in ProductNode.get_product_variants(family, lookup="")
        )
        channels = ProductNode.get_channels(family)

        # Avoid setting fields on models which don't have them
        # Also set fields correctly based on which side of the relationship we see
        # forward relationship like "versions -> products" assigns a single object
        # reverse relationship like "products -> versions" calls .set() with many objects
        if isinstance(self, Product):
            self.productversions.set(productversions)
            self.productstreams.set(productstreams)
            self.productvariants.set(productvariants)

        elif isinstance(self, ProductVersion):
            self.products = products  # Should be only one parent object
            self.productstreams.set(productstreams)
            self.productvariants.set(productvariants)
            self.save()

        elif isinstance(self, ProductStream):
            self.products = products
            self.productversions = productversions[0]
            self.productvariants.set(productvariants)
            self.save()

        elif isinstance(self, ProductVariant):
            self.products = products
            self.productversions = productversions[0]
            self.productstreams = productstreams[0]
            self.save()

        else:
            raise NotImplementedError(
                f"Add behavior for class {type(self)} in ProductModel.save_product_taxonomy()"
            )

        # All ProductModels have a set of descendant channels
        self.channels.set(channels)

    def save(self, *args, **kwargs):
        self.ofuri = self.get_ofuri()
        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        indexes = (models.Index(fields=("ofuri",)),)

    def __str__(self) -> str:
        return str(self.name)


class Product(ProductModel):
    @property
    def products(self) -> "Product":
        return self

    # implicit "productversions" field on Product model
    # is created by products field on ProductVersion model
    productversions: models.Manager["ProductVersion"]

    # implicit "productstreams" field on Product model
    # is created by products field on ProductStream model
    productstreams: models.Manager["ProductStream"]

    # implicit "productvariants" field on Product model
    # is created by products field on ProductVariant model
    productvariants: models.Manager["ProductVariant"]

    # implicit "channels" field on Product model
    # is created by products field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on Product model
    # is created by products field on Component model
    components: models.Manager["Component"]

    def get_ofuri(self) -> str:
        """Return product URI

        Ex.: o:redhat:rhel
        """
        return f"o:redhat:{self.name}"


class ProductTag(Tag):
    tagged_model = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="tags")


class ProductVersion(ProductModel):

    products = models.ForeignKey(
        "Product", on_delete=models.CASCADE, related_name="productversions"
    )

    @property
    def productversions(self) -> "ProductVersion":
        return self

    # implicit "productstreams" field on ProductVersion model
    # is created by productversions field on ProductStream model
    productstreams: models.Manager["ProductStream"]

    # implicit "productvariants" field on ProductVersion model
    # is created by productversions field on ProductVariant model
    productvariants: models.Manager["ProductVariant"]

    # implicit "channels" field on ProductVersion model
    # is created by productversions field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on ProductVersion model
    # is created by productversions field on Component model
    components: models.Manager["Component"]

    def get_ofuri(self) -> str:
        """Return product version URI.

        Ex.: o:redhat:rhel:8
        """
        version_name = re.sub(r"(-|_|)" + self.version + "$", "", self.name)
        return f"o:redhat:{version_name}:{self.version}"


class ProductVersionTag(Tag):
    tagged_model = models.ForeignKey(ProductVersion, on_delete=models.CASCADE, related_name="tags")


class ProductStream(ProductModel):

    cpe = models.CharField(max_length=1000, default="")

    # NOTE brew_tags and yum_repositories values shouldn't be exposed outside of Red Hat
    brew_tags = models.JSONField(default=dict)
    yum_repositories = fields.ArrayField(models.CharField(max_length=200), default=list)

    composes = models.JSONField(default=dict)
    active = models.BooleanField(default=False)
    et_product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)

    products = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="productstreams")
    productversions = models.ForeignKey(
        "ProductVersion",
        on_delete=models.CASCADE,
        related_name="productstreams",
    )

    @property
    def productstreams(self) -> "ProductStream":
        return self

    # implicit "productvariants" field on ProductStream model
    # is created by productstreams field on ProductVariant model
    productvariants: models.Manager["ProductVariant"]

    # implicit "channels" field on ProductStream model
    # is created by productstreams field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on ProductStream model
    # is created by productstreams field on Component model
    components: models.Manager["Component"]

    def get_ofuri(self) -> str:
        """Return product stream URI

        Ex.: o:redhat:rhel:8.2.eus

        TODO: name embeds more then version ... need discussion
        """
        stream_name = re.sub(r"(-|_|)" + self.version + "$", "", self.name)
        return f"o:redhat:{stream_name}:{self.version}"

    @property
    def manifest(self) -> str:
        """Return an SPDX-style manifest in JSON format."""
        return ProductManifestFile(self).render_content()

    def get_latest_components(self):
        """Return root components from latest builds."""
        return (
            Component.objects.filter(
                ROOT_COMPONENTS_CONDITION,
                productstreams__ofuri=self.ofuri,
            )
            .exclude(software_build__isnull=True)
            .annotate(
                latest=models.Subquery(
                    Component.objects.filter(
                        ROOT_COMPONENTS_CONDITION,
                        name=models.OuterRef("name"),
                        productstreams__ofuri=self.ofuri,
                    )
                    .exclude(software_build__isnull=True)
                    .annotate(
                        version_arr=Func(
                            F("version"),
                            Value("[.,-]"),
                            function="regexp_split_to_array",
                            output=fields.ArrayField(TextField()),
                        ),
                        release_arr=Func(
                            F("release"),
                            Value("[.,-]"),
                            function="regexp_split_to_array",
                            output=fields.ArrayField(TextField()),
                        ),
                        el_match=Func(
                            F("release"),
                            Value("el([0-9]+)"),
                            function="regexp_match",
                            output=fields.ArrayField(IntegerField()),
                        ),
                    )
                    .order_by(
                        "-el_match",
                        "-software_build__completion_time",
                        "-version_arr",
                        "-release_arr",
                    )
                    .values("uuid")[:1]
                )
            )
            .filter(
                uuid=models.F("latest"),
            )
        )


class ProductStreamTag(Tag):
    tagged_model = models.ForeignKey(ProductStream, on_delete=models.CASCADE, related_name="tags")


class ProductVariant(ProductModel):
    """Product Variant model

    This directly relates to Errata Tool Variants which are mapped then mapped to CDN
    repositories for content that is shipped as RPMs.
    """

    cpe = models.CharField(max_length=1000, default="")

    products = models.ForeignKey(
        "Product", on_delete=models.CASCADE, related_name="productvariants"
    )
    productversions = models.ForeignKey(
        "ProductVersion", on_delete=models.CASCADE, related_name="productvariants"
    )
    # Below creates implicit "productvariants" field on ProductStream
    productstreams = models.ForeignKey(
        ProductStream, on_delete=models.CASCADE, related_name="productvariants"
    )

    @property
    def productvariants(self) -> "ProductVariant":
        return self

    # implicit "channels" field on ProductVariant model
    # is created by productvariants field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on ProductVariant model
    # is created by productvariants field on Component model
    components: models.Manager["Component"]

    @property
    def cpes(self) -> tuple[str]:
        # Split to fix warning that linter and IDE disagree about
        cpes = (self.cpe,)
        return cpes

    def get_ofuri(self) -> str:
        """Return product variant URI

        Ex.: o:redhat:rhel:8.6.0.z:baseos-8.6.0.z.main.eus
        """
        return f"{self.productstreams.ofuri}:{self.name.lower()}"


class ProductVariantTag(Tag):
    tagged_model = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="tags")


class ProductTaxonomyMixin(models.Model):
    """Add product taxonomy fields and a method to save them for Channels and Components."""

    # related_name like "channels" or "components" - first "s" is a format specifier
    products = models.ManyToManyField(Product, related_name="%(class)ss")
    productversions = models.ManyToManyField(ProductVersion, related_name="%(class)ss")
    productstreams = models.ManyToManyField(ProductStream, related_name="%(class)ss")
    productvariants = models.ManyToManyField(ProductVariant, related_name="%(class)ss")

    class Meta:
        abstract = True

    @abstractmethod
    def save_product_taxonomy(self) -> None:
        pass


class Channel(TimeStampedModel, ProductTaxonomyMixin):
    """A model that represents a specific type of delivery channel.

    A Channel is essentially the "location" of where a specific artifact is available from to
    customers.
    """

    class Type(models.TextChoices):
        CDN_REPO = "CDN_REPO"  # Main delivery channel for RPMs
        CONTAINER_REGISTRY = "CONTAINER_REGISTRY"  # Registries, e.g.: registry.redhat.io, quay.io

    uuid = models.UUIDField(primary_key=True, default=uuid_class.uuid4, editable=False)
    name = models.TextField(unique=True)
    relative_url = models.CharField(max_length=200, default="")
    type = models.CharField(choices=Type.choices, max_length=50)
    description = models.TextField(default="")
    meta_attr = models.JSONField(default=dict)
    pnodes = GenericRelation(ProductNode, related_query_name="channel")

    def __str__(self) -> str:
        return str(self.name)

    def save_product_taxonomy(self) -> None:
        """Save taxonomy fields using ancestors of this Channel"""
        ancestors = self.pnodes.get_queryset().get_ancestors()
        products = ProductNode.get_products(ancestors)
        versions = ProductNode.get_product_versions(ancestors)
        streams = ProductNode.get_product_streams(ancestors)
        variants = ProductNode.get_product_variants(ancestors)

        self.products.set(products)
        self.productversions.set(versions)
        self.productstreams.set(streams)
        self.productvariants.set(variants)


class ProductComponentRelation(TimeStampedModel):
    """class to be used for linking taxonomies"""

    class Type(models.TextChoices):
        ERRATA = "ERRATA"
        COMPOSE = "COMPOSE"
        BREW_TAG = "BREW_TAG"
        CDN_REPO = "CDN_REPO"
        YUM_REPO = "YUM_REPO"

    # Below not defined in constants to avoid circular imports
    # ProductComponentRelation types which refer to ProductStreams
    STREAM_TYPES = (Type.BREW_TAG, Type.COMPOSE, Type.YUM_REPO)

    # ProductComponentRelation types which refer to ProductVariants
    VARIANT_TYPES = (Type.CDN_REPO, Type.ERRATA)

    uuid = models.UUIDField(primary_key=True, default=uuid_class.uuid4, editable=False)
    type = models.CharField(choices=Type.choices, max_length=50)
    meta_attr = models.JSONField(default=dict)

    # Ex. if Errata Tool this would be errata_id
    external_system_id = models.CharField(max_length=200, default="")
    # E.g. product variant for ERRATA, or product stream for COMPOSE
    product_ref = models.CharField(max_length=200, default="")
    build_id = models.CharField(max_length=200, default="")

    class Meta:
        constraints = (
            models.UniqueConstraint(
                name="unique_productcomponentrelation",
                fields=("external_system_id", "product_ref", "build_id"),
            ),
        )
        indexes = (
            models.Index(fields=("external_system_id", "product_ref", "build_id")),
            models.Index(fields=("type", "build_id")),
            models.Index(fields=("build_id",)),
            models.Index(fields=("product_ref", "type")),
        )


def get_product_details(
    variant_names: tuple[str, ...], stream_names: list[str]
) -> dict[str, set[uuid_class.UUID]]:
    """
    Given stream / variant names from the PCR table, look up all related ProductModel subclasses

    In other words, builds relate to variants and streams through the PCR table
    Components relate to builds, and products / versions relate to variants / streams
    We want to link all build components to their related products, versions, streams, and variants
    """
    if variant_names:
        variant_streams = ProductVariant.objects.filter(name__in=variant_names).values_list(
            "productstreams__name", flat=True
        )
        stream_names.extend(variant_streams)

    product_details: dict[str, set[uuid_class.UUID]] = {
        "products": set(),
        "productversions": set(),
        "productstreams": set(),
        "productvariants": set(),
        "channels": set(),
    }

    for pnode in ProductNode.objects.filter(
        level=MODEL_NODE_LEVEL_MAPPING["ProductStream"], productstream__name__in=stream_names
    ):
        family = pnode.get_family()

        products = ProductNode.get_products(family)
        product_details["products"].update(products)

        product_versions = ProductNode.get_product_versions(family)
        product_details["productversions"].update(product_versions)

        product_streams = ProductNode.get_product_streams(family)
        product_details["productstreams"].update(product_streams)

        product_variants = ProductNode.get_product_variants(family)
        product_details["productvariants"].update(product_variants)

        channels = ProductNode.get_channels(family)
        product_details["channels"].update(channels)
    # For some build, return a mapping of ProductModel type to all related ProductModel UUIDs
    return product_details


class ComponentQuerySet(models.QuerySet):
    def srpms(self) -> models.QuerySet["Component"]:
        return self.filter(SRPM_CONDITION)

    def root_components(self) -> models.QuerySet["Component"]:
        return self.filter(ROOT_COMPONENTS_CONDITION)


class Component(TimeStampedModel, ProductTaxonomyMixin):
    class Type(models.TextChoices):
        CARGO = "CARGO"  # Rust packages
        CONTAINER_IMAGE = "OCI"  # Container images and other OCI artifacts
        GEM = "GEM"  # Ruby gem packages
        GENERIC = "GENERIC"  # Fallback if no other type can be used
        GITHUB = "GITHUB"
        GOLANG = "GOLANG"
        MAVEN = "MAVEN"
        NPM = "NPM"
        RPMMOD = "RPMMOD"  # RHEL/Fedora modules; not an actual purl type, see CORGI-226
        RPM = "RPM"  # Includes SRPMs, which can be identified with arch=src; see also is_srpm().
        PYPI = "PYPI"

    class Namespace(models.TextChoices):
        UPSTREAM = "UPSTREAM"
        REDHAT = "REDHAT"

    uuid = models.UUIDField(primary_key=True, default=uuid_class.uuid4, editable=False)
    name = models.TextField()
    description = models.TextField()
    meta_attr = models.JSONField(default=dict)

    type = models.CharField(choices=Type.choices, max_length=20)
    namespace = models.CharField(choices=Namespace.choices, max_length=20)
    version = models.CharField(max_length=1024)
    release = models.CharField(max_length=1024, default="")
    arch = models.CharField(max_length=1024, default="")

    purl = models.CharField(max_length=1024, default="")
    nvr = models.CharField(max_length=1024, default="")
    nevra = models.CharField(max_length=1024, default="")

    # related_name defaults to fieldname_set if not specified
    # e.g. channels_set, upstreams_set
    channels = models.ManyToManyField(Channel)
    upstreams = models.ManyToManyField("Component")

    # sources is the inverse of provides. One container can provide many RPMs
    # and one RPM can have many different containers as a source (as well as modules and SRPMs)
    sources = models.ManyToManyField("Component", related_name="provides")
    provides: models.ManyToManyField

    # Specify related_query_name to add e.g. component field
    # that can be used to filter from a cnode to its related component
    # TODO: Or just switch from GenericForeignKey to regular ForeignKey?
    cnodes = GenericRelation(ComponentNode, related_query_name="%(class)s")
    software_build = models.ForeignKey(
        SoftwareBuild, on_delete=models.CASCADE, null=True, related_name="components"
    )

    # Copyright text as it appears in the component source code, from an OpenLCS scan
    copyright_text = models.TextField(default="")
    # License information as it appears in the component source code, from an OpenLCS scan
    # Use the license_concluded or license_concluded_list properties, not this field directly
    license_concluded_raw = models.TextField(default="")
    # The raw "summary license string" without any transformations
    # This information comes from the build system via an RPM spec file, Maven POM file, etc.
    # Use the license_declared or license_declared_list properties, not this field directly
    license_declared_raw = models.TextField(default="")
    # URL with more information about the scan that was performed
    openlcs_scan_url = models.TextField(default="")
    # Version of scanner used to perform this analysis
    openlcs_scan_version = models.TextField(default="")

    # The filename of the source rpm package, or similar, from the meta_attr / build system
    filename = models.TextField(default="")

    related_url = models.CharField(max_length=1024, default="")

    data_score = models.IntegerField(default=0)
    data_report = fields.ArrayField(models.CharField(max_length=200), default=list)

    objects = ComponentQuerySet.as_manager()

    class Meta:
        constraints = (
            models.UniqueConstraint(
                name="unique_components",
                fields=("name", "type", "arch", "version", "release"),
            ),
        )
        indexes = (
            models.Index(fields=("name", "type", "arch", "version", "release")),
            models.Index(fields=("type", "name")),
            models.Index(fields=("nvr",)),
            models.Index(fields=("purl",)),
            models.Index(
                fields=("type", "name", "arch"),
                name="compon_latest_name_type_idx",
                condition=ROOT_COMPONENTS_CONDITION,
            ),
            models.Index(
                fields=("name", "type", "arch"),
                name="compon_latest_type_name_idx",
                condition=ROOT_COMPONENTS_CONDITION,
            ),
            models.Index(
                fields=("uuid", "software_build_id", "type", "name", "arch"),
                name="compon_latest_idx",
                condition=ROOT_COMPONENTS_CONDITION,
            ),
        )

    def __str__(self) -> str:
        """return name"""
        return str(self.name)

    def get_purl(self) -> PackageURL:
        if self.type == Component.Type.RPM:
            purl_data = self._build_rpm_purl()
        elif self.type == Component.Type.RPMMOD:
            purl_data = self._build_module_purl()
        elif self.type == Component.Type.CONTAINER_IMAGE:
            purl_data = self._build_container_purl()
        elif self.type == Component.Type.MAVEN:
            purl_data = self._build_maven_purl()

        else:
            version = self.version
            if self.release:
                version = f"{version}-{self.release}"
            purl_data = dict(
                name=self.name,
                version=version,
            )

        # Red Hat components should be namespaced, everything else is assumed to be upstream.
        if self.namespace == Component.Namespace.REDHAT:
            purl_data["namespace"] = str(self.namespace).lower()

        return PackageURL(type=str(self.type).lower(), **purl_data)

    def _build_maven_purl(self):
        group_id = self.meta_attr.get("group_id")
        purl_data = dict(name=self.name, version=self.version)
        if group_id:
            purl_data["namespace"] = group_id
        return purl_data

    def _build_module_purl(self):
        # Break down RHEL module version into its specific parts:
        # NSVC = Name, Stream, Version, Context
        version, _, context = self.release.partition(".")
        stream = self.version
        return dict(
            name=self.name,
            version=f"{stream}:{version}:{context}",
        )

    def _build_rpm_purl(self):
        qualifiers = {
            "arch": self.arch,
        }
        if self.epoch:
            qualifiers["epoch"] = str(self.epoch)
        # Don't append '-' unless release is populated
        release = f"-{self.release}" if self.release else ""
        return dict(
            name=self.name,
            version=f"{self.version}{release}",
            qualifiers=qualifiers,
        )

    def _build_container_purl(self):
        digest = ""
        if self.meta_attr.get("digests"):
            for digest_fmt in CONTAINER_DIGEST_FORMATS:
                digest = self.meta_attr["digests"].get(digest_fmt)
                if digest:
                    break
        # Use the last path of the repository_url if available
        purl_name = self.name
        name_from_label = self.meta_attr.get("name_from_label")
        if name_from_label:
            purl_name = name_from_label
        # Add the tag which matches version+release (check for empty release)
        release = f"-{self.release}" if self.release else ""
        qualifiers = {
            "tag": f"{self.version}{release}",
        }
        # Only add the arch qualify if it's not an image_index
        if self.arch != "noarch":
            qualifiers["arch"] = self.arch
        # Add full repository_url as well
        repository_url = self.meta_attr.get("repository_url")
        if repository_url:
            qualifiers["repository_url"] = repository_url
        # Note that if no container digest format matched, the digest below is ""
        # and the constructed purl has no .version attribute
        return dict(
            name=purl_name,
            version=digest,
            qualifiers=qualifiers,
        )

    def save_component_taxonomy(self):
        upstreams = self.get_upstreams_pks()
        self.upstreams.set(upstreams)
        provides = self.get_provides_nodes().values_list("component__pk", flat=True)
        self.provides.set(provides)
        sources = self.get_sources_nodes().values_list("component__pk", flat=True)
        self.sources.set(sources)

    def is_srpm(self):
        return self.type == Component.Type.RPM and self.arch == "src"

    def get_nvr(self) -> str:
        release = f"-{self.release}" if self.release else ""
        return f"{self.name}-{self.version}{release}"

    def get_nevra(self) -> str:
        epoch = f":{self.epoch}" if self.epoch else ""
        release = f"-{self.release}" if self.release else ""
        arch = f".{self.arch}" if self.arch else ""

        return f"{self.name}{epoch}-{self.version}{release}{arch}"

    def save(self, *args, **kwargs):
        self.nvr = self.get_nvr()
        self.nevra = self.get_nevra()
        purl = self.get_purl()
        self.purl = purl.to_string()
        super().save(*args, **kwargs)

    def save_product_taxonomy(
        self, product_pks_dict: Union[dict[str, set[uuid_class.UUID]], None] = None
    ) -> None:
        """
        Save links between ProductModel subclasses and this Component

        product_pks_dict should contain a mapping of ProductModel type to IDs
        These are all the products / versions / etc. that relate to some build
        As determined by the PCR table and ProductModel taxonomy.
        We call save_product_taxonomy() on the build, which calls this method
        for each of the build's related components.
        """
        if not product_pks_dict:
            raise ValueError(
                "Call SoftwareBuild.save_product_taxonomy(),"
                "instead of Component.save_product_taxonomy() directly"
            )
        # Since we're only setting the product details for a specific build id we need
        # to ensure we are only updating, not replacing the existing product details.
        # https://github.com/typeddjango/django-stubs/issues/1266 will fix a mypy bug
        self.products.add(*product_pks_dict["products"])  # type: ignore[arg-type]
        self.productversions.add(*product_pks_dict["productversions"])  # type: ignore[arg-type]
        self.productstreams.add(*product_pks_dict["productstreams"])  # type: ignore[arg-type]
        self.productvariants.add(*product_pks_dict["productvariants"])  # type: ignore[arg-type]
        self.channels.add(*product_pks_dict["channels"])  # type: ignore[arg-type]

        return None

    @property
    def get_roots(self) -> list[ComponentNode]:
        """Return component root entities."""
        roots: list[ComponentNode] = []
        # If a component does not have a softwarebuild that means it's not built at Red Hat
        # therefore it doesn't need its upstreams listed. If we start using the get_roots property
        # for functions other than get_upstreams we might need to revisit this check
        if not self.software_build:
            return roots
        for cnode in self.cnodes.get_queryset():
            root = cnode.get_root()
            if root.obj.type == Component.Type.CONTAINER_IMAGE:
                # TODO if we change the CONTAINER->RPM ComponentNode.type to something besides
                # 'PROVIDES' we would check for that type here to prevent 'hardcoding' the
                # container -> rpm relationship here.
                # RPMs are included as children of Containers as well as SRPMs
                # We don't want to include Containers in the RPMs roots.
                # Partly because RPMs in containers can have unprocessed SRPMs
                # And partly because we use roots to find upstream components,
                # and it's not true to say that rpms share upstreams with containers
                rpm_descendant = (
                    cnode.get_ancestors(include_self=True)
                    .filter(component__type=Component.Type.RPM)
                    .exists()
                )
                if not rpm_descendant:
                    roots.append(root)
            else:
                roots.append(root)
        return roots

    @property
    def build_meta(self):
        return self.software_build.meta_attr

    @property
    def download_url(self) -> str:
        """Report a URL for RPMs and container images that can be used in manifests"""
        # RPM ex:
        # /downloads/content/aspell-devel/0.60.8-8.el9/aarch64/fd431d51/package
        # We can't build URLs like above because the fd431d51 signing key is required,
        # but isn't available in the meta_attr / other properties (CORGI-342). Get it with:
        # rpm -q --qf %{SIGPGP:pgpsig} aspell-devel-0.60.8-8.el9.x86_64 | tail -c8
        if self.type == Component.Type.RPM:
            return "https://access.redhat.com/downloads/content/package-browser"

        # Image ex:
        # /software/containers/container-native-virtualization/hco-bundle-registry/5ccae1925a13467289f2475b
        # /software/containers/openshift/ose-local-storage-diskmaker/
        # 5d9347b8dd19c70159f2f6e4?architecture=s390x&tag=v4.4.0-202007171809.p0
        # We can't build URLs like above because the hash is required,
        # but doesn't match any hash in the meta_attr / other properties.
        # We could use repository_url instead, if we decide to store it on the model.
        # Currently it's only in the meta_attr and should not be used (CORGI-341).

        # TODO: self.filename, AKA the filename of the image archive a container is included in,
        #  is always unset. This is not the digest SHA but the config layer SHA.
        elif self.type == Component.Type.CONTAINER_IMAGE:
            return "https://catalog.redhat.com/software/containers/search"

        # All other component types are either not currently supported or have no downloadable
        # artifacts (e.g. RHEL module builds).
        return ""

    @property
    def errata(self) -> list[str]:
        """Return errata that contain component."""
        if not self.software_build:
            return []
        errata_qs = (
            ProductComponentRelation.objects.filter(
                type=ProductComponentRelation.Type.ERRATA,
                build_id=self.software_build.build_id,
            )
            .values_list("external_system_id", flat=True)
            .distinct()
        )
        return list(erratum for erratum in errata_qs if erratum)

    @property
    def license_concluded(self) -> str:
        """Return the OLCS scan results formatted as an SPDX license expression
        This is almost the same as above, but operators (AND, OR) + license IDs are uppercased"""
        return self.license_concluded_raw.upper()

    @property
    def license_declared(self) -> str:
        """Return the "summary license string" formatted as an SPDX license expression
        This is almost the same as above, but operators (AND, OR) + license IDs are uppercased"""
        return self.license_declared_raw.upper()

    @staticmethod
    def license_list(license_expression: str) -> list[str]:
        """Return a list of any possibly-relevant licenses. No information is given about which apply
        To see if all apply or if you may choose between them, parse the license expression above"""
        # "words".split("not present in string") will return ["words"]
        # AKA below will always add one level of nesting to the array
        license_parts = license_expression.split(" AND ")
        # Flatten it back to a list[str] in one line to fix mypy errors
        license_parts = [nested for part in license_parts for nested in part.split(" OR ")]

        return [part.strip("()") for part in license_parts]

    @property
    def license_concluded_list(self) -> list[str]:
        return self.license_list(self.license_concluded)

    @property
    def license_declared_list(self) -> list[str]:
        return self.license_list(self.license_declared)

    @property
    def manifest(self) -> str:
        """Return an SPDX-style manifest in JSON format."""
        return ComponentManifestFile(self).render_content()

    @property
    def epoch(self) -> str:
        return self.meta_attr.get("epoch", "")

    def get_provides_nodes(self, include_dev: bool = True) -> QuerySet[ComponentNode]:
        """return a QuerySet of descendants with PROVIDES ComponentNode type"""
        # Used in manifests / taxonomies. Returns whole objects to access their properties
        type_list = [ComponentNode.ComponentNodeType.PROVIDES]
        if include_dev:
            type_list.append(ComponentNode.ComponentNodeType.PROVIDES_DEV)
        return self.cnodes.get_queryset().get_descendants().filter(type__in=type_list)

    def get_provides_purls(self, include_dev: bool = True) -> QuerySet:
        """return a QuerySet of unique descendant PURLs with PROVIDES ComponentNode type"""
        # Used in serializers. Returns identifiers (purls) to track relationships
        return (
            self.get_provides_nodes(include_dev=include_dev)
            .values_list("purl", flat=True)
            .distinct()
        )

    def get_sources_nodes(self) -> QuerySet[ComponentNode]:
        """return all root node objects"""
        return self.cnodes.get_queryset().get_ancestors(include_self=False).filter(parent=None)

    def get_sources_purls(self) -> QuerySet:
        """return all root node purls"""
        return self.get_sources_nodes().values_list("purl", flat=True).distinct()

    def get_upstreams_nodes(self) -> list[ComponentNode]:
        """return upstreams component ancestors in family trees"""
        # The get_roots logic is too complicated to use only Django filtering
        # So it has to use Python logic and return a set, instead of a QuerySet
        # which forces us to use Python in this + all other get_upstreams_* methods
        upstreams = []
        roots = self.get_roots
        for root in roots:
            # For SRPM/RPMS, and noarch containers, these are the cnodes we want.
            source_descendants = root.get_descendants().filter(
                type=ComponentNode.ComponentNodeType.SOURCE
            )
            # Cachito builds nest components under the relevant source component for that
            # container build, eg. buildID=1911112. In that case we need to walk up the
            # tree from the current node to find its relevant source
            root_obj = root.obj
            if (
                root_obj
                and root_obj.type == Component.Type.CONTAINER_IMAGE
                and root_obj.arch == "noarch"
                and source_descendants.count() > 1
            ):
                upstreams.extend(
                    self.cnodes.get_queryset()
                    .get_ancestors(include_self=True)
                    .filter(
                        type=ComponentNode.ComponentNodeType.SOURCE,
                        component__namespace=Component.Namespace.UPSTREAM,
                    )
                )
            else:
                upstreams.extend(source_descendants)
        return upstreams

    def get_upstreams_pks(self) -> tuple[str, ...]:
        """Return only the primary keys from the set of all upstream nodes"""
        linked_pks = set(node.component.pk for node in self.get_upstreams_nodes())
        return tuple(linked_pks)

    def get_upstreams_purls(self) -> set[str]:
        """Return only the purls from the set of all upstream nodes"""
        return set(node.purl for node in self.get_upstreams_nodes())

    def save_datascore(self):
        score = 0
        report = set()
        if self.namespace == Component.Namespace.UPSTREAM and not self.related_url:
            score += 20
            report.add("upstream has no related_url")
        if not self.description:
            score += 10
            report.add("no description")
        if not self.version:
            score += 10
            report.add("no version")
        if not self.license_declared_raw:
            score += 10
            report.add("no license declared")
        if not self.products.exists():
            score += 10
            report.add("no products")
        if not self.productversions.exists():
            score += 10
            report.add("no product versions")
        if not self.productstreams.exists():
            score += 10
            report.add("no product streams")
        if not self.productvariants.exists():
            score += 20
            report.add("no product variants")
        if not self.sources.exists():
            score += 10
            report.add("no sources")
        if not self.provides.exists():
            score += 10
            report.add("no provides")
        if not self.upstreams.exists():
            score += 10
            report.add("no upstream")
        if not self.software_build:
            score += 10
            report.add("no software build")
        self.data_score = score
        self.data_report = list(report)
        self.save()


class ComponentTag(Tag):
    tagged_model = models.ForeignKey(Component, on_delete=models.CASCADE, related_name="tags")


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
        constraints = (
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
                fields=("name", "type", "product", "initial_product_version", "stream"),
                name="unique_lifecycle_entity",
            ),
        )
