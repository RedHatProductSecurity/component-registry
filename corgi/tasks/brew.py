from datetime import datetime, timedelta
from typing import Optional

import koji
from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, QuerySet
from django.utils import dateformat, dateparse, timezone
from django.utils.timezone import make_aware

from config.celery import app
from corgi.collectors.brew import ADVISORY_REGEX, Brew, BrewBuildTypeNotSupported
from corgi.core.models import (
    Component,
    ComponentNode,
    ComponentQuerySet,
    ProductComponentRelation,
    ProductStream,
    SoftwareBuild,
)
from corgi.tasks.common import (
    BUILD_TYPE,
    RETRY_KWARGS,
    RETRYABLE_ERRORS,
    create_relations,
    get_last_success_for_task,
)
from corgi.tasks.errata_tool import slow_load_errata
from corgi.tasks.sca import cpu_software_composition_analysis

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_brew_build(
    build_id: str,
    build_type: str = BUILD_TYPE,
    save_product: bool = True,
    force_process: bool = False,
) -> bool:
    logger.info("Fetch brew build called with build id: %s", build_id)

    if SoftwareBuild.objects.filter(build_id=build_id, build_type=build_type).exists():
        logger.info("Already processed build_id %s", build_id)

        # Build exists, and we don't want to reload it
        if not force_process:
            # But we want to save the taxonomy again
            if save_product:
                logger.info("Only saving product taxonomy for build_id %s", build_id)
                slow_save_taxonomy.delay(build_id, build_type)
            return False
        # Else build exists, but we do want to reload it
    # Else build doesn't exist
    logger.info("Fetching brew build with build_id: %s", build_id)

    try:
        component = Brew(build_type).get_component_data(int(build_id))
    except BrewBuildTypeNotSupported as exc:
        logger.warning(str(exc))
        return False

    if not component:
        logger.info("No data fetched for build %s from Brew, exiting...", build_id)
        return False

    build_meta = component["build_meta"]["build_info"]
    build_meta["corgi_ingest_start_dt"] = dateformat.format(timezone.now(), "Y-m-d H:i:s")
    build_meta["corgi_ingest_status"] = "INPROGRESS"

    completion_time = build_meta.get("completion_time", "")
    if completion_time:
        dt = dateparse.parse_datetime(completion_time.split(".")[0])
        if dt:
            completion_dt = make_aware(dt)
        else:
            raise ValueError(f"Could not parse completion_time for build {build_id}")
    else:
        # Build has no timestamp even though it's in COMPLETE state?
        # This really shouldn't happen
        raise ValueError(f"No completion_time for build {build_id}")

    # TODO: Should we update_or_create to pick up changed build_meta?
    #  Sometimes builds are deleted in Brew
    #  In that case, don't wipe out data - test this
    softwarebuild, build_created = SoftwareBuild.objects.get_or_create(
        build_id=build_id,
        build_type=build_type,
        defaults={
            "source": build_meta.pop("source"),
            "meta_attr": build_meta,
            "name": component["meta"]["name"],
        },
        completion_time=completion_dt,
    )
    if build_created:
        # Create foreign key from Relations to the new SoftwareBuild, where they don't already exist
        ProductComponentRelation.objects.filter(
            build_id=build_id, build_type=build_type, software_build__isnull=True
        ).update(software_build=softwarebuild)

    if not force_process and not build_created:
        # If another task starts while this task is downloading data this can result in processing
        # the same build twice, let's just bail out here to save cpu
        logger.warning("SoftwareBuild with build_id %s already existed, not reprocessing", build_id)
        return False

    if component["type"] == Component.Type.RPM:
        root_node, root_created = save_srpm(softwarebuild, component)
    elif component["type"] == Component.Type.CONTAINER_IMAGE:
        root_node, root_created = save_container(softwarebuild, component)
    elif component["type"] == Component.Type.RPMMOD:
        root_node, root_created = save_module(softwarebuild, component)
    else:
        logger.warning(f"Build {build_id} type is not supported: {component['type']}")
        return False

    any_child_created = False
    for child_component in component.get("components", []):
        any_child_created |= save_component(child_component, root_node)

    # for builds with any tag, check if the tag is used for product stream relations, and create the
    # relations if so.
    if not build_meta["tags"]:
        logger.info("no brew tags")
    else:
        new_relations = load_brew_tags(softwarebuild, build_meta["tags"])
        logger.info(f"Created {new_relations} for brew tags in {build_type}:{build_id}")

    # Allow async call of slow_load_errata task, see CORGI-21
    if save_product:
        slow_save_taxonomy.delay(build_id, build_type)

    # for builds with errata tags set ProductComponentRelation
    # get_component_data always calls _extract_advisory_ids to set tags, but list may be empty
    if not build_meta["errata_tags"]:
        logger.info("no errata tags")
    else:
        for e in build_meta["errata_tags"]:
            slow_load_errata.delay(e, force_process=force_process)

    build_ids = component.get("nested_builds", ())
    logger.info("Fetching brew builds for (%s, %s)", build_ids, build_type)
    for b_id in build_ids:
        logger.info("Requesting fetch of nested build: (%s, %s)", b_id, build_type)
        slow_fetch_brew_build.delay(
            b_id, build_type, save_product=save_product, force_process=force_process
        )

    logger.info("Requesting software composition analysis for %s", softwarebuild.pk)
    if settings.SCA_ENABLED:
        cpu_software_composition_analysis.delay(str(softwarebuild.pk), force_process=force_process)

    logger.info(
        "Created build (%s) or root (%s) or child(ren) (%s) for brew build: (%s, %s)",
        build_created,
        root_created,
        any_child_created,
        build_id,
        build_type,
    )
    logger.info("Finished fetching brew build: (%s, %s)", build_id, build_type)
    return build_created or root_created or any_child_created


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_modular_build(
    build_id: str, save_product: bool = True, force_process: bool = False
) -> bool:
    logger.info("Fetch modular build called with build id: %s", build_id)
    rhel_module_data = Brew.fetch_rhel_module(build_id)
    # Some compose build_ids in the relations table will be for SRPMs, skip those here
    if not rhel_module_data:
        logger.info("No module data fetched for build %s from Brew, exiting...", build_id)
        slow_fetch_brew_build.delay(build_id, force_process=force_process)
        return False
    # Note: module builds don't include arch information, only the individual RPMs that make up a
    # module are built for specific architectures.
    # TODO: Merge below with similar logic in save_module() if possible
    meta = rhel_module_data["meta"]
    obj, created = Component.objects.update_or_create(
        type=rhel_module_data["type"],
        name=meta.pop("name"),
        version=meta.pop("version"),
        release=meta.pop("release"),
        arch="noarch",
        defaults={
            # Any remaining meta keys are recorded in meta_attr
            "meta_attr": meta,
            "namespace": Component.Namespace.REDHAT,
        },
    )
    # This should result in a lookup if slow_fetch_brew_build has already processed this module.
    # Likewise if slow_fetch_brew_build processes the module subsequently we should not create
    # a new ComponentNode, instead the same one will be looked up and used as the root node
    node, node_created = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)

    any_child_created = False
    for c in rhel_module_data.get("components", []):
        # Request fetch of the SRPM build_ids here to ensure software_builds are created and linked
        # to the RPM components. We don't link the SRPM into the tree because some of it's RPMs
        # might not be included in the module
        if "brew_build_id" in c:
            slow_fetch_brew_build.delay(c["brew_build_id"])
        any_child_created |= save_component(c, node)
    slow_fetch_brew_build.delay(build_id, save_product=save_product, force_process=force_process)
    logger.info("Finished fetching modular build: %s", build_id)
    return created or node_created or any_child_created


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def slow_save_taxonomy(build_id: str, build_type: str) -> None:
    """Helper function to call save_product_taxonomy()
    and save_component_taxonomy() in a separate task
    to reduce the risk of timeouts in slow_fetch_brew_build"""
    logger.info(f"Saving product taxonomy for {build_type} build {build_id}")
    build = SoftwareBuild.objects.get(build_id=build_id, build_type=build_type)
    build.save_product_taxonomy()

    # Below only saves the component taxonomy for the root component
    # There should only be one root component / node per build
    # but there are some historical data issues
    # TODO: Save the taxonomy for all components in the tree, CORGI-739
    #  We tried to do this in CORGI-730 but it caused SoftTimeLimitExceeded errors
    #  We may need to switch from GenericForeignKey to regular ForeignKey first
    logger.info(f"Saving component taxonomy for {build_type} build {build_id}")
    for root_component in build.components.get_queryset():
        root_component.save_component_taxonomy()
    logger.info(f"Finished saving taxonomies for {build_type} build {build_id}")


def save_component(component: dict, parent: ComponentNode) -> bool:
    logger.debug("Called save component with component %s", component)
    component_type = component.pop("type")
    meta = component.get("meta", {})

    node_type = ComponentNode.ComponentNodeType.PROVIDES
    if meta.pop("dev", False):
        node_type = ComponentNode.ComponentNodeType.PROVIDES_DEV

    # Map Cachito (lowercase) type to Corgi TYPE, or use existing Corgi TYPE, or raise error
    if component_type in ("go-package", "gomod"):
        meta["go_component_type"] = component_type
        component_type = Component.Type.GOLANG
    elif component_type in Brew.CACHITO_PKG_TYPE_MAPPING:
        component_type = Brew.CACHITO_PKG_TYPE_MAPPING[component_type]
    elif component_type.upper() in Component.Type.values:
        component_type = component_type.upper()
    else:
        raise ValueError(f"Tried to create component with invalid component_type: {component_type}")

    related_url = meta.pop("url", "")
    if not related_url:
        # Only RPMs have a URL header, containers might have below
        related_url = meta.get("repository_url", "")
    if not related_url:
        # Handle case when key is present but value is None
        related_url = ""

    license_declared_raw = meta.pop("license", "")

    name = meta.pop("name", "")
    version = meta.pop("version", "")
    release = meta.pop("release", "")
    arch = meta.pop("arch", "noarch")

    epoch = int(meta.pop("epoch", 0))
    description = meta.pop("description", "")
    namespace = Brew.check_red_hat_namespace(component_type, version)

    if epoch:
        nevra = f"{name}:{epoch}-{version}"
    else:
        nevra = f"{name}-{version}"

    if release:
        nevra = f"{nevra}-{release}.{arch}"
    else:
        nevra = f"{nevra}.{arch}"

    defaults = {
        "description": description,
        "epoch": epoch,
        "namespace": namespace,
        "related_url": related_url,
    }
    try:
        obj, created = Component.objects.update_or_create(
            type=component_type,
            name=name,
            version=version,
            release=release,
            arch=arch,
            defaults=defaults,
        )
    except IntegrityError:
        # Return a queryset we can .update(), but there's only one match
        match = handle_duplicate_component(component_type, name, nevra)
        # Using .update() here caused deadlocks, maybe? Not sure of cause
        with transaction.atomic():
            obj = match.get()
            for field_name in defaults:
                setattr(obj, field_name, defaults[field_name])
            obj.save()
        created = False

    set_license_declared_safely(obj, license_declared_raw)

    # Usually component_meta is an empty dict by the time we get here, but if it's not, and we have
    # new keys, add them to the existing meta_attr. Only call save if something has been added
    if meta:
        obj.meta_attr = obj.meta_attr | meta
        obj.save()

    node, node_created = save_node(node_type, parent, obj)
    any_child_created = recurse_components(component, node)
    return created or node_created or any_child_created


def handle_duplicate_component(
    component_type: Component.Type, name: str, nevra: str
) -> ComponentQuerySet:
    """Handle an IntegrityError when saving a "new" Component
    which is really a duplicate Component that almost matches an existing NEVRA
    and which generates the same purl"""
    # "Same purl for different NEVRAs" should happen only for Github and Python components
    # These purls are always lowercase, but NEVRAs can be lowercase or mixed-case
    # Finding the existing Component fails due to the mismatched casing in the name
    # and creating a new Component fails due to an IntegrityError / duplicate purl values

    # We can't easily build a purl before the component is saved,
    # so we can't handle the IntegrityError (duplicate purl) here like we do in the SCA task
    # Instead, let's try to find the same component's NEVRA with a different case
    # and reuse / update that component
    possible_matches = Component.objects.filter(type=component_type, nevra__iexact=nevra)
    # TODO: Add test for - dash / _ underscore that both get converted to - in purls
    #  e.g. for build 2617813 / NEVRA typing_extensions-3.10.0.2.noarch
    #  and purl pkg:pypi/typing-extensions@3.10.0.2
    if len(possible_matches) > 1:
        # There can be multiple results for one NEVRA if e.g. a binary RPM and PyPI package
        # have the same name, version, and arch (PyYAML 5.4.1 noarch)
        # We also filter by type above, so this code should never get hit
        raise ValueError(
            f"New edge case when handling duplicate {component_type} component {name}: "
            f"too many case-insensitive matches for NEVRA {nevra} ({len(possible_matches)})"
        )
    elif len(possible_matches) == 0:
        possible_matches = handle_dash_underscore_confusion(component_type, name, nevra)

    # else there was only one match initially, so extra logic above isn't needed
    # Now that we have only one possible match, return it so it can be updated instead of created
    return possible_matches


def handle_dash_underscore_confusion(
    component_type: Component.Type, name: str, nevra: str
) -> ComponentQuerySet:
    """Handle an IntegrityError when saving a "new" Component
    which is really a duplicate Component that matches an existing NEVRA
    except for dashes vs. underscores, and which generates the same purl"""
    # Couldn't find a match for "same NEVRA, different case" that caused IntegrityError
    # Underscores and dashes in names are converted to dashes in PyPI purls only
    # So two components with different NEVRAs may still end up with the same purl
    # TODO: check logic against SCA task
    name_with_dash = name.replace("_", "-")
    name_with_underscore = name.replace("-", "_")
    nevra_with_dash = nevra.replace(name, name_with_dash, 1)
    nevra_with_underscore = nevra.replace(name, name_with_underscore, 1)
    dash_count = 0
    underscore_count = 0

    if "-" in name and "_" in name:
        # Check for both possible variants - only dashes, only underscores
        dash_matches = Component.objects.filter(type=component_type, nevra__iexact=nevra_with_dash)
        dash_count = len(dash_matches)
        underscore_matches = Component.objects.filter(
            type=component_type, nevra__iexact=nevra_with_underscore
        )
        underscore_count = len(underscore_matches)

        if dash_count > 0 and underscore_count > 0:
            # There are PyPI / Github / RPM components in stage today
            # with both dashes and underscores in their names
            # In that case, we don't know the right NEVRA to use
            possible_matches = dash_matches.union(underscore_matches)
        elif dash_count == 1:
            possible_matches = dash_matches
        elif underscore_count == 1:
            possible_matches = underscore_matches
        else:
            # There's a duplicate of this component / purl,
            # but we couldn't find the same NEVRA with a different case,
            # and we couldn't find a similar NEVRA with only dashes or underscores in it
            # (0 matches) OR we have too many matches for one of "only dashes" or "only underscores"
            # We shouldn't get here, but if we do, it's an edge case we're not handling
            raise ValueError(
                f"New edge case when handling duplicate {component_type} component {name}: "
                f"no case-insensitive matches for NEVRAs {nevra} or "
                f"{nevra_with_dash} or {nevra_with_underscore}"
            )

    elif "-" in name:
        # Only dashes in name, check for same NEVRA with underscores
        possible_matches = Component.objects.filter(
            type=component_type, nevra__iexact=nevra_with_underscore
        )
        underscore_count = len(possible_matches)

    elif "_" in name:
        # Only underscores in name, check for same NEVRA with dashes
        possible_matches = Component.objects.filter(
            type=component_type, nevra__iexact=nevra_with_dash
        )
        dash_count = len(possible_matches)

    else:
        # There's a duplicate of this component / purl,
        # but we couldn't find the same NEVRA with a different case,
        # and the name didn't have any dashes or underscores in it
        # We shouldn't get here, but if we do, it's an edge case we're not handling
        raise ValueError(
            f"New edge case when handling duplicate {component_type} component {name}: "
            f"no case-insensitive matches for NEVRA {nevra} "
            "which has no dashes or underscores"
        )

    if len(possible_matches) != 1:
        raise ValueError(
            f"New edge case when handling duplicate {component_type} component {name}: "
            f"no case-insensitive matches for NEVRA {nevra} "
            f"and too many matches ({len(possible_matches)}) "
            f"for {nevra_with_dash} ({dash_count}) or {nevra_with_underscore} ({underscore_count})"
        )

    return possible_matches


def save_srpm(softwarebuild: SoftwareBuild, build_data: dict) -> tuple[ComponentNode, bool]:
    name = build_data["meta"].pop("name")
    version = build_data["meta"].pop("version")
    related_url = build_data["meta"].pop("url", "")
    epoch = build_data["meta"].pop("epoch", 0)
    if not related_url:
        # Handle case when key is present but value is None
        related_url = ""

    extra = {
        "description": build_data["meta"].pop("description", ""),
        "license_declared_raw": build_data["meta"].pop("license", ""),
        "related_url": related_url,
    }

    obj, created = Component.objects.update_or_create(
        type=build_data["type"],
        name=name,
        version=version,
        release=build_data["meta"].pop("release", ""),
        arch=build_data["meta"].pop("arch", "noarch"),
        defaults={
            **extra,
            "meta_attr": build_data["meta"],
            "namespace": Component.Namespace.REDHAT,
            "software_build": softwarebuild,
            "epoch": int(epoch),
        },
    )
    node, node_created = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)
    upstream_created = False
    if related_url:
        _, _, upstream_created = save_upstream(
            build_data["type"], name, version, build_data["meta"], extra, node
        )
    return node, created or node_created or upstream_created


def process_image_components(image):
    builds_to_fetch = set()
    if "rpm_components" in image:
        for rpm in image["rpm_components"]:
            builds_to_fetch.add(rpm["brew_build_id"])
        # TODO save the list of rpms by image to the container meta for reconcilation.
    return builds_to_fetch


def set_license_declared_safely(obj: Component, license_declared_raw: str) -> None:
    """Save a declared license onto a Component, without erasing any existing value"""
    # The license value we have in the DB doesn't match the value from Brew
    different_license = Component.objects.filter(pk=obj.pk).exclude(
        license_declared_raw=license_declared_raw
    )
    # AND the value from Brew isn't an empty string
    if license_declared_raw and different_license:
        # Any non-empty license here should be reported
        # We only rely on OpenLCS if we don't know the license_declared
        # But we can't set license_declared in update_or_create
        # If the license in the metadata is an empty string and we are reprocessing,
        # we might erase the license that OpenLCS provided
        # They cannot erase any licenses we set ourselves (when the field is not empty)
        # API endpoint blocks this (400 Bad Request)
        # Use .update() to avoid possible race conditions
        different_license.update(license_declared_raw=license_declared_raw)


def save_container(softwarebuild: SoftwareBuild, build_data: dict) -> tuple[ComponentNode, bool]:
    license_declared_raw = build_data["meta"].pop("license", "")
    related_url = build_data["meta"].get("repository_url", "")
    if not related_url:
        # Handle case when key is present but value is None
        related_url = ""

    obj, root_created = Component.objects.update_or_create(
        type=build_data["type"],
        name=build_data["meta"].pop("name"),
        version=build_data["meta"].pop("version"),
        release=build_data["meta"].pop("release"),
        arch="noarch",
        defaults={
            "description": build_data["meta"].pop("description", ""),
            "filename": build_data["meta"].pop("filename", ""),
            "meta_attr": build_data["meta"],
            "namespace": Component.Namespace.REDHAT,
            "related_url": related_url,
            "software_build": softwarebuild,
        },
    )

    set_license_declared_safely(obj, license_declared_raw)
    root_node, root_node_created = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)
    any_image_created = any_image_node_created = False
    any_go_module_created = any_rpm_created = False
    any_source_created = any_cachito_created = False

    if "upstream_go_modules" in build_data["meta"]:
        meta_attr = {"go_component_type": "gomod", "source": ["collectors/brew"]}
        for module in build_data["meta"]["upstream_go_modules"]:
            # the upstream commit is included in the dist-git commit history, but is not
            # exposed anywhere in the brew data that I can find, so can't set version
            _, _, temp_created = save_upstream(
                Component.Type.GOLANG, module, "", meta_attr, {}, root_node
            )
            any_go_module_created |= temp_created

    if "image_components" in build_data:
        for image in build_data["image_components"]:
            license_declared_raw = image["meta"].pop("license", "")

            obj, temp_created = Component.objects.update_or_create(
                type=image["type"],
                name=image["meta"].pop("name"),
                version=image["meta"].pop("version"),
                release=image["meta"].pop("release"),
                arch=image["meta"].pop("arch"),
                defaults={
                    "description": image["meta"].pop("description", ""),
                    "filename": image["meta"].pop("filename", ""),
                    "meta_attr": image["meta"],
                    "namespace": Component.Namespace.REDHAT,
                },
            )
            any_image_created |= temp_created

            set_license_declared_safely(obj, license_declared_raw)
            # Based on a conversation with the container factory team,
            # almost all image components are build-time dependencies in a multi-stage build
            # and are discarded / do not end up in the final image.
            # The only exceptions are image components from the base layer (ie UBI)
            # So we should probably still use PROVIDES here, and not PROVIDES_DEV
            # Unless we can distinguish between these two types of components
            # using some other Brew metadata
            image_arch_node, temp_created = save_node(
                ComponentNode.ComponentNodeType.PROVIDES, root_node, obj
            )
            any_image_node_created |= temp_created

            if "rpm_components" in image:
                for rpm in image["rpm_components"]:
                    any_rpm_created |= save_component(rpm, image_arch_node)
                    # SRPMs are loaded using nested_builds

    if "sources" in build_data:
        for source in build_data["sources"]:
            component_name = source["meta"].pop("name")
            component_version = source["meta"].pop("version")
            related_url = source["meta"].pop("url", "")
            if not related_url:
                # Handle case when key is present but value is None
                related_url = ""
                if component_name.startswith("github.com/"):
                    related_url = f"https://{component_name}"
            if "openshift-priv" in related_url:
                # Component name is something like github.com/openshift-priv/cluster-api
                # The public repo we want is just github.com/openshift/cluster-api
                related_url = related_url.replace("openshift-priv", "openshift")

            if source["type"] == Component.Type.GOLANG:
                # Assume upstream container sources are always go modules, never go-packages
                source["meta"]["go_component_type"] = "gomod"

            extra = {"related_url": related_url}
            _, upstream_node, temp_created = save_upstream(
                source["type"], component_name, component_version, source["meta"], extra, root_node
            )
            any_source_created |= temp_created

            # Collect the Cachito dependencies
            temp_created = recurse_components(source, upstream_node)
            any_cachito_created |= temp_created
    return root_node, (
        (root_created or root_node_created)
        or (any_image_created or any_image_node_created)
        or (any_go_module_created or any_rpm_created)
        or (any_source_created or any_cachito_created)
    )


def recurse_components(component: dict, parent: ComponentNode) -> bool:
    any_child_created = False
    if not parent:
        logger.warning(f"Failed to create ComponentNode for component: {component}")
    else:
        if "components" in component:
            for child in component["components"]:
                any_child_created |= save_component(child, parent)
    return any_child_created


def save_module(softwarebuild, build_data) -> tuple[ComponentNode, bool]:
    """Upstreams are not created because modules have no related source code. They are a
    collection of RPMs from other SRPMS. The upstreams can be looked up from all the RPM children.
    No child components are created here because we don't have enough data in Brew to determine
    the relationships. We create the relationships using data from RHEL_COMPOSE, or RPM repository
    See CORGI-200, and CORGI-163"""
    meta_attr = build_data["meta"]["meta_attr"]
    obj, created = Component.objects.update_or_create(
        type=build_data["type"],
        name=build_data["meta"]["name"],
        version=build_data["meta"].get("version", ""),
        release=build_data["meta"].get("release", ""),
        arch=build_data["meta"].get("arch", "noarch"),
        defaults={
            "description": build_data["meta"].get("description", ""),
            "license_declared_raw": build_data["meta"].get("license", ""),
            "meta_attr": meta_attr,
            "namespace": Component.Namespace.REDHAT,
            "software_build": softwarebuild,
        },
    )
    node, node_created = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)

    return node, created or node_created


def save_upstream(
    component_type: str, name: str, version: str, meta_attr: dict, extra: dict, node: ComponentNode
) -> tuple[Component, ComponentNode, bool]:
    """Helper function to save an upstream component and create a node for it"""
    upstream_component, created = Component.objects.update_or_create(
        type=component_type,
        name=name,
        version=version,
        release="",
        arch="noarch",
        defaults={
            **extra,
            "meta_attr": meta_attr,
            "namespace": Component.Namespace.UPSTREAM,
        },
    )
    upstream_node, node_created = save_node(
        ComponentNode.ComponentNodeType.SOURCE, node, upstream_component
    )

    return upstream_component, upstream_node, created or node_created


def save_node(
    node_type: str, parent: Optional[ComponentNode], related_component: Component
) -> tuple[ComponentNode, bool]:
    """Helper function that wraps ComponentNode creation"""
    node, created = ComponentNode.objects.get_or_create(
        type=node_type,
        parent=parent,
        purl=related_component.purl,
        defaults={"obj": related_component},
    )
    return node, created


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def load_stream_brew_tags() -> None:
    for ps in ProductStream.objects.get_queryset():
        brew, build_type, refresh_task = _relation_context_for_stream(ps.name)
        for brew_tag, inherit in ps.brew_tags.items():
            builds = brew.get_builds_with_tag(brew_tag, inherit=inherit, latest=False)
            no_of_created = create_relations(
                builds,
                build_type,
                brew_tag,
                ps.name,
                ProductComponentRelation.Type.BREW_TAG,
                refresh_task,
            )
            logger.info("Saving %s new builds for %s", no_of_created, brew_tag)


def load_brew_tags(software_build: SoftwareBuild, brew_tags: list[str]) -> int:
    all_stream_tags = ProductStream.objects.exclude(brew_tags__exact={}).values_list(
        "name", "brew_tags"
    )
    no_created = 0
    distinct_brew_tags = set(brew_tags)
    for stream_name, stream_tags in all_stream_tags:
        distinct_stream_tags = set(stream_tags)
        distinct_stream_tags = distinct_brew_tags.intersection(distinct_stream_tags)
        for tag in distinct_stream_tags:
            brew, build_type, _ = _relation_context_for_stream(stream_name)
            logger.info(f"Creating relations for {stream_name} and {tag}")
            # We do this inline instead of via create_relations function because
            # it's the only time we call it where we have a software_build object created
            _, created = ProductComponentRelation.objects.update_or_create(
                external_system_id=tag,
                product_ref=stream_name,
                build_id=software_build.build_id,
                build_type=build_type,
                defaults={
                    "type": ProductComponentRelation.Type.BREW_TAG,
                    "software_build": software_build,
                },
            )
            if created:
                no_created += 1
    return no_created


def _relation_context_for_stream(stream_name: str):
    build_type = BUILD_TYPE
    brew = Brew(BUILD_TYPE)
    refresh_task = slow_fetch_modular_build
    # This should really be a property in Product Definitions
    if settings.COMMUNITY_MODE_ENABLED and stream_name == "openstack-rdo":
        brew = Brew(SoftwareBuild.Type.CENTOS)
        build_type = SoftwareBuild.Type.CENTOS
        refresh_task = slow_fetch_brew_build
    return brew, build_type, refresh_task


def fetch_modular_builds(relations_query: QuerySet, force_process: bool = False) -> None:
    for build_id in relations_query:
        slow_fetch_modular_build.delay(build_id, force_process=force_process)


def fetch_unprocessed_relations(
    created_since: Optional[datetime] = None,
    product_ref: Optional[str] = "",
    relation_type: Optional[ProductComponentRelation.Type] = None,
    force_process: bool = False,
) -> int:
    """Load Brew builds for relations which don't have an associated SoftwareBuild.
    Accepts optional arguments product_ref and relation_type which add query filters"""
    query = Q()
    if relation_type:
        query &= Q(type=relation_type)
        logger.info(f"Processing relations of type {relation_type}")
    if product_ref:
        query &= Q(product_ref=product_ref)
        logger.info(f"Processing relations with product reference {product_ref}")

    if created_since:
        query &= Q(created_at__gte=created_since)
    relations_query = ProductComponentRelation.objects.filter(query).filter(software_build=None)

    processed_builds = 0
    for relation in relations_query.iterator():
        logger.info(f"Processing {relation.type} relation build with id: {relation.build_id}")
        if relation.build_type == SoftwareBuild.Type.CENTOS:
            # This skips use of the Collector models for builds in the CENTOS koji instance
            # It was done to avoid updating the collector models not to use build_id as
            # a primary key. It's possible because the only product stream (openstack-rdo)
            # stored in CENTOS koji doesn't use modules
            slow_fetch_brew_build.delay(
                relation.build_id, SoftwareBuild.Type.CENTOS, force_process=force_process
            )
        else:
            slow_fetch_modular_build.delay(relation.build_id, force_process=force_process)
        processed_builds += 1
    return processed_builds


@app.task(
    base=Singleton,
    autorety_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def fetch_unprocessed_brew_tag_relations(
    force_process: bool = False, days_created_since: int = 0
) -> int:
    if days_created_since:
        created_dt = timezone.now() - timedelta(days=days_created_since)
    else:
        created_dt = get_last_success_for_task(
            "corgi.tasks.brew.fetch_unprocessed_brew_tag_relations"
        )
    return fetch_unprocessed_relations(
        relation_type=ProductComponentRelation.Type.BREW_TAG,
        force_process=force_process,
        created_since=created_dt,
    )


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_update_brew_tags(build_id: str, tag_added: str = "", tag_removed: str = "") -> str:
    """Update a build's list of tags in Corgi when they change in Brew"""
    if not tag_added and not tag_removed:
        raise ValueError("Must supply one tag to be added or removed")

    with transaction.atomic():
        build = SoftwareBuild.objects.filter(
            build_id=build_id, build_type=SoftwareBuild.Type.BREW
        ).first()
        if not build:
            logger.warning(f"Brew build with matching ID not ingested (yet?): {build_id}")
            # Include warning message in Celery task result
            # We don't raise an error here because there are potentially many builds
            # that we haven't loaded, but which could have tags updated at any time
            return f"Brew build with matching ID not ingested (yet?): {build_id}"

        if tag_added:
            tags = set(build.meta_attr["tags"])
            tags.add(tag_added)
            build.meta_attr["tags"] = sorted(tags)
            errata_tag = ADVISORY_REGEX.match(tag_added)
            if errata_tag:
                # Below should automatically create new relations for this build / erratum
                slow_load_errata.delay(errata_tag.group())
        else:
            try:
                build.meta_attr["tags"].remove(tag_removed)
                # TODO: Clean up old relations for some build / erratum when a tag is removed
            except ValueError:
                # Tag to be removed not found in list
                # i.e. it was renamed earlier and is now being removed
                # We don't get a UMB event for these renames
                # Refresh all the tags so we have the most current data
                logger.warning(f"Tag to remove {tag_removed} not found, so refreshing all tags")
                slow_refresh_brew_build_tags.delay(int(build_id))
                return f"Tag to remove {tag_removed} not found, so refreshing all tags"

        build.meta_attr["errata_tags"] = Brew.extract_advisory_ids(build.meta_attr["tags"])
        build.meta_attr["released_errata_tags"] = Brew.parse_advisory_ids(
            build.meta_attr["errata_tags"]
        )
        build.save()
        return f"Added tag {tag_added} or removed tag {tag_removed} for build {build_id}"


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_refresh_brew_build_tags(build_id: int) -> None:
    """Refresh tags for a Brew build when some erratum releases it"""
    # We can't rely on above tag added / removed logic
    # The tags are only renamed, but there's no UMB event for this
    # Errata Tool's UMB messages only have info about the errata tags
    # We also need to update non-errata tags that link streams to builds
    # e.g. stream-name-candidate will change to stream-name-released

    logger.info(f"Refreshing Brew build tags for {build_id}")
    brew = Brew(SoftwareBuild.Type.BREW)
    tags = sorted(set(tag["name"] for tag in brew.koji_session.listTags(build_id)))
    errata_tags = Brew.extract_advisory_ids(tags)
    released_errata_tags = Brew.parse_advisory_ids(errata_tags)

    with transaction.atomic():
        # Can't use .update(key="value") on individual keys in a JSONField
        build = SoftwareBuild.objects.get(
            build_type=SoftwareBuild.Type.BREW, build_id=str(build_id)
        )
        # If the newly-refreshed tags have errata that weren't present before
        # We need to create relations for these new errata tags
        new_errata_tags = set(errata_tags) - set(build.meta_attr["errata_tags"])

        build.meta_attr["tags"] = tags
        build.meta_attr["errata_tags"] = errata_tags
        build.meta_attr["released_errata_tags"] = released_errata_tags
        build.save()

    for erratum_id in sorted(new_errata_tags):
        slow_load_errata.delay(erratum_id)
    logger.info(f"Finished refreshing Brew build tags for {build_id}")


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def slow_delete_brew_build(build_id: int, build_state: int) -> int:
    """Delete a Brew build (and its relations) in Corgi when it's deleted in Brew"""
    # TODO: Make this faster, then reenable running this task automatically
    #  Right now this query uses up all temporary storage in our DB, which causes many issues
    #  Code left in place so we can still run manually, but we no longer trigger from UMB
    #  or when trying to reload a deleted build
    # Shipped builds (which have a Brew tag for any product) are never deleted
    # Only unshipped builds not part of any product are deleted
    # For example, builds that failed QE will not become part of a product
    # We don't need this data for any of our use cases
    # Keeping it causes other issues when reloading old builds, scanning for licenses, etc.

    logger.info(f"Deleting Brew build {build_id} with state {build_state}")
    if build_state != koji.BUILD_STATES["DELETED"]:
        raise ValueError(
            f"Invalid state for build {build_id}: "
            f"expected {koji.BUILD_STATES['DELETED']}, received {build_state}"
        )

    deleted_count = 0
    with transaction.atomic():
        # Get a root component's PK, if possible
        # Build might not exist, or might exist but have no root components (?)
        # Should only be 1 result if any
        root_component_qs = (
            SoftwareBuild.objects.filter(build_id=build_id, build_type=SoftwareBuild.Type.BREW)
            .exclude(components__isnull=True)
            .values_list("components", "pk")
        )
        if len(root_component_qs) > 1:
            raise ValueError(
                f"Brew build {build_id} had multiple root components: {root_component_qs}"
            )

        root_component_and_build_pks = root_component_qs.first()
        # Skip deleting child components when build doesn't exist
        if root_component_and_build_pks:
            root_component_pk, build_pk = root_component_and_build_pks
            # The build has a root component, so delete the child components that are
            # provided by / upstreams of only this build's root, and not any other builds
            # Deleting the Component will automatically delete the ComponentNodes
            # TODO: The logic is correct but very inefficient, so only run the task manually
            #  Component.objects.filter(sources__isnull=True, downstreams__isnull=True).delete()
            #  may be slightly better, but this will delete other components
            #  which were accidentally created without a root component (CORGI-617)
            #  We should fix that bug first before making changes here
            provided_components = Component.objects.annotate(
                build_count=Count("sources__software_build_id")
            ).filter(sources=root_component_pk, build_count=1)
            deleted_count += _delete_queryset(provided_components, "provided component")

            # Doing .filter(downstreams=root_component_pk)
            # before .annotate(downstreams_count=Count("downstreams"))
            # makes Django return the wrong results
            # https://docs.djangoproject.com/en/3.2/topics/db/aggregation/
            # #order-of-annotate-and-filter-clauses
            # TODO: The logic is correct but very inefficient, so only run the task manually
            #  Component.objects.filter(sources__isnull=True, downstreams__isnull=True).delete()
            #  may be slightly better, but this will delete other components
            #  which were accidentally created without a root component (CORGI-617)
            #  We should fix that bug first before making changes here
            upstream_components = Component.objects.annotate(
                build_count=Count("downstreams__software_build_id")
            ).filter(downstreams=root_component_pk, build_count=1)
            deleted_count += _delete_queryset(upstream_components, "upstream component")

        # Relations without a linked (NULL) build are "unprocessed"
        # We process these relations each day by loading their build ID
        # Deleting the relation avoids reloading the build we are deleting
        relations = ProductComponentRelation.objects.filter(
            build_id=build_id, build_type=SoftwareBuild.Type.BREW
        )
        deleted_count += _delete_queryset(relations, "relation")

        # Deleting the build automatically deletes the linked root component
        # But not the child components, so we handled those separately above
        builds = SoftwareBuild.objects.filter(build_id=build_id, build_type=SoftwareBuild.Type.BREW)
        deleted_count += _delete_queryset(builds, "build")

    return deleted_count


def _delete_queryset(queryset: QuerySet, model_name: str) -> int:
    """Delete all rows in some queryset, then parse and return the number of deleted objects
    (including both direct and CASCADE / transitive deletions due to ForeignKeys)"""
    # When we call .delete() on a queryset, Django returns a tuple with 2 elements
    # The first element is the total number of models we deleted
    # The second element is a dict. The keys are this model's name, plus all the related model names
    # which have a ForeignKey to the deleted model that defines on_delete=models.CASCADE
    # The values are the number of model instances we deleted either directly or due to a ForeignKey
    # Note that for ManyToManyFields, the counts / related models include entries
    # for the "through tables" that are used to map e.g. many provided components to many sources
    deleted_info: tuple[int, dict[str, int]] = queryset.delete()
    deleted_count, deleted_links = deleted_info
    logger.info(f"Deleted {deleted_count} {model_name}(s) and related models: {deleted_links}")
    return deleted_count
