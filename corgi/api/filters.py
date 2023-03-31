import logging

from django.core.validators import EMPTY_VALUES
from django.db.models import QuerySet
from django_filters.rest_framework import BooleanFilter, CharFilter, Filter, FilterSet

from corgi.core.models import Channel, Component, SoftwareBuild

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
            if ":" in tag:
                tag_name, _, tag_value = tag.partition(":")
                queryset = queryset.filter(
                    tags__name__icontains=tag_name, tags__value__icontains=tag_value
                )
            else:
                queryset = queryset.filter(tags__name__icontains=tag)
        return queryset


class ComponentFilter(FilterSet):
    """Class that filters queries to Component list views."""

    class Meta:
        model = Component
        # Fields that are matched to a filter using their Django model field type and default
        # __exact lookups.
        fields = ("type", "namespace", "name", "version", "release", "arch", "nvr", "nevra")

    # Custom filters
    re_name = CharFilter(lookup_expr="regex", field_name="name")
    re_purl = CharFilter(lookup_expr="regex", field_name="purl")
    description = CharFilter(lookup_expr="icontains")
    related_url = CharFilter(lookup_expr="icontains")
    tags = TagFilter()

    # User gave a filter like ?ofuri= in URL, assume they wanted a stream
    ofuri = CharFilter(field_name="productstreams", lookup_expr="ofuri")
    products = CharFilter(method="filter_ofuri_or_name")
    product_versions = CharFilter(field_name="productversions", method="filter_ofuri_or_name")
    product_streams = CharFilter(field_name="productstreams", method="filter_ofuri_or_name")
    product_variants = CharFilter(field_name="productvariants", method="filter_ofuri_or_name")
    channels = CharFilter(lookup_expr="name")

    # Normally we are interested in retrieving provides,sources or upstreams of a specific component
    sources = CharFilter(lookup_expr="purl")
    provides = CharFilter(lookup_expr="purl")
    upstreams = CharFilter(lookup_expr="purl")
    # otherwise use regex to match provides,sources or upstreams purls
    re_sources = CharFilter(field_name="sources", lookup_expr="purl__regex")
    re_provides = CharFilter(field_name="provides", lookup_expr="purl__regex")
    re_upstreams = CharFilter(field_name="upstreams", lookup_expr="purl__regex")

    el_match = CharFilter(label="RHEL version for layered products", lookup_expr="icontains")

    missing_copyright = EmptyStringFilter(
        field_name="copyright_text",
        label="Show only unscanned components (where copyright text is empty)",
    )

    missing_license = EmptyStringFilter(
        field_name="license_concluded_raw",
        label="Show only unscanned components (where license concluded is empty)",
    )

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


class ProductDataFilter(FilterSet):
    """Class that filters queries to Product-related list views."""

    name = CharFilter()
    re_name = CharFilter(lookup_expr="regex", field_name="name")
    re_ofuri = CharFilter(lookup_expr="regex", field_name="ofuri")
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
        fields = ("build_type",)
