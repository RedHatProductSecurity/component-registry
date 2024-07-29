import logging
import re
from abc import abstractmethod
from typing import Any, Iterable, Iterator, Type, Union
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres import fields
from django.db import models, transaction
from django.db.models import ManyToManyField, Q, QuerySet
from django.db.models.expressions import F, Func, Subquery, Value
from mptt.managers import TreeManager
from mptt.models import MPTTModel, TreeForeignKey
from packageurl import PackageURL
from packageurl.contrib import purl2url

from corgi.core.constants import (
    CONTAINER_DIGEST_FORMATS,
    EL_MATCH_RE,
    MODEL_NODE_LEVEL_MAPPING,
    MODULAR_SRPM_CONDITION,
    NODE_LEVEL_ATTRIBUTE_MAPPING,
    RED_HAT_MAVEN_REPOSITORY,
    ROOT_COMPONENTS_CONDITION,
    SRPM_CONDITION,
)
from corgi.core.fixups import cpe_lookup, external_name_lookup
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


class ProductNode(NodeModel):
    """Product taxonomy node."""

    class ProductNodeType(models.TextChoices):
        DIRECT = "DIRECT"
        # eg. Variants discovered by stream to ET Product Version brew_tag matching
        INFERRED = "INFERRED"

    node_type = models.CharField(
        choices=ProductNodeType.choices, default=ProductNodeType.DIRECT, max_length=20
    )

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
    #   the use of `pnodes.get()`).
    #
    # - The `values_list()` query relies on the GenericRelation of each model's pnodes
    #   attribute's related query name.

    @staticmethod
    def get_node_pks_for_type(
        qs: QuerySet["ProductNode"], mapping_model: Type[object], lookup: str = "__pk"
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

    PROVIDES_NODE_TYPES = (ComponentNodeType.PROVIDES, ComponentNodeType.PROVIDES_DEV)

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
        indexes = (  # type: ignore[assignment]
            models.Index(fields=("type", "parent", "purl")),
            models.Index(fields=("parent",)),
            models.Index(
                fields=("tree_id", "parent_id", "lft"), name="core_cn_tree_parent_lft_idx"
            ),
            models.Index(
                fields=("tree_id", "lft", "purl", "parent_id"),
                name="core_cn_tree_lft_purl_prnt_idx",
            ),
            models.Index(fields=("lft", "rght", "tree_id"), name="core_cn_lft_rght_tree_idx"),
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
        CENTOS = "CENTOS"  # Used by OpenStack RDO
        APP_INTERFACE = "APP_INTERFACE"  # Managed Services
        PNC = "PNC"  # Middleware
        PYXIS = "PYXIS"  # API backing Red Hat's container catalog

    uuid = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    build_id = models.CharField(max_length=200, default="")
    build_type = models.CharField(choices=Type.choices, max_length=20)
    name = models.TextField()  # Arbitrary identifier for a build
    source = models.TextField()  # Source code reference for build
    completion_time = models.DateTimeField(null=True)  # meta_attr["completion_time"]
    # Store meta attributes relevant to different build system types.
    meta_attr = models.JSONField(default=dict)
    # implicit field "components" from Component model's ForeignKey
    components: models.Manager["Component"]

    class Meta:
        indexes = (models.Index(fields=("build_id", "build_type")),)
        constraints = [
            models.UniqueConstraint(
                fields=["build_id", "build_type"], name="unique_build_id_by_type"
            ),
        ]

    def save_product_taxonomy(self):
        """update ('materialize') product taxonomy on all build components

        This method is defined on SoftwareBuild and not Component,
        because the ProductComponentRelation table refers to builds,
        which we use to look up which products a certain component should be linked to
        """
        variant_names = tuple(
            ProductComponentRelation.objects.filter(
                software_build=self,
                type__in=ProductComponentRelation.VARIANT_TYPES,
            )
            .values_list("product_ref", flat=True)
            .distinct()
        )

        stream_names = list(
            ProductComponentRelation.objects.filter(
                software_build=self,
                type__in=ProductComponentRelation.STREAM_TYPES,
            )
            .values_list("product_ref", flat=True)
            .distinct()
        )

        product_details = get_product_details(variant_names, stream_names)

        components = set()
        for component in self.components.iterator():
            components.add(component)
            # This is needed for container image builds which pull in components not
            # built at Red Hat, and therefore not assigned a build_id
            for cnode in component.cnodes.iterator():
                for d in cnode.get_descendants().iterator():
                    components.add(d.obj)

        for component in components:
            component.save_product_taxonomy(product_details)

        return None

    def disassociate_with_service_streams(self, stream_pks: Iterable[str | UUID]) -> None:
        """Remove the stream references from all components associated with this build.
        Assumes that all references belong to ProductStreams for managed services."""
        service_streams = ProductStream.objects.filter(pk__in=stream_pks)

        for component in self.components.iterator():
            component.disassociate_with_service_streams(service_streams)
            for cnode in component.cnodes.iterator():
                for descendant in cnode.get_descendants().iterator():
                    descendant.obj.disassociate_with_service_streams(service_streams)

    def reset_product_taxonomy(self) -> None:
        """Remove the product references from all components associated with this build."""

        for component in self.components.iterator():
            component.reset_product_taxonomy()
            for cnode in component.cnodes.iterator():
                for descendant in cnode.get_descendants().iterator():
                    descendant.obj.reset_product_taxonomy()


class SoftwareBuildTag(Tag):
    tagged_model = models.ForeignKey(
        SoftwareBuild, on_delete=models.CASCADE, related_name="tags", db_column="tagged_model_uuid"
    )


class ProductModel(TimeStampedModel):
    """Abstract model that defines common fields for all product-related models."""

    uuid = models.UUIDField(primary_key=True, default=uuid4, editable=False)
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
    ) -> Union["ProductStream", models.Manager["ProductStream"], ManyToManyField]:
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
    def components(self) -> "ComponentQuerySet":
        pass

    @property
    def builds(self) -> QuerySet:
        """Returns unique productcomponentrelations with at least 1 matching variant or
        stream, ordered by build_id."""
        product_refs = [self.name]

        if isinstance(self, ProductStream):
            # we also want to include child product variants of this product stream
            product_refs.extend(
                self.productvariants.values_list("name", flat=True).using("read_only")
            )
        elif isinstance(self, ProductVersion) or isinstance(self, Product):
            # we don't store products or product versions in the relations table therefore
            # we only want to include child product streams and product variants in the query
            product_refs.extend(
                self.productstreams.values_list("name", flat=True).using("read_only")
            )
            product_refs.extend(
                self.productvariants.values_list("name", flat=True).using("read_only")
            )
            # else it was a product variant, only look up by self.name

        if product_refs:
            return SoftwareBuild.objects.filter(relations__product_ref__in=product_refs)
        # Else no product variants or product streams - should never happen

        return SoftwareBuild.objects.none()

    @property
    def cpes(self) -> tuple[str, ...]:
        """Return CPEs for direct descendant variants if they exist. Otherwise, return a union
        of indirect descendant variants and descendant streams cpes_matching_patterns"""
        if isinstance(self, ProductStream):
            hardcoded_cpes = cpe_lookup(self.name)
            if hardcoded_cpes:
                return tuple(hardcoded_cpes)
        elif isinstance(self, ProductVariant):
            return (self.cpe,)  # type: ignore[attr-defined]
        # else this is a Product, ProductVersion,
        # or ProductStream with no hardcoded cpes

        direct_variant_cpes = (
            self.pnodes.get_queryset()
            .get_descendants()
            .using("read_only")
            .filter(level=MODEL_NODE_LEVEL_MAPPING["ProductVariant"])
            .exclude(node_type=ProductNode.ProductNodeType.INFERRED)
            .values_list("productvariant__cpe", flat=True)
            .order_by("productvariant__cpe")
        )
        if direct_variant_cpes:
            return self.qs_to_tuple_no_empty_strings(direct_variant_cpes)

        return self._get_indirect_cpes()

    def _get_indirect_cpes(self):
        cpes_matching_patterns = (
            self.pnodes.get_queryset()
            .get_descendants(include_self=True)
            .using("read_only")
            .filter(level=MODEL_NODE_LEVEL_MAPPING["ProductStream"])
            .values_list("productstream__cpes_matching_patterns", flat=True)
        )
        distinct_cpes = set(cpe for cpe_patterns in cpes_matching_patterns for cpe in cpe_patterns)
        distinct_cpes.update(self.distinct_inferred_variant_cpes())
        return tuple(sorted(distinct_cpes))

    def distinct_inferred_variant_cpes(self):
        inferred_variant_cpes = (
            self.pnodes.get_queryset()
            .get_descendants(include_self=True)
            .using("read_only")
            .filter(
                level=MODEL_NODE_LEVEL_MAPPING["ProductVariant"],
                node_type=ProductNode.ProductNodeType.INFERRED,
            )
            .values_list("productvariant__cpe", flat=True)
            .distinct()
        )
        distinct_inferred_cpes = self.qs_to_tuple_no_empty_strings(inferred_variant_cpes)
        return distinct_inferred_cpes

    def qs_to_tuple_no_empty_strings(self, direct_variant_cpes: QuerySet) -> tuple[str, ...]:
        """Omit CPEs like ''"""
        return tuple(cpe for cpe in direct_variant_cpes if cpe)

    def get_ofuri(self) -> str:
        model_level = MODEL_NODE_LEVEL_MAPPING[type(self).__name__]
        if model_level == 0:
            return f"o:redhat:{self.name}"
        else:
            parent_level = model_level - 1
        return self._build_ofuri(model_level, parent_level)

    def _build_ofuri(self, model_level, target_level) -> str:
        # No common parents
        if target_level == -1:
            return f"o::::{self.name}"
        ancestor_query = self.pnodes.get_queryset().get_ancestors().filter(level=target_level)
        parent_count = ancestor_query.count()
        if parent_count == 0:
            raise ValueError(f"ProductModel {self.name} is orphaned")
        if parent_count > 1:
            # try going up another level to find a common parent
            return self._build_ofuri(model_level, target_level - 1)
        else:
            parent = ancestor_query.first()
            # ProductVersion and ProductStream take their ofuri from their own name and version
            if model_level in (
                1,
                2,
            ):
                name_without_version = re.sub(r"(-|_|)" + self.version + "$", "", self.name)
                return f"o:redhat:{name_without_version}:{self.version}"
            # Must be a variant with a single parent (stream)
            return f"{parent.obj.ofuri}:{self.name}"

    def save_product_taxonomy(self):
        """Save links between related ProductModel subclasses"""
        products = set()
        productversions = set()
        productstreams = set()
        productvariants = set()
        channels = set()
        for pnode in self.pnodes.get_queryset():
            family = pnode.get_family()
            # Get obj from raw nodes - no way to return related __product obj in values_list()
            products = ProductNode.get_products(family, lookup="").first().obj
            productversions.update(
                node.obj for node in ProductNode.get_product_versions(family, lookup="")
            )
            productstreams.update(
                node.obj for node in ProductNode.get_product_streams(family, lookup="")
            )
            productvariants.update(
                node.obj for node in ProductNode.get_product_variants(family, lookup="")
            )
            channels.update(ProductNode.get_channels(family))

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

        elif isinstance(self, ProductStream):
            self.products = products
            self.productversions = productversions.pop()
            self.productvariants.set(productvariants)

        elif isinstance(self, ProductVariant):
            self.products = products
            self.productversions = productversions.pop()
            self.productstreams.set(productstreams)

        else:
            raise NotImplementedError(
                f"Add behavior for class {type(self)} in ProductModel.save_product_taxonomy()"
            )

        # All ProductModels have a set of descendant channels
        self.channels.set(channels)
        self.ofuri = self.get_ofuri()
        self.save()

    def get_related_names_of_type(self, mapping_model: type, inferred: bool = False) -> list[str]:
        """For a given ProductModel instance find all directly related nodes with the given model
        type and return their names"""
        mapping_key = mapping_model.__name__
        attribute_name = mapping_key.lower()
        results = []
        if inferred:
            direct_pnodes = self.pnodes.get_queryset()
        else:
            direct_pnodes = self.pnodes.exclude(node_type=ProductNode.ProductNodeType.INFERRED)
        if direct_pnodes.exists():
            for tree in direct_pnodes.get_cached_trees():
                query = tree.get_family().filter(level=MODEL_NODE_LEVEL_MAPPING[mapping_key])
                if not inferred:
                    query = query.exclude(node_type=ProductNode.ProductNodeType.INFERRED)
                query = query.values_list(f"{attribute_name}__name", flat=True).distinct()
                results.extend(list(query))
        return results

    def save(self, *args, **kwargs):
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
    components: "ComponentQuerySet"


class ProductTag(Tag):
    tagged_model = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="tags")


class ProductVersion(ProductModel):
    # This is read from product_definitions ps_module cpe field. See
    # product-definitions/-/blob/master/docs/data_model.md#psmodule
    cpe_patterns = fields.ArrayField(models.CharField(max_length=1000), default=list)
    # This is a collection of Errata Tool CPE values matching the above patterns
    # It is populated by the corgi.tasks.prod_defs.update_products task
    cpes_matching_patterns = fields.ArrayField(models.CharField(max_length=1000), default=list)

    products = models.ForeignKey(
        "Product", on_delete=models.CASCADE, related_name="productversions", null=True
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
    components: "ComponentQuerySet"


class ProductVersionTag(Tag):
    tagged_model = models.ForeignKey(ProductVersion, on_delete=models.CASCADE, related_name="tags")


class ProductStream(ProductModel):
    # NOTE brew_tags and yum_repositories values shouldn't be exposed outside of Red Hat
    brew_tags = models.JSONField(default=dict)
    yum_repositories = fields.ArrayField(models.CharField(max_length=200), default=list)

    composes = models.JSONField(default=dict)
    active = models.BooleanField(default=False)
    et_product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)

    exclude_components = fields.ArrayField(models.CharField(max_length=200), default=list)

    cpes_matching_patterns = fields.ArrayField(models.CharField(max_length=1000), default=list)

    products = models.ForeignKey(
        "Product", on_delete=models.CASCADE, related_name="productstreams", null=True
    )
    productversions = models.ForeignKey(
        "ProductVersion", on_delete=models.CASCADE, related_name="productstreams", null=True
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
    components: "ComponentQuerySet"

    @property
    def provides_queryset(self, using: str = "read_only") -> QuerySet["Component"]:
        """Returns unique aggregate "provides" for the latest components in this stream,
        for use in templates"""
        unique_provides = (
            self.components.manifest_components(ofuri=self.get_ofuri())
            .using(using)
            .values_list("provides__pk", flat=True)
            .distinct()
            .order_by("provides__pk")
            .iterator()
        )
        return (
            Component.objects.filter(pk__in=unique_provides)
            # See CORGI-658 for the motivation
            .exclude(purl__contains="redhat.com")
            # Remove .exclude() below when CORGI-428 is resolved
            .exclude(purl__startswith="pkg:golang/", purl__contains="./")
            .exclude(purl__startswith="pkg:golang/", purl__contains="..")
            .external_components()
            .using(using)
        )

    @property
    def upstreams_queryset(self, using: str = "read_only") -> QuerySet["Component"]:
        """Returns unique aggregate "upstreams" for the latest components in this stream,
        for use in templates"""
        unique_upstreams = (
            # RPM upstream data is human-generated and unreliable
            self.components.exclude(type=Component.Type.RPM)
            .manifest_components(ofuri=self.get_ofuri())
            .using(using)
            .values_list("upstreams__pk", flat=True)
            .distinct()
            .order_by("upstreams__pk")
            .iterator()
        )
        return Component.objects.filter(pk__in=unique_upstreams).using(using)

    @property
    def cpes_from_brew_tags(self):
        return self.distinct_inferred_variant_cpes()

    @property
    def external_name(self) -> str:
        constant_name = external_name_lookup(self.name)
        if constant_name:
            return constant_name
        # streams with composes (like rhel-8.8.0) do not have attached Variants, so we need to
        # search all variants here, not just this stream's productvariants
        et_versions_matching_cpes = (
            ProductVariant.objects.filter(cpe__in=self.cpes)
            .exclude(et_product_version="")
            .values_list("et_product_version", flat=True)
            .distinct()
        )
        # There is a single matching et_product_version for this streams cpes
        if len(et_versions_matching_cpes) == 1:
            return et_versions_matching_cpes[0].upper()
        # else we have more than 1 matching Variant with distinct et_product_version values.
        et_versions_with_match = (
            ProductVariant.objects.filter(
                cpe__in=self.cpes, et_product_version__contains=self.version.removesuffix(".z")
            )
            .values_list("et_product_version", flat=True)
            .distinct()
        )
        # There is a single matching et_product_version with this stream's cpes, and this
        # stream's version in the et_product_version
        if len(et_versions_with_match) == 1:
            return et_versions_with_match[0].upper()
        et_products_matching_cpes = (
            ProductVariant.objects.filter(cpe__in=self.cpes)
            .exclude(et_product="")
            .values_list("et_product", flat=True)
            .distinct()
        )
        # There is a single matching et_product with this stream's cpes
        if len(et_products_matching_cpes) == 1:
            # Some products such as 'Ansible Automation Platform' have spaces in the name, which
            # does not work well in a Linux filename
            return f"{et_products_matching_cpes[0]}-{self.version}".upper().replace(" ", "-")
        elif len(et_products_matching_cpes) > 1:
            for et_product in et_products_matching_cpes:
                # There is a matching et_product where the et_product name is found in the
                # stream name. For example rhn_satellite_6.8 has multiple products, "SAT-TOOLS",
                # and "SATELLITE" use the stream's name to favour "SATELLITE" found in that case
                if self.name.upper().find(et_product) > 0:
                    # Upper case any trailing '.z' here to match the values from Errata Tool
                    return f"{et_product}-{self.version}".upper()
        # else there are no matching variants with this stream's cpes, could be a managed service
        # Make the stream name upper case to matching product version names from Errata Tool
        return self.name.upper()


class ProductStreamTag(Tag):
    tagged_model = models.ForeignKey(ProductStream, on_delete=models.CASCADE, related_name="tags")


class ProductVariant(ProductModel):
    """Product Variant model

    This directly relates to Errata Tool Variants which are mapped then mapped to CDN
    repositories for content that is shipped as RPMs.
    """

    cpe = models.CharField(max_length=1000, default="")
    et_product = models.CharField(max_length=1000, default="")
    et_product_version = models.CharField(max_length=1000, default="")

    products = models.ForeignKey(
        "Product", on_delete=models.CASCADE, related_name="productvariants", null=True
    )
    productversions = models.ForeignKey(
        "ProductVersion", on_delete=models.CASCADE, related_name="productvariants", null=True
    )
    # Below creates implicit "productvariants" field on ProductStream
    productstreams = models.ManyToManyField(ProductStream, related_name="productvariants")

    @property
    def productvariants(self) -> "ProductVariant":
        return self

    # implicit "channels" field on ProductVariant model
    # is created by productvariants field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on ProductVariant model
    # is created by productvariants field on Component model
    components: "ComponentQuerySet"

    @property
    def cpes(self) -> tuple[str]:
        # Split to fix warning that linter and IDE disagree about
        cpes = (self.cpe,)
        return cpes


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

    uuid = models.UUIDField(primary_key=True, default=uuid4, editable=False)
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
        APP_INTERFACE = "APP_INTERFACE"
        SBOMER = "SBOMER"

    # Below not defined in constants to avoid circular imports
    # ProductComponentRelation types which refer to ProductStreams
    STREAM_TYPES = (Type.BREW_TAG, Type.COMPOSE, Type.YUM_REPO, Type.APP_INTERFACE)

    # ProductComponentRelation types which refer to ProductVariants
    VARIANT_TYPES = (
        Type.CDN_REPO,
        Type.ERRATA,
        Type.SBOMER,
    )

    uuid = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    type = models.CharField(choices=Type.choices, max_length=50)
    meta_attr = models.JSONField(default=dict)

    # Ex. if Errata Tool this would be errata_id
    external_system_id = models.CharField(max_length=200, default="")
    # E.g. product variant for ERRATA, or product stream for COMPOSE
    product_ref = models.CharField(max_length=200, default="")
    software_build = models.ForeignKey(
        "SoftwareBuild", on_delete=models.SET_NULL, related_name="relations", null=True
    )
    build_id = models.CharField(max_length=200, default="")
    build_type = models.CharField(choices=SoftwareBuild.Type.choices, max_length=20)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                name="unique_productcomponentrelation",
                fields=("external_system_id", "product_ref", "build_id", "build_type"),
            ),
        )
        indexes = (
            models.Index(
                fields=(
                    "external_system_id",
                    "product_ref",
                    "build_id",
                    "build_type",
                )
            ),
            models.Index(
                fields=(
                    "type",
                    "build_id",
                    "build_type",
                )
            ),
            models.Index(
                fields=(
                    "build_id",
                    "build_type",
                )
            ),
            models.Index(fields=("product_ref", "type")),
        )


def get_product_details(
    variant_names: tuple[str, ...], stream_names: list[str]
) -> dict[str, set[str]]:
    """
    Given stream / variant names from the PCR table, look up all related ProductModel subclasses

    In other words, builds relate to variants and streams through the PCR table
    Components relate to builds, and products / versions relate to variants / streams
    We want to link all build components to their related products, versions, streams, and variants
    """
    for variant in ProductVariant.objects.filter(name__in=variant_names):
        variant_streams = variant.get_related_names_of_type(ProductStream)
        stream_names.extend(variant_streams)
    product_details: dict[str, set[str]] = {
        "products": set(),
        "productversions": set(),
        "productstreams": set(),
        "productvariants": set(),
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

    if variant_names:
        product_variants = ProductVariant.objects.filter(name__in=variant_names).values_list(
            "pk", flat=True
        )
        product_details["productvariants"].update(str(pk) for pk in product_variants)
    # else we don't know which variants to link
    # we only know which stream to link this build / these components to
    # Not all variants ship the same components
    # Linking all variants in the stream to this component causes bugs
    # So for now, just don't link anything

    # For some build, return a mapping of ProductModel type to all related ProductModel UUIDs
    # Except channels, because we can't link these correctly (CORGI-298)
    return product_details


class ComponentQuerySet(models.QuerySet):
    """Helper methods to filter QuerySets of Components"""

    @staticmethod
    def _latest_components_func(
        components: "ComponentQuerySet", model_type: str, ofuri: str, include_inactive_streams: bool
    ) -> Iterable[str]:
        # get_latest_component ps_ofuris is an array
        ofuris = [ofuri]
        return (
            components.values("type", "namespace", "name", "arch")
            .order_by("type", "namespace", "name", "arch")
            .distinct("type", "namespace", "name", "arch")
            .annotate(
                latest_version=Func(
                    Value(model_type),
                    Value(ofuris),
                    F("type"),
                    F("namespace"),
                    F("name"),
                    F("arch"),
                    Value(include_inactive_streams),
                    function="get_latest_components",
                    output_field=models.UUIDField(),
                )
            )
            .values_list("latest_version", flat=True)
        )

    def latest_components(
        self,
        ofuri: str,
        model_type: str = "ProductStream",
        include: bool = True,
        include_inactive_streams: bool = False,
        include_all_variants: bool = False,
    ) -> "ComponentQuerySet":
        """Return components from latest builds in a single stream."""

        # Constrain both the grouping of package (type/namespace/name/arch), and the final filtered
        # set by the root components of the ProductModel.
        # This assumes either the Product has root components which are comparable using RPM
        # schematics, or there is only a single package for the model, not multiple versions.
        components = self.root_components()

        # the concept of 'latest component' is only relevant within product boundaries
        # we want to constrain by product type/ofuri which is why we pass in model_type and ofuri
        # into the get_latest_component stored proc annotation
        # If a product stream has multiple variants, we want to return the latest package for each
        # variant, see CORGI-602

        latest_components_uuids: set[str] = set()
        if model_type == "ProductStream":
            stream = ProductStream.objects.get(ofuri=ofuri)
            stream_variant_ofuris = stream.productvariants.values_list("ofuri", flat=True)
            if len(stream_variant_ofuris) > 1 and include_all_variants:
                # calculate the latest uuids for each variant
                for variant_ofuri in stream_variant_ofuris:
                    variant_latest_uuids = self._latest_pks_by_ofuri(
                        components,
                        "ProductVariant",
                        variant_ofuri,
                    )
                    latest_components_uuids.update(variant_latest_uuids)
            else:
                latest_components_uuids = set(
                    self._latest_pks_by_ofuri(
                        components, model_type, ofuri, include_inactive_streams
                    )
                )
        else:
            latest_components_uuids = set(
                self._latest_pks_by_ofuri(components, model_type, ofuri, include_inactive_streams)
            )

        if latest_components_uuids:
            lookup = {"pk__in": latest_components_uuids}
            if include:
                return components.filter(**lookup)
            else:
                return components.exclude(**lookup)

        elif include:
            # no latest components, don't do any further filtering
            return Component.objects.none()
        # No latest components found to exclude so show everything / return unfiltered queryset
        return self

    def _latest_pks_by_ofuri(
        self,
        components: "ComponentQuerySet",
        model_type: str,
        ofuri: str,
        include_inactive_streams: bool = False,
    ) -> Iterable[str]:
        product_prefetch = f"{model_type.lower()}s"
        components.prefetch_related(product_prefetch)
        return self._latest_components_func(components, model_type, ofuri, include_inactive_streams)

    def latest_components_by_streams(
        self,
        include: bool = True,
        include_inactive_streams: bool = False,
    ) -> "ComponentQuerySet":
        """Return root components of latest builds for each product stream."""
        components = self.root_components().prefetch_related("productstreams")

        # if include_inactive_streams is False we need to filter ofuris
        # Clear ordering inherited from parent Queryset, if any
        # So .distinct() works properly and doesn't have duplicates
        productstreams = components.values("productstreams").order_by().distinct()
        productstreams = ProductStream.objects.filter(pk__in=Subquery(productstreams))
        if not include_inactive_streams:
            productstreams = productstreams.filter(active=True)
        product_stream_ofuris = list(productstreams.values_list("ofuri", flat=True).distinct())

        latest_components_uuids = list(
            set(
                components.values("type", "namespace", "name", "arch")
                .order_by("type", "namespace", "name", "arch")
                .distinct("type", "namespace", "name", "arch")
                .annotate(
                    latest_version=Func(
                        Value("ProductStream"),
                        Value(product_stream_ofuris),
                        F("type"),
                        F("namespace"),
                        F("name"),
                        F("arch"),
                        Value(include_inactive_streams),
                        function="get_latest_components",
                        output_field=models.UUIDField(),
                    )
                )
                .values_list("latest_version", flat=True)
            )
        )

        lookup = {"pk__in": latest_components_uuids}
        if include:
            # Show only the latest components
            if not latest_components_uuids:
                # no latest components, don't do any further filtering
                return Component.objects.none()
            return components.filter(**lookup)
        else:
            # Show only the older / non-latest components
            if not latest_components_uuids:
                # No latest components to hide??
                # So show everything / return unfiltered queryset
                return self
            return components.exclude(**lookup)

    def released_components(
        self,
        variants: tuple[str, ...] = (),
        active_compose: bool = False,
        include: bool = True,
    ) -> "ComponentQuerySet":
        """Show only released components by default, or unreleased components if include=False
        If variants are passed in, only return components with errata relations matching one of
        those variants"""

        if variants:
            errata_variant_relations = Q(
                software_build__relations__type=ProductComponentRelation.Type.ERRATA,
                software_build__relations__product_ref__in=variants,
            )
            return self.filter(errata_variant_relations)

        # Ideally this would also check for product_ref in variants, but the only way to get
        # variants for composes is to hardcode them. We did this for CPEs already but not variants
        elif active_compose:
            return Component.objects.none()

        errata_relations = Q(
            software_build__relations__type__in=(
                ProductComponentRelation.Type.ERRATA,
                ProductComponentRelation.Type.COMPOSE,
                ProductComponentRelation.Type.APP_INTERFACE,
            )
        )
        if include:
            # Truthy values return only released components
            queryset = self.filter(errata_relations)
        else:
            # Falsey values return only unreleased components
            queryset = self.exclude(errata_relations)
        # Clear ordering and apply distinct() to avoid duplicates from above filter
        return queryset.order_by().distinct()

    def root_components(self, include: bool = True) -> "ComponentQuerySet":
        """Show only root components by default, or only non-root components if include=False"""
        if include:
            # Truthy values return the filtered queryset (only root components)
            return self.filter(ROOT_COMPONENTS_CONDITION).exclude(MODULAR_SRPM_CONDITION)
        # Falsey values return the excluded queryset (only non-root components)
        return self.filter(Q(software_build__isnull=True) | MODULAR_SRPM_CONDITION)

    # See CORGI-658 for the motivation
    def external_components(self, include: bool = True) -> "ComponentQuerySet":
        """Show only external components by default, or internal components if include=False"""
        redhat_com_query = Q(name__contains="redhat.com/")
        if include:
            # Truthy values return the excluded queryset (only external components)
            return self.exclude(redhat_com_query)
        # Falsey values return the filtered queryset (only internal components)
        return self.filter(redhat_com_query)

    def manifest_components(self, ofuri: str, quick=False) -> "ComponentQuerySet":
        """filter latest components takes a long time, dont bother with that if we're just
        checking there is anything to manifest"""
        non_container_source_components = self.exclude(name__endswith="-container-source")
        roots = non_container_source_components.root_components()
        if not settings.COMMUNITY_MODE_ENABLED:
            # Only filter in enterprise Corgi, where we have ERRATA-type relations
            stream = ProductStream.objects.get(ofuri=ofuri)
            variant_names = stream.get_related_names_of_type(ProductVariant, inferred=True)
            if variant_names:
                roots = roots.released_components(variants=tuple(variant_names))
            elif stream.composes and stream.active:
                roots = roots.released_components(active_compose=True)
            else:
                roots = roots.released_components()

        if not quick:
            # Only filter latest when we're actually generating a manifest
            # not when checking if there are components to manifest, since it's slow
            roots = roots.latest_components(
                model_type="ProductStream",
                ofuri=ofuri,
                include_inactive_streams=True,
                include_all_variants=True,
            )

        # Order by UUID to give stable results in manifests
        # Other querysets should not define an ordering
        # or should clear it with .order_by() if they use .distinct()
        return roots.order_by("pk").distinct("pk")

    def srpms(self, include: bool = True) -> models.QuerySet["Component"]:
        """Show only source RPMs by default, or only non-SRPMs if include=False"""
        if include:
            # Truthy values return the filtered queryset (only SRPM components)
            return self.filter(SRPM_CONDITION)
        # Falsey values return the excluded queryset (only non-SRPM components)
        return self.exclude(SRPM_CONDITION)

    def active_streams(self, include: bool = True) -> "ComponentQuerySet":
        """Show only components in active product streams"""
        if include:
            # Truthy values return the filtered queryset (only components in active streams)
            return (
                self.filter(productstreams__active=True)
                .order_by("name", "type", "arch", "version", "release")
                .distinct()
            )
        # Falsey values return the excluded queryset (only components in no active streams)
        return (
            self.exclude(productstreams__active=True)
            .order_by("name", "type", "arch", "version", "release")
            .distinct()
        )


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

    RPM_PACKAGE_BROWSER = (
        "https://packages.fedoraproject.org/pkgs/rpm/rpm/"
        if settings.COMMUNITY_MODE_ENABLED
        else "https://access.redhat.com/downloads/content/package-browser"
    )
    CONTAINER_CATALOG_SEARCH = (
        "https://registry.fedoraproject.org/"
        if settings.COMMUNITY_MODE_ENABLED
        else "https://catalog.redhat.com/software/containers/search"
    )

    # Types with custom purl-building logic for components in the REDHAT namespace
    # Every other type just gets "redhat/" appended after "pkg:type/"
    CUSTOM_NAMESPACE_TYPES = (Type.CONTAINER_IMAGE, Type.MAVEN)
    REMOTE_SOURCE_COMPONENT_TYPES = (
        Type.CARGO,
        Type.GEM,
        Type.GENERIC,
        Type.GITHUB,
        Type.GOLANG,
        Type.MAVEN,
        Type.NPM,
        Type.PYPI,
    )

    uuid = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.TextField()
    description = models.TextField()
    meta_attr = models.JSONField(default=dict)

    type = models.CharField(choices=Type.choices, max_length=20)
    namespace = models.CharField(choices=Namespace.choices, max_length=20)
    epoch = models.PositiveSmallIntegerField(default=0)
    version = models.CharField(max_length=1024)
    release = models.CharField(max_length=1024, default="")
    arch = models.CharField(max_length=1024, default="")

    purl = models.CharField(max_length=1024, default="", unique=True)
    nvr = models.CharField(max_length=1024, default="")
    nevra = models.CharField(max_length=1024, default="")

    # related_name defaults to modelname_set if not specified
    # e.g. an implicit component_set field is added on the Channel model
    # for all channels linked to this component
    channels = models.ManyToManyField(Channel)

    # upstreams is the inverse of downstreams. One Go module can have multiple containers
    # as downstreams, and one container can have multiple Go modules as upstreams
    upstreams: ManyToManyField = models.ManyToManyField("Component", related_name="downstreams")

    # sources is the inverse of provides. One container can provide many RPMs
    # and one RPM can have many different containers as a source (as well as modules and SRPMs)
    sources: ManyToManyField = models.ManyToManyField("Component", related_name="provides")
    provides: models.Manager["Component"]

    # Specify related_query_name to add e.g. component field
    # that can be used to filter from a cnode to its related component
    # TODO: Or just switch from GenericForeignKey to regular ForeignKey?
    cnodes = GenericRelation(ComponentNode, related_query_name="%(class)s")
    software_build = models.ForeignKey(
        SoftwareBuild,
        on_delete=models.CASCADE,
        null=True,
        related_name="components",
        db_column="software_build_uuid",
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

    # We parse release on all components to support filtering
    # a layered product's (e.g. OpenShift's) components
    # using the base product version (e.g. RHEL 8 vs. RHEL 9)
    el_match = fields.ArrayField(models.CharField(max_length=200), default=list)

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
            models.Index(fields=("nevra",)),
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
                fields=("uuid", "name", "namespace", "software_build_id", "type", "arch"),
                name="compon_latest_idx",
                condition=ROOT_COMPONENTS_CONDITION,
            ),
            # setting gin indexes with gin_trgm_ops does not work when define here
            # django Component model indexes - so we set them manually in
            # corgi/core/migrations/0096_install_gin_indexes.py
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
        # BUT https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#oci
        # "OCI purls do not contain a namespace, although,
        # repository_url may contain a namespace as part of the physical location of the package."
        # BUT https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#maven
        # "The group id is the namespace", so we use repository_url instead
        if (
            self.namespace == Component.Namespace.REDHAT
            and self.type not in Component.CUSTOM_NAMESPACE_TYPES
        ):
            # Don't wipe out purl namespaces if they already exist
            existing_namespace = purl_data.get("namespace", "")
            purl_data["namespace"] = f"{Component.Namespace.REDHAT.lower()}/{existing_namespace}"

        return PackageURL(type=str(self.type).lower(), **purl_data)

    def _build_maven_purl(self) -> dict[str, Union[str, dict[str, str]]]:
        qualifiers = {}

        classifier = self.meta_attr.get("classifier")
        if classifier:
            qualifiers["classifier"] = classifier

        extension = self.meta_attr.get("type")
        if extension:
            qualifiers["type"] = extension

        if self.namespace == Component.Namespace.REDHAT:
            qualifiers["repository_url"] = RED_HAT_MAVEN_REPOSITORY

        purl_data: dict[str, Union[str, dict[str, str]]] = {
            "name": self.name,
            "version": self.version,
            "qualifiers": qualifiers,
        }
        group_id = self.meta_attr.get("group_id")
        if group_id:
            purl_data["namespace"] = group_id

        return purl_data

    def _build_module_purl(self) -> dict[str, str]:
        # Break down RHEL module version into its specific parts:
        # NSVC = Name, Stream, Version, Context
        version, _, context = self.release.partition(".")
        stream = self.version
        return dict(
            name=self.name,
            version=f"{stream}:{version}:{context}",
        )

    def _build_rpm_purl(self) -> dict[str, Any]:
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

    def _build_container_purl(self) -> dict[str, Any]:
        digest = ""
        if self.meta_attr.get("digests"):
            for digest_fmt in CONTAINER_DIGEST_FORMATS:
                digest = self.meta_attr["digests"].get(digest_fmt)
                if digest:
                    break
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
            name=self.name,
            version=digest,
            qualifiers=qualifiers,
        )

    def strip_namespace(self, purl: str) -> str:
        """Remove "redhat/" that we prepend to namespace strings in our purls"""
        if (
            self.namespace == Component.Namespace.REDHAT
            and self.type not in Component.CUSTOM_NAMESPACE_TYPES
            and purl
        ):
            # purls like pkg:golang/redhat/otherstuff cause issues with purl2url
            # Container and Maven components don't have a /redhat/ namespace in their purl
            # Trying to strip this value anyway might cause other issues
            # if the purl contains e.g. ?repository_url=example.com/redhat/path/
            purl = purl.replace("/redhat/", "/", 1)
        return purl

    def strip_release(self, purl_version: str) -> str:
        """Remove self.release that we append to version strings in our purls"""
        # TODO: Need to look at other enhancements / fixes for our purls
        #  Some of them are missing recommended / necessary information for purl2url
        #  Maybe we should add ?release= as a domain-specific qualifier
        #  Instead of appending to upstream versions
        if self.release and purl_version:
            purl_version = purl_version.replace(f"-{self.release}", "", 1)
        return purl_version

    @staticmethod
    def _build_github_download_url(purl: str) -> str:
        """Return a GitHub download URL from the `purl` string."""
        # TODO: Open PR for this upstream
        # github download urls are just zip files like below:
        # https://github.com/RedHatProductSecurity/django-mptt/archive/commit_hash.zip
        purl_data = PackageURL.from_string(purl)

        namespace = purl_data.namespace
        name = purl_data.name
        version = purl_data.version

        if namespace and name and version:
            return f"https://github.com/{namespace}/{name}/archive/{version}.zip"
        elif name and version:
            # Name should embed namespace if not discovered explicitly
            return f"https://github.com/{name}/archive/{version}.zip"
        return ""

    @classmethod
    def _build_golang_download_url(cls, purl: str) -> str:
        """
        Return a download URL from the `purl` string for golang.
        Due to the non deterministic nature of go package locations
        this function works in a best effort basis.
        """
        # Copied from open PR upstream, not merged yet:
        # https://github.com/package-url/packageurl-python/pull/113/files
        purl_dict = PackageURL.from_string(purl).to_dict()

        namespace = purl_dict.get("namespace")
        name = purl_dict.get("name")
        version = purl_dict.get("version")

        download_url = ""
        # Key sometimes has a None value instead of an empty dict
        qualifiers = purl_dict.get("qualifiers")
        if qualifiers:
            download_url = qualifiers.get("download_url")

        if download_url:
            return download_url

        if not (namespace and name and version):
            return ""

        if "github.com" in namespace:
            namespace = namespace.split("/")

            # if the version is a pseudo version and contains several sections
            # separated by - the last section is a git commit id
            # what should be referred in the tree of the repo
            # https://stackoverflow.com/questions/57355929/
            # what-does-incompatible-in-go-mod-mean-will-it-cause-harm
            if "-" in version:
                version = version.split("-")[-1]

            # if the version refers to a module using semantic versioning,
            # but not opted to use modules it has a
            # '+incompatible' differentiator in the version what can be just omitted in our case.
            # Ref: https://stackoverflow.com/questions/57355929/
            # what-does-incompatible-in-go-mod-mean-will-it-cause-harm
            # Ref: https://github.com/golang/go/wiki/
            # Modules#can-a-module-consume-a-package-that-has-not-opted-in-to-modules
            version = version.replace("+incompatible", "")

            purl_dict["version"] = version
            # If the referred module is in a Github repo,
            # like github.com/user/repo/path/to/module
            # then keep only user as the (github) namespace
            # and keep repo as the (github) component name
            # Ignore the (golang) component name and namespace,
            # and ignore github.com plus any subpaths in the repo
            if len(namespace) >= 2:
                purl_dict["namespace"] = namespace[1]
            if len(namespace) >= 3:
                purl_dict["name"] = namespace[2]
            purl_dict["type"] = "github"
            purl = PackageURL(**purl_dict).to_string()
            return cls._build_github_download_url(purl)

        else:
            return f"https://proxy.golang.org/{namespace}/{name}/@v/{version}.zip"

    @classmethod
    def _build_golang_repo_url(cls, purl: str) -> str:
        """
        Return a golang repository URL from the `purl` string.
        Due to the non deterministic nature of go package locations
        this function works in a best effort basis.
        """
        purl_dict = PackageURL.from_string(purl).to_dict()

        namespace = purl_dict.get("namespace")
        name = purl_dict.get("name")
        version = purl_dict.get("version")

        if not (namespace and name and version):
            return ""

        if "github.com" in namespace:
            namespace = namespace.split("/")

            # if the version is a pseudo version and contains several sections
            # separated by - the last section is a git commit id
            # what should be referred in the tree of the repo
            # https://stackoverflow.com/questions/57355929/
            # what-does-incompatible-in-go-mod-mean-will-it-cause-harm
            if "-" in version:
                version = version.split("-")[-1]

            # if the version refers to a module using semantic versioning,
            # but not opted to use modules it has a
            # '+incompatible' differentiator in the version what can be just omitted in our case.
            # Ref: https://stackoverflow.com/questions/57355929/
            # what-does-incompatible-in-go-mod-mean-will-it-cause-harm
            # Ref: https://github.com/golang/go/wiki/
            # Modules#can-a-module-consume-a-package-that-has-not-opted-in-to-modules
            version = version.replace("+incompatible", "")

            purl_dict["version"] = version
            # If the referred module is in a Github repo,
            # like github.com/user/repo/path/to/module
            # then keep only user as the (github) namespace
            # and keep repo as the (github) component name
            # Ignore the (golang) component name and namespace,
            # and ignore github.com plus any subpaths in the repo
            if len(namespace) >= 2:
                purl_dict["namespace"] = namespace[1]
            if len(namespace) >= 3:
                purl_dict["name"] = namespace[2]
            purl_dict["type"] = "github"
            purl = PackageURL(**purl_dict).to_string()
            return purl2url.get_repo_url(purl) or ""

        else:
            return f"https://pkg.go.dev/{namespace}/{name}@{version}"

    @staticmethod
    def _build_maven_download_url(purl: str) -> str:
        """Return a maven download URL from the `purl` string."""
        # TODO: Open PR for this upstream
        # Based on existing url2purl logic for Maven, and official docs:
        # https://maven.apache.org/repositories/layout.html
        purl_data = PackageURL.from_string(purl)
        # Red Hat components use the repository_url from their purl
        # Upstream components use the default repository_url from the purl spec
        repository_url = (
            purl_data.qualifiers.get("repository_url") or "https://repo.maven.apache.org/maven2/"
        )

        namespace = purl_data.namespace
        if namespace:
            namespace = namespace.split(".")
            namespace = "/".join(namespace)

        name = purl_data.name
        version = purl_data.version

        classifier = purl_data.qualifiers.get("classifier")
        classifier = f"-{classifier}" if classifier else ""
        extension = purl_data.qualifiers.get("type")

        if namespace and name and version and extension:
            return (
                f"{repository_url}{namespace}/{name}/{version}/"
                f"{name}-{version}{classifier}.{extension}"
            )

        elif namespace and name and version:
            return f"{repository_url}{namespace}/{name}/{version}"

        else:
            return ""

    @staticmethod
    def _build_maven_repo_url(purl: str) -> str:
        """Return a maven repository URL from the `purl` string."""
        # TODO: Open PR for this upstream
        # Based on existing url2purl logic for Maven, and official docs:
        # https://maven.apache.org/repositories/layout.html
        purl_data = PackageURL.from_string(purl)

        # All components use an upstream site, never the Red Hat repo
        # since the repo doesn't have any human-readable "about page" / info
        central_maven_server = "https://mvnrepository.com/artifact"

        namespace = purl_data.namespace
        name = purl_data.name
        version = purl_data.version

        if namespace and name and version:
            return f"{central_maven_server}/{namespace}/{name}/{version}"
        return ""

    @staticmethod
    def _build_pypi_download_url(purl: str) -> str:
        """Return a PyPI download URL from the `purl` string."""
        # TODO: Open PR for this upstream
        #  Or don't, this predictable URL is a legacy thing we're not really supposed to use
        # https://stackoverflow.com/questions/47781035/does-pypi-have-simple-urls-for-package-downloads#47840593
        purl_data = PackageURL.from_string(purl)
        central_pypi_server = "https://pypi.io/packages/source"

        name = purl_data.name
        version = purl_data.version
        if name and version:
            return f"{central_pypi_server}/{name[0]}/{name}/{name}-{version}.tar.gz"
        return ""

    def save_component_taxonomy(self):
        """Link related components together using foreign keys. Avoids repeated MPTT tree lookups"""
        self.upstreams.set(self.get_upstreams_pks(using="default"))
        self.provides.set(self.get_provides_pks(using="default"))
        self.sources.set(self.get_sources_pks(using="default"))

    @property
    def provides_queryset(self, using: str = "read_only") -> Iterator["Component"]:
        """Return the "provides" queryset using the read-only DB, for use in templates"""
        return self.provides.db_manager(using).iterator()

    def is_srpm(self):
        return self.type == Component.Type.RPM and self.arch == "src"

    def _get_software_build_name(self, name):
        if self.software_build:
            return self.software_build.name
        # else this component didn't have a software_build foreign key
        root_node = self.cnodes.get_queryset().get_ancestors().filter(level=0).first()
        if root_node:
            return Component.objects.get(pk=root_node.object_id).software_build.name

    def get_nvr(self) -> str:
        name = self.name
        if (
            self.namespace == Component.Namespace.REDHAT
            and self.type == Component.Type.CONTAINER_IMAGE
        ):
            name = self._get_software_build_name(name)

        # Many GOLANG components don't have a version or release set
        # so don't return an oddly formatted NVR, like f"{name}-"
        version = f"-{self.version}" if self.version else ""
        release = f"-{self.release}" if self.release else ""
        return f"{name}{version}{release}"

    def get_nevra(self) -> str:
        name = self.name
        if (
            self.namespace == Component.Namespace.REDHAT
            and self.type == Component.Type.CONTAINER_IMAGE
        ):
            name = self._get_software_build_name(name)
        epoch = f":{self.epoch}" if self.epoch else ""
        # Many GOLANG components don't have a version or release set
        # so don't return an oddly formatted NEVRA, like f"{name}-.{arch}"
        version = f"-{self.version}" if self.version else ""
        release = f"-{self.release}" if self.release else ""
        arch = f".{self.arch}" if self.arch else ""

        return f"{name}{epoch}{version}{release}{arch}"

    def _build_repo_url_for_type(self) -> str:
        """Get an upstream repo URL based on a purl"""
        # Remove Red Hat-specific identifiers that break purl2url
        purl = self.strip_namespace(self.purl)
        purl_dict = PackageURL.from_string(purl).to_dict()
        purl_dict["version"] = self.strip_release(purl_dict["version"])
        purl = PackageURL(**purl_dict).to_string()

        if self.type == Component.Type.GEM:
            # Work around a bug in the library
            purl = purl.replace("pkg:gem/", "pkg:rubygems/")
            related_url = purl2url.get_repo_url(purl)

        elif self.type == Component.Type.GENERIC:
            related_url = self.related_url
            # Usually (15k of 17k) generic upstream components point at Github
            generic_pkg_on_github_http = "pkg:generic/github.com/"
            generic_pkg_on_github_git = "pkg:generic/git%40github.com:"
            github_pkg = "pkg:github/"

            if purl.startswith(generic_pkg_on_github_http):
                purl = purl.replace(generic_pkg_on_github_http, github_pkg)
                related_url = purl2url.get_repo_url(purl) or ""
            elif purl.startswith(generic_pkg_on_github_git):
                purl = purl.replace(generic_pkg_on_github_git, github_pkg)
                related_url = purl2url.get_repo_url(purl) or ""
            # else the component isn't hosted on Github, so we don't know

            if "openshift-priv" in related_url:
                # Sometimes we need to redirect to the public OpenShift repos
                related_url = related_url.replace("openshift-priv", "openshift")

            if not related_url:
                # The component isn't available on Github
                # TODO: We can't build a Cachito link here
                #  We need a human-readable upstream page, not a download link
                #  There's nothing in meta_attr besides RPM ID
                #  We could call getRPMHeaders, but does the RPM's URL header
                #  necessarily match the remote-source component's upstream?
                pass

        elif self.type == Component.Type.GOLANG:
            related_url = self._build_golang_repo_url(purl)

        elif self.type == Component.Type.MAVEN:
            related_url = self._build_maven_repo_url(purl)

        elif self.type in Component.REMOTE_SOURCE_COMPONENT_TYPES:
            # All other remote-source component types are natively supported by purl2url
            related_url = purl2url.get_repo_url(purl)

        else:
            # RPM or OCI have values set on ingestion, don't overwrite them
            # We don't care about RPMMOD
            related_url = ""
        # purl2url.get_download_url() returns None if it failed
        # If so, use the existing value for the field (or the empty string default value) instead
        return related_url if related_url else self.related_url

    def save(self, *args, **kwargs):
        self.nvr = self.get_nvr()
        self.nevra = self.get_nevra()
        if self.type == Component.Type.RPM:
            # Filenames for non-RPM components are set with data from build system / meta_attr
            self.filename = f"{self.nevra}.rpm"

        purl = self.get_purl().to_string()
        if self.purl != purl:
            self.purl = purl
            self.cnodes.exclude(purl=purl).update(purl=purl)

        self.related_url = self._build_repo_url_for_type()

        # generate el_match field needed for filter
        el_match = re.match(EL_MATCH_RE, self.release)
        if el_match:
            self.el_match = [x for x in el_match.groups() if x]

        super().save(*args, **kwargs)

    def save_product_taxonomy(
        self, product_pks_dict: Union[dict[str, QuerySet], None] = None
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
        self.products.add(*product_pks_dict["products"])
        self.productversions.add(*product_pks_dict["productversions"])
        self.productstreams.add(*product_pks_dict["productstreams"])
        self.productvariants.add(*product_pks_dict["productvariants"])
        # Don't link channels for all variants to this component (CORGI-728)
        # Not every channel has the same content sets / ships this component
        # We don't know which do and don't, so for now just stop linking
        return None

    def get_roots(self, using: str = "read_only") -> QuerySet[ComponentNode]:
        """Return component root entities."""
        # Only components built at Red Hat need their upstreams listed
        if self.namespace != Component.Namespace.REDHAT:
            return ComponentNode.objects.none()

        # Only root components have a linked SoftwareBuild / have a root node in self.cnodes
        # Non-root components like binary RPMs / Red Hat Maven components also need upstreams listed
        # So we must find their root nodes a little indirectly
        root_node_pks = set()
        for cnode in self.cnodes.db_manager(using).iterator():
            root_node_pks_queryset = (
                cnode.get_ancestors(include_self=True)
                .filter(parent=None)
                .values_list("pk", flat=True)
                .using(using)
            )
            container_image_root_pk = root_node_pks_queryset.filter(
                component__type=Component.Type.CONTAINER_IMAGE
            ).first()
            if container_image_root_pk:
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
                    .using(using)
                    .exists()
                )
                if not rpm_descendant:
                    # If the root for this tree is a container image
                    # AND we're not an RPM, or a child of an RPM,
                    # include the container in the list of roots
                    root_node_pks.add(container_image_root_pk)
                # Else the root for this tree is a container, but we're an RPM or its child
                # So ignore the container image root for this tree

            # Else the root for this tree is not a container, so it could be an SRPM or RPM module
            # or a GitHub repo for a managed service, or a Red Hat Maven component
            # We always include these roots just in case
            # although RPM module and GitHub repo roots should never have any upstreams
            # We can't do this in one query (excluding all the container roots)
            # because GenericForeignKey / GenericRelation doesn't support .exclude()
            else:
                root_node_pks.add(root_node_pks_queryset.get())

        # return non-container roots, AND container roots if self is not an RPM descendant
        return ComponentNode.objects.filter(pk__in=root_node_pks).using(using)

    @property
    def cpes(self) -> QuerySet:
        """Build and return a list of CPEs from all Variants this Component relates to"""
        # For each Variant-type relation, get the linked Variant's CPE directly
        # Remove any duplicates, and return the CPEs in sorted order so manifests are stable
        return (
            self.productvariants.exclude(cpe="")
            .values_list("cpe", flat=True)
            .distinct()
            .order_by("cpe")
        )

        # For Stream-type relations, we no longer link any Variants
        # since this caused data quality issues, where Components were linked to Variants
        # that didn't actually ship those Components
        # We could look at all the linked streams to get CPEs from all their child Variants
        # But this would cause the same problem - we'd report too many Variants / incorrect CPEs

        # TODO: We return an empty QuerySet if no Variants are linked, or none have CPEs
        #  But do we want to raise an error / make manifesting fail,
        #  whenever we can't find CPEs for some root component?

    def _build_download_url_for_type(self) -> str:
        """Get a source code or binary download URL based on a purl"""
        # Remove Red Hat-specific identifiers that break purl2url
        purl = self.strip_namespace(self.purl)
        purl_dict = PackageURL.from_string(purl).to_dict()
        purl_dict["version"] = self.strip_release(purl_dict["version"])
        purl = PackageURL(**purl_dict).to_string()

        if self.type == Component.Type.GEM:
            # Work around a bug in the library:
            # https://github.com/package-url/packageurl-python/pull/114
            purl = purl.replace("pkg:gem/", "pkg:rubygems/")
            download_url = purl2url.get_download_url(purl)

        elif self.type == Component.Type.GENERIC:
            # purl2url can't support this type very well, for obvious reasons
            # just return a download URL if the purl has an explicit one
            purl_obj = PackageURL.from_string(purl)
            download_url = purl_obj.qualifiers.get("download_url", "")

            if not download_url:
                # Usually (15k of 17k) generic upstream components point at Github
                generic_pkg_on_github_http = "pkg:generic/github.com/"
                generic_pkg_on_github_git = "pkg:generic/git%40github.com:"
                github_pkg = "pkg:github/"

                if purl.startswith(generic_pkg_on_github_http):
                    purl = purl.replace(generic_pkg_on_github_http, github_pkg)
                    download_url = self._build_github_download_url(purl)
                elif purl.startswith(generic_pkg_on_github_git):
                    purl = purl.replace(generic_pkg_on_github_git, github_pkg)
                    download_url = self._build_github_download_url(purl)
                # else the component isn't hosted on Github, so we don't know

            # if "openshift-priv" in download_url:
            #    Sometimes we need to redirect to the public OpenShift repos
            #    But different repos will have different commit IDs / "versions"
            #    Users should just request access to the OpenShift repo in question
            #    download_url = download_url.replace("openshift-priv", "openshift")

            if not download_url:
                # The component isn't available on Github
                # TODO: Build some download link for an internal service,
                #  but these generic components aren't available in
                #  f"{settings.BREW_DOWNLOAD_ROOT_URL}/packages/"
                #  and I can't figure out how to find them in Cachito
                pass

        elif self.type == Component.Type.GITHUB:
            download_url = self._build_github_download_url(purl)

        elif self.type == Component.Type.GOLANG:
            download_url = self._build_golang_download_url(purl)

        elif self.type == Component.Type.MAVEN:
            download_url = self._build_maven_download_url(purl)

        elif self.type == Component.Type.PYPI:
            download_url = self._build_pypi_download_url(purl)

        elif self.type in Component.REMOTE_SOURCE_COMPONENT_TYPES:
            # All other remote-source component types are natively supported by purl2url
            download_url = purl2url.get_download_url(purl)

        # All other component types are either not currently supported or have no downloadable
        # artifacts (e.g. RHEL module builds).
        else:
            download_url = ""

        # purl2url.get_download_url() returns None if it failed, but we need an empty string
        return download_url if download_url else ""

    @property
    def download_url(self) -> str:
        """Report a URL for RPMs and container images that can be used in manifests"""
        # TODO: Should we eventually make this a stored field on the model
        #  instead of computing the property each time?
        # RPM ex:
        # /downloads/content/aspell-devel/0.60.8-8.el9/aarch64/fd431d51/package
        # We can't build URLs like above because the fd431d51 signing key is required,
        # but isn't available in the meta_attr / other properties (CORGI-342). Get it with:
        # rpm -q --qf %{SIGPGP:pgpsig} aspell-devel-0.60.8-8.el9.x86_64 | tail -c8
        if self.type == Component.Type.RPM:
            return self.RPM_PACKAGE_BROWSER

        # Image ex:
        # /software/containers/container-native-virtualization/hco-bundle-registry/5ccae1925a13467289f2475b
        # /software/containers/openshift/ose-local-storage-diskmaker/
        # 5d9347b8dd19c70159f2f6e4?architecture=s390x&tag=v4.4.0-202007171809.p0
        # We can't build Container Catalog URLs like above because the hash is required,
        # but doesn't match any hash in the meta_attr / other properties.
        # repository_url / related_URL is the customer-facing location,
        # so for now we just build a pull URL from that instead.

        elif self.type == Component.Type.CONTAINER_IMAGE:
            if not self.related_url:
                return self.CONTAINER_CATALOG_SEARCH

            # registry.redhat.io/repo/name:version-release
            release = f"-{self.release}" if self.release else ""
            return f"{self.related_url}:{self.version}{release}"

        else:
            # Usually a remote-source component
            # Anything else (RPMMOD) just returns an empty string
            return self._build_download_url_for_type()

    @property
    def errata(self) -> list[str]:
        """Return errata that contain component."""
        if not self.software_build:
            return []
        errata_qs = (
            ProductComponentRelation.objects.filter(
                type=ProductComponentRelation.Type.ERRATA, software_build=self.software_build
            )
            .values_list("external_system_id", flat=True)
            .distinct()
            .using("read_only")
        )
        return list(erratum for erratum in errata_qs if erratum)

    @staticmethod
    def license_clean(license_str: str) -> str:
        """Take an SPDX license expression, and remove spaces from all identifiers
        so that PUBLIC DOMAIN becomes PUBLIC-DOMAIN, ASL 2.0 becomes ASL-2.0, etc."""
        # We use underscores to avoid turning "GPL-V2-OR-LATER" into "GPL-V2 OR LATER"
        license_str = license_str.replace(" ", "_")
        # Above fixed identifiers, but also the keywords "_AND_", "_OR_" and "_WITH_"
        license_str = license_str.replace("_AND_", " AND ")
        license_str = license_str.replace("_OR_", " OR ")
        license_str = license_str.replace("_WITH_", " WITH ")
        license_str = license_str.replace("_", "-")
        return license_str

    @property
    def license_concluded(self) -> str:
        """Return the OLCS scan results formatted as an SPDX license expression
        This is almost the same as above, but operators (AND, OR) + license IDs are uppercased
        and multi-word identifiers like ASL 2.0 are joined with - dashes, like ASL-2.0"""
        license_str = self.license_concluded_raw.upper()
        return self.license_clean(license_str)

    @property
    def license_declared(self) -> str:
        """Return the "summary license string" formatted as an SPDX license expression
        This is almost the same as above, but operators (AND, OR) + license IDs are uppercased
        and multi-word identifiers like ASL 2.0 are joined with - dashes, like ASL-2.0"""
        license_str = self.license_declared_raw.upper()
        return self.license_clean(license_str)

    @staticmethod
    def license_list(license_expression: str) -> list[str]:
        """Return a list of any possibly-relevant licenses. No information is given about which
        apply. To see if all apply or if you may choose between them, parse the license expression
        above"""
        # "words".split("not present in string") will return ["words"]
        # AKA below will always add one level of nesting to the array
        license_parts = license_expression.split(" AND ")
        # Flatten it back to a list[str] in one line to fix mypy errors
        license_parts = [nested for part in license_parts for nested in part.split(" OR ")]

        return [part.strip("()") for part in license_parts if part]

    @property
    def license_concluded_list(self) -> list[str]:
        return self.license_list(self.license_concluded)

    @property
    def license_declared_list(self) -> list[str]:
        return self.license_list(self.license_declared)

    def get_provides_pks(self, include_dev: bool = True, using: str = "read_only") -> set[str]:
        """Return Component PKs which are PROVIDES descendants of this Component, for taxonomies"""
        provides_set = set()

        type_list: tuple[ComponentNode.ComponentNodeType, ...] = (
            ComponentNode.ComponentNodeType.PROVIDES,
        )
        if include_dev:
            type_list = ComponentNode.PROVIDES_NODE_TYPES
        for cnode in self.cnodes.iterator():
            provides_set.update(
                cnode.get_descendants()
                .filter(type__in=type_list)
                .using(using)
                .values_list("object_id", flat=True)
                .iterator()
            )
        return provides_set

    def get_provides_nodes_queryset(
        self, include_dev: bool = True, using: str = "read_only"
    ) -> QuerySet[ComponentNode]:
        """Return (node purl, node type, component PK) which are PROVIDES descendants of this
        Component for manifests"""
        type_list: tuple[ComponentNode.ComponentNodeType, ...] = (
            ComponentNode.ComponentNodeType.PROVIDES,
        )
        if include_dev:
            type_list = ComponentNode.PROVIDES_NODE_TYPES

        provides_set = set()
        for cnode in self.cnodes.iterator():
            provides_set.update(
                cnode.get_descendants()
                .filter(type__in=type_list)
                .using(using)
                .values_list("pk", flat=True)
                .iterator()
            )
        return (
            ComponentNode.objects.filter(pk__in=provides_set)
            # See CORGI-658 for the motivation
            .exclude(purl__contains="redhat.com")
            # Remove .exclude() below when CORGI-428 is resolved
            .exclude(purl__startswith="pkg:golang/", purl__contains="./")
            .exclude(purl__startswith="pkg:golang/", purl__contains="..")
            .using(using)
            .values_list("purl", "type", "object_id")
            # Ensure generated manifests only change when content does
            .order_by("object_id")
            .distinct()
            .iterator()
        )

    def get_sources_pks(self, include_dev: bool = True, using: str = "read_only") -> set[str]:
        """Return Component PKs which are ancestors of this Component's PROVIDES nodes,
        for taxonomies"""
        sources_set = set()
        type_list: tuple[ComponentNode.ComponentNodeType, ...] = (
            ComponentNode.ComponentNodeType.PROVIDES,
        )
        if include_dev:
            type_list = ComponentNode.PROVIDES_NODE_TYPES
        # Return ancestors of only PROVIDES nodes for this component
        # Sources should be inverse of provides, so don't consider other nodes
        # Inverting "PROVIDES descendants of all nodes" gives "all ancestors of PROVIDES nodes"
        for cnode in self.cnodes.filter(type__in=type_list).iterator():
            sources_set.update(
                cnode.get_ancestors(include_self=False)
                .using(using)
                .values_list("object_id", flat=True)
                .iterator()
            )
        return sources_set

    def get_upstreams_nodes(self, using: str = "read_only") -> QuerySet[ComponentNode]:
        """return upstreams component ancestors in family trees"""
        upstream_pks = set()
        for root in self.get_roots(using=using).iterator():
            root_obj = root.obj
            if (
                root_obj
                and root_obj.type == Component.Type.CONTAINER_IMAGE
                and self.type != Component.Type.CONTAINER_IMAGE
            ):
                # If the root obj is a container, but this child component is not,
                # skip reporting any upstreams from this container tree, because:

                # Binary RPMs should only report upstreams from a source RPM tree
                # If one exists, we'll process it in a later loop iteration
                # So the source RPM and binary RPMs both report the same upstreams
                # and the binary RPMs won't report the container's upstreams

                # Red Hat GitHub / Maven components might be root components (in another tree)
                # If one exists, we'll process it in a later loop iteration
                # So REDHAT components report only their upstreams, if any
                # and won't report the container's upstreams

                # Other remote-source components will never reach this point at all
                # since get_roots only gives results for REDHAT components
                # UPSTREAM components are already upstream, so don't list any new upstreams
                continue

            # Else we're processing a non-container root
            # So include all SOURCE-type descendants of the root
            # Source RPM roots should only have 1, binary RPMs will share it

            # RPM module and managed service GitHub repo roots should have 0
            # Not sure about Red Hat Maven component roots, probably depends on CORGI-796

            # OR the root obj is a container, and so is the component we're processing
            # "Source descendants of the root" should just be the Brew "sources"
            # as well as the upstream Go modules, if any
            # So index / arch-independent and arch-specific containers will report
            # the same upstreams for all variations of the same container
            source_descendants = (
                root.get_descendants()
                .filter(type=ComponentNode.ComponentNodeType.SOURCE)
                .values_list("pk", flat=True)
                .using(using)
            )
            upstream_pks.update(source_descendants)
        return ComponentNode.objects.filter(pk__in=upstream_pks).using(using)

    def get_upstreams_pks(self, using: str = "read_only") -> QuerySet:
        """Return only the linked Component primary keys from the set of all upstream nodes"""
        return self.get_upstreams_nodes(using=using).values_list("object_id", flat=True).distinct()

    def get_upstreams_purls(self, using: str = "read_only") -> QuerySet:
        """Return only the purls from the set of all upstream nodes"""
        return self.get_upstreams_nodes(using=using).values_list("purl", flat=True).distinct()

    def disassociate_with_service_streams(self, stream_refs: Iterable[ProductStream]) -> None:
        """Disassociate this component with the passed in managed service ProductStreams,
        any child ProductModels, and any unused ancestor ProductModels in that service's hierarchy.
        This is the reverse of what happens in save_product_taxonomy.
        """
        # DANGER: This code is only needed, and only safe, for managed service streams
        # These streams have no variants. Do not call for other streams, or for variants directly
        # We call remove in a for-loop, instead of once using nested tuple unpacking
        # because the code gets really ugly otherwise
        for stream_ref in stream_refs:
            if not isinstance(stream_ref, ProductStream):
                # Many-to-many relationships, like variants to products or variants to versions,
                # are not supported here because we cannot easily determine the ancestors to unlink
                # We can only parse many-to-one, like streams to products or streams to versions
                # We only need this code to unlink components from managed service streams
                raise ValueError("Unsafe attempt to unlink a non-ProductStream model")
            # This technically could be incorrect, if the managed service stream
            # we are unlinking shares a variant with any other streams
            # or if the removed variant itself had a relation to the component
            # We would remove these variants, even though they should be kept
            # Currently none of the service streams have any variants, so this is safe
            self.productvariants.remove(
                *stream_ref.productvariants.values_list("pk", flat=True)  # type: ignore[arg-type]
            )
            self.productstreams.remove(stream_ref)
            self._check_and_remove_orphaned_product_refs(stream_ref, "ProductVersion")
            self._check_and_remove_orphaned_product_refs(stream_ref, "Product")

    def reset_product_taxonomy(self):
        """Disassociate this component with the passed in ProductModel and any child ProductModels
        in that product's hierarchy. This is the reverse of what happens in save_product_taxonomy.
        """
        # Traverse up the tree ancestors to the roots
        root_pks = set()
        for cnode in self.cnodes.iterator():
            for root_pk in (
                cnode.get_ancestors(include_self=True)
                .filter(parent=None)
                # cant filter on component fields for generic foreign key
                .values_list("component", flat=True)
                .using("read_only")
            ):
                root_pks.add(root_pk)
        # Get the software builds for roots which have relations
        software_builds_with_relations = None
        if root_pks:
            software_builds_with_relations = (
                Component.objects.filter(pk__in=root_pks)
                .exclude(software_build__relations=None)
                .values_list("software_build")
            )

        # Build an updated product hierarchy for this set of software builds from the
        # relations table
        product_details = {}
        if software_builds_with_relations:
            variant_names = tuple(
                ProductComponentRelation.objects.filter(
                    software_build__in=software_builds_with_relations,
                    type__in=ProductComponentRelation.VARIANT_TYPES,
                )
                .values_list("product_ref", flat=True)
                .distinct()
            )

            stream_names = list(
                ProductComponentRelation.objects.filter(
                    software_build__in=software_builds_with_relations,
                    type__in=ProductComponentRelation.STREAM_TYPES,
                )
                .values_list("product_ref", flat=True)
                .distinct()
            )
            product_details = get_product_details(variant_names, stream_names)
        if product_details:  # Update the product relations for this component atomically
            with transaction.atomic():
                self._clear_product_refs()
                self.save_product_taxonomy(product_details)
        else:  # There are no longer any product relations for this component
            self._clear_product_refs()

    def _clear_product_refs(self):
        self.products.clear()
        self.productversions.clear()
        self.productstreams.clear()
        self.productvariants.clear()

    def _check_and_remove_orphaned_product_refs(
        self, stream_ref: ProductStream, ancestor_model_name: str
    ) -> None:
        """Remove product_models from this component where there are no remaining children of
        product_ref or the remaining children of product_ref don't share this product_ref as an
        ancestor"""
        if not isinstance(stream_ref, ProductStream):
            # Many-to-many relationships, like variants to products or variants to versions,
            # are not supported here because we cannot easily determine the ancestors to unlink
            # We can only parse many-to-one, like streams to products or streams to versions
            # We only need this code to unlink components from managed service streams
            raise ValueError("Unsafe attempt to unlink s non-ProductStream model")

        # For an ancestor_model_name like "Product", this is 0
        ancestor_node_level = MODEL_NODE_LEVEL_MAPPING[ancestor_model_name]
        # and this will be "products"
        ancestor_attribute = NODE_LEVEL_ATTRIBUTE_MAPPING[ancestor_node_level]
        # this will be "productversions", after looking up 0 + 1 AKA 1
        child_of_ancestor_attribute = NODE_LEVEL_ATTRIBUTE_MAPPING[ancestor_node_level + 1]
        # e.g.this_component.productversions.get_queryset()
        # we get the list of remaining versions, we've already removed this_stream's parent version
        children_of_ancestor = getattr(self, child_of_ancestor_attribute).get_queryset()
        # children_of_ancestor AKA child_versions_qs.values_list("products", flat=True)
        # gives us just the PKs of the parent products
        ancestors_of_remaining_siblings = children_of_ancestor.values_list(
            ancestor_attribute, flat=True
        ).distinct()
        # AKA this_stream.products.pk - there's only one
        ancestor_of_stream_ref = getattr(stream_ref, ancestor_attribute).pk
        # If the (grand)parent product of this_stream is not in the list of (grand)parent products
        # for parent versions
        if ancestor_of_stream_ref not in ancestors_of_remaining_siblings:
            # this_component.products.remove(PK for grandparent_product_of_this_stream)
            getattr(self, ancestor_attribute).remove(ancestor_of_stream_ref)


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


class RedHatProfile(models.Model):
    """Additional information provided by Red Hat SSO, used for access controls"""

    rhat_uuid = models.UUIDField(primary_key=True, default=uuid4)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rhat_roles = models.TextField(default="")
    # Storing CN instead of trying to split it into Django's given/first/family/last
    # bc https://www.kalzumeus.com/2010/06/17/falsehoods-programmers-believe-about-names/
    full_name = models.CharField(max_length=256, default="")

    def __str__(self) -> str:
        return f"{self.full_name} <{self.user.email}>"
