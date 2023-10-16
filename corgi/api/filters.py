import logging

from django.core.validators import EMPTY_VALUES
from django.db.models import QuerySet
from django_filters.rest_framework import BooleanFilter, CharFilter, Filter, FilterSet

from corgi.api.serializers import get_model_ofuri_type
from corgi.core.models import (
    Channel,
    Component,
    ComponentQuerySet,
    Product,
    ProductStream,
    ProductVariant,
    ProductVersion,
    SoftwareBuild,
)

logger = logging.getLogger(__name__)


class EmptyStringFilter(BooleanFilter):
    """Filter or exclude an arbitrary field against an empty string value"""

    def filter(self, qs: QuerySet[Component], value: bool) -> QuerySet[Component]:
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return qs

        # Use .exclude() if the filter declares exclude=True
        # or if the user choose BooleanFilter's "NO" option in the UI
        # e.g. "show empty licenses" -> NO means show only components with licenses
        # Otherwise use .filter()
        exclude = self.exclude ^ (value is False)
        method = qs.exclude if exclude else qs.filter

        return method(**{self.field_name: ""})


class TagFilter(Filter):
    def filter(self, queryset: QuerySet, value: str) -> QuerySet:
        # TODO: currently defaults to AND condition, we should make this configurable for both
        # OR and AND conditions.
        if not value:
            return queryset
        search_tags = value.split(",")
        for tag in search_tags:
            exclude = False
            if tag.startswith("!"):
                tag = tag[1:]
                exclude = True
            if ":" in tag:
                tag_name, _, tag_value = tag.partition(":")
                queryset_kwargs = {"tags__name": tag_name, "tags__value": tag_value}
            else:
                queryset_kwargs = {"tags__name": tag}
            if exclude:
                queryset = queryset.exclude(**queryset_kwargs)
            else:
                queryset = queryset.filter(**queryset_kwargs)
        return queryset


class ComponentFilter(FilterSet):
    """Class that filters queries to Component list views."""

    class Meta:
        model = Component
        # Fields that are matched to a filter using their Django model field type and default
        # __exact lookups.
        fields = ("type", "namespace", "name", "version", "release", "arch", "nvr", "nevra")

    # Custom filters
    re_name = CharFilter(lookup_expr="iregex", field_name="name")
    re_purl = CharFilter(lookup_expr="iregex", field_name="purl")
    description = CharFilter(lookup_expr="icontains")
    related_url = CharFilter(lookup_expr="icontains")
    tags = TagFilter()

    # User gave a filter like ?ofuri= in URL, assume they wanted a stream
    ofuri = CharFilter(
        method="filter_ofuri_components", label="Show only latest root components of product"
    )
    products = CharFilter(method="filter_ofuri_or_name")
    product_versions = CharFilter(field_name="productversions", method="filter_ofuri_or_name")
    product_streams = CharFilter(field_name="productstreams", method="filter_ofuri_or_name")
    product_variants = CharFilter(field_name="productvariants", method="filter_ofuri_or_name")
    channels = CharFilter(lookup_expr="name")

    # Normally we are interested in retrieving provides,sources or upstreams of a specific component
    sources = CharFilter(lookup_expr="purl")
    sources_name = CharFilter(field_name="sources", lookup_expr="name")
    provides = CharFilter(lookup_expr="purl")
    provides_name = CharFilter(field_name="provides", lookup_expr="name")
    upstreams = CharFilter(lookup_expr="purl")
    upstreams_name = CharFilter(field_name="upstreams", lookup_expr="name")
    downstreams = CharFilter(lookup_expr="purl")

    # otherwise use regex to match provides,sources or upstreams purls
    re_sources = CharFilter(field_name="sources", lookup_expr="purl__iregex")
    re_sources_name = CharFilter(field_name="sources", lookup_expr="name__iregex")
    re_provides = CharFilter(field_name="provides", lookup_expr="purl__iregex")
    re_provides_name = CharFilter(field_name="provides", lookup_expr="name__iregex")
    re_upstreams = CharFilter(field_name="upstreams", lookup_expr="purl__iregex")
    re_upstreams_name = CharFilter(field_name="upstreams", lookup_expr="name__iregex")
    re_downstreams = CharFilter(field_name="downstreams", lookup_expr="purl__iregex")

    el_match = CharFilter(label="RHEL version for layered products", lookup_expr="icontains")
    released_components = BooleanFilter(
        method="filter_released_components", label="Show only released components"
    )
    root_components = BooleanFilter(
        method="filter_root_components",
        label="Show only root components (source RPMs, index container images)",
    )
    latest_components_by_streams = BooleanFilter(
        method="filter_latest_components_by_streams",
        label="Show only latest components across product streams",
    )

    missing_copyright = EmptyStringFilter(
        field_name="copyright_text",
        label="Show only unscanned components (where copyright text is empty)",
    )

    missing_license = EmptyStringFilter(
        field_name="license_concluded_raw",
        label="Show only unscanned components (where license concluded is empty)",
    )

    missing_scan_url = EmptyStringFilter(
        field_name="openlcs_scan_url",
        label="Show only unscanned components (where OpenLCS scan URL is empty)",
    )

    gomod_components = BooleanFilter(
        label="Show only gomod components, hide go-packages",
        method="filter_gomod_components",
    )

    active_streams = BooleanFilter(
        label="Show components from active streams",
        method="filter_active_streams",
    )

    @staticmethod
    def filter_gomod_components(
        qs: QuerySet[Component], _name: str, value: bool
    ) -> QuerySet[Component]:
        """Show only GOLANG components that are Go modules, and hide Go packages"""
        # TODO: Probably should be added to ComponentQuerySet instead of here
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return qs

        # Below check only works / keys in meta_attr only exist for GOLANG type
        qs = qs.filter(type=Component.Type.GOLANG)

        # Use .exclude() if the user choose BooleanFilter's "NO" option in the UI
        # e.g. "show gomod components" -> NO means show only go-packages
        # Otherwise use .filter()
        method = qs.exclude if value is False else qs.filter

        return method(**{"meta_attr__go_component_type": "gomod"})

    @staticmethod
    def filter_ofuri_or_name(
        queryset: QuerySet[Component], name: str, value: str
    ) -> QuerySet[Component]:
        """Filter some field by a ProductModel subclass's ofuri
        Or else by a name, depending on the user's input"""
        if value.startswith("o:redhat:"):
            # User provided an ofuri
            # Filter using "products__ofuri", "productversions__ofuri", etc.
            lookup_expr = f"{name}__ofuri"
        else:
            # We don't have a valid ofuri, so assume the user gave a name
            # Filter using "product__name", "productversions__name", etc.
            lookup_expr = f"{name}__name"
        return queryset.filter(**{lookup_expr: value})

    @staticmethod
    def filter_released_components(
        queryset: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet["Component"]:
        """Show only released components in some queryset if user chose YES / NO"""
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return queryset
        # Else user gave a non-empty value
        # Truthy values return the excluded queryset (only released components)
        # Falsey values return the filtered queryset (only unreleased components)
        return queryset.released_components(include=value)

    @staticmethod
    def filter_root_components(
        queryset: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet["Component"]:
        """Show only root / non-root components in some queryset if user chose YES / NO"""
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return queryset
        # Else user gave a non-empty value
        # Truthy values return the filtered queryset (only root components)
        # Falsey values return the excluded queryset (only non-root components)
        return queryset.root_components(include=value)

    @staticmethod
    def filter_latest_components_by_streams(
        queryset: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet["Component"]:
        """Show only latest ROOT components in some queryset if user chose YES / NO"""
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return queryset
        # Else user gave a non-empty value
        # Truthy values return the filtered queryset (only latest components)
        # Falsey values return the excluded queryset (only older components)
        return queryset.latest_components_by_streams(include=value)

    @staticmethod
    def filter_active_streams(
        queryset: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet["Component"]:
        """Show only components from active streams when True"""
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return queryset
        return queryset.active_streams(include=value)

    @staticmethod
    def filter_ofuri_components(
        queryset: QuerySet[Component], name: str, value: str
    ) -> QuerySet["Component"]:
        """'latest' and 'root components' filter automagically turn on
        when the ofuri parameter is provided"""
        if value in EMPTY_VALUES:
            return queryset
        model, model_type = get_model_ofuri_type(value)
        if isinstance(model, Product):
            components_for_model = queryset.filter(products__ofuri=value)
        elif isinstance(model, ProductVersion):
            components_for_model = queryset.filter(productversions__ofuri=value)
        elif isinstance(model, ProductStream):
            components_for_model = queryset.filter(productstreams__ofuri=value)
        elif isinstance(model, ProductVariant):
            components_for_model = queryset.filter(productvariants__ofuri=value)
        else:
            components_for_model = queryset
        return components_for_model.root_components().latest_components(
            model_type=model_type,
            ofuri=value,
        )


class ProductDataFilter(FilterSet):
    """Class that filters queries to Product-related list views."""

    name = CharFilter()
    re_name = CharFilter(lookup_expr="iregex", field_name="name")
    re_ofuri = CharFilter(lookup_expr="iregex", field_name="ofuri")
    tags = TagFilter()

    products = CharFilter(lookup_expr="name__icontains")
    product_versions = CharFilter(field_name="productversions", lookup_expr="name__icontains")
    product_streams = CharFilter(field_name="productstreams", lookup_expr="name__icontains")
    product_variants = CharFilter(field_name="productvariants", lookup_expr="name__icontains")
    channels = CharFilter(lookup_expr="name__icontains")


class ChannelFilter(FilterSet):
    """Class that filters queries to Channel-related list views."""

    name = CharFilter(lookup_expr="icontains")

    class Meta:
        model = Channel
        fields = ("type",)


class SoftwareBuildFilter(FilterSet):
    """Class that filters queries to SoftwareBuild views."""

    name = CharFilter(lookup_expr="icontains")
    tags = TagFilter()

    class Meta:
        model = SoftwareBuild
        fields = (
            "build_id",
            "build_type",
        )
