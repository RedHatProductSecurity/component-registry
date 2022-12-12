from rest_framework.renderers import BrowsableAPIRenderer


class APIRendererWithoutFavicon(BrowsableAPIRenderer):
    """Override the default DRF renderer to avoid 404 error spam in developer console"""

    template = "api.html"
