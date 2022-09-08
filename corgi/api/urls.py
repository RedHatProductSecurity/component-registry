from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework import routers

from .views import (
    ComponentView,
    CoverageReportViewSet,
    ProductStreamView,
    ProductVariantView,
    ProductVersionView,
    ProductView,
    RelationsView,
    SoftwareBuildView,
    StatusViewSet,
)


class ComponentRegistryAPI(routers.APIRootView):
    """
    Component Registry API root
    """

    # The title of this class is what gets used in the web page header at /api/v1; the doc string
    # is the description of the view.

    pass


class ComponentRegistryRouter(routers.DefaultRouter):
    APIRootView = ComponentRegistryAPI


router = ComponentRegistryRouter(trailing_slash=False)
router.register(r"builds", SoftwareBuildView)
router.register(r"components", ComponentView)
# Comment out until app-stream life cycles are incorporated into data
# router.register(r"lifecycles", AppStreamLifeCycleView)
router.register(r"products", ProductView)
router.register(r"product_versions", ProductVersionView)
router.register(r"product_streams", ProductStreamView)
router.register(r"product_variants", ProductVariantView)
# Comment out until we start loading Channels and tying them to products/errata
# router.register(r"channels", ChannelView)
router.register(r"status", StatusViewSet, basename="status")
router.register(r"reports/coverage", CoverageReportViewSet, basename="coverage-reports")
router.register(r"relations", RelationsView)

urlpatterns = [
    # v1 API
    path(r"schema", SpectacularAPIView.as_view(), name="schema"),
    path(
        r"schema/docs",
        SpectacularSwaggerView.as_view(url_name="schema"),
    ),
    path("", include(router.urls)),
]
