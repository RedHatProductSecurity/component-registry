import os

from django import template

from corgi import __version__

register = template.Library()


@register.simple_tag
def app_docs():
    return os.getenv("CORGI_DOCS_URL")


@register.simple_tag
def app_email():
    return os.getenv("PRODSEC_EMAIL")


@register.simple_tag
def app_version():
    return __version__


@register.simple_tag
def app_git_ref():
    """Display Git commit hash when running in prod/stage.

    This is useful especially in the staging env where we are running off of master and can't
    solely rely on version numbers.
    """
    git_ref = os.getenv("OPENSHIFT_BUILD_COMMIT")
    if git_ref:
        return f" ({git_ref[:8]})"
    else:
        return ""
