import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Generator, Union

from django.db.models import Manager, QuerySet
from spdx_tools.spdx.jsonschema.document_converter import DocumentConverter
from spdx_tools.spdx.model import (
    Actor,
    ActorType,
    CreationInfo,
    Document,
    ExternalPackageRef,
    ExternalPackageRefCategory,
    Package,
    Relationship,
    RelationshipType,
    Version,
)
from spdx_tools.spdx.model.spdx_no_assertion import SpdxNoAssertion
# from spdx_tools.spdx.validation.document_validator import validate_full_spdx_document
# from spdx_tools.spdx.validation.validation_message import ValidationMessage

from corgi.core.models import Component, ComponentNode, ProductStream

logger = logging.getLogger(__name__)


class ManifestFile(ABC):
    """A data file that represents a generic manifest in machine-readable SPDX / JSON format."""

    SPDX_VERSION = "SPDX-2.3"
    REF_PREFIX = "SPDXRef-"
    DOCUMENT_REF = f"{REF_PREFIX}DOCUMENT"
    DOCUMENT_UUID = f"{REF_PREFIX}{uuid.uuid4()}"
    # TODO verify this path
    DOCUMENT_NAMESPACE_URL = "https://access.redhat.com/security/data/sbom/spdx/"
    CREATED_BY = Actor(ActorType.ORGANIZATION, "Red Hat Product Security", "secalert@redhat.com")
    PACKAGE_SUPPLIER = Actor(ActorType.ORGANIZATION, "Red Hat")
    LICENSE_LIST_VERSION = Version(3, 8)
    DATA_LICENSE = "CC0-1.0"
    PACKAGE_FIELDS = [
        "copyright_text",
        "purl",
        "related_url",
        "license_concluded_raw",
        "license_declared_raw",
        "name",
        "filename",
        "epoch",
        "version",
        "release",
        "type",
    ]

    @staticmethod
    def version_info(epoch: int, version: str, release: str) -> str:
        epoch_part = f"{epoch}-" if epoch else ""
        # Many GOLANG components don't have a version or release set
        # so don't return an oddly formatted NEVRA, like f"{name}-.{arch}"
        release_part = f"-{release}" if release else ""

        return f"{epoch_part}{version}{release_part}"

    @staticmethod
    def no_assert_if_empty(value):
        if value:
            return value
        else:
            return SpdxNoAssertion()

    def get_document_namespace(self, document_name: str) -> str:
        return f"{self.DOCUMENT_NAMESPACE_URL}{document_name}"

    @abstractmethod
    def render_content(self, created_at: datetime, document_uuid: str = "") -> dict:
        pass

    @staticmethod
    def build_external_cpe_references(
        cpes: Union[tuple[str, ...], QuerySet]
    ) -> Generator[ExternalPackageRef, None, None]:
        for cpe in cpes:
            yield ExternalPackageRef(
                category=ExternalPackageRefCategory.SECURITY,
                reference_type="cpe22Type",
                locator=cpe,
            )

    def get_relationships_for_component(self, component) -> Generator[Relationship, None, None]:
        for node_purl, node_type, node_id in component.get_provides_nodes_queryset():
            relationship_type = (
                RelationshipType.CONTAINED_BY
                if node_type == ComponentNode.ComponentNodeType.PROVIDES
                else RelationshipType.DEV_DEPENDENCY_OF
            )
            yield Relationship(
                f"{self.REF_PREFIX}{node_id}", relationship_type, f"{self.REF_PREFIX}{component.pk}"
            )
        # upstream component relationships
        # RPM upstream data is human-generated and unreliable
        if component.type != Component.Type.RPM:
            for node_id in component.get_upstreams_pks():
                yield Relationship(
                    f"{self.REF_PREFIX}{node_id}",
                    RelationshipType.GENERATES,
                    f"{self.REF_PREFIX}{component.pk}",
                )

    def build_creation_info(self, created_at, document_name, document_namespace):
        creation_info = CreationInfo(
            spdx_id=self.DOCUMENT_REF,
            spdx_version=self.SPDX_VERSION,
            name=document_name,
            created=created_at,
            document_namespace=document_namespace,
            creators=[self.CREATED_BY],
            data_license=self.DATA_LICENSE,
            license_list_version=self.LICENSE_LIST_VERSION,
        )
        return creation_info

    def build_package(
        self, component: Component, include_cpes: bool = False, package_id: str = ""
    ) -> Package:
        external_references = [
            ExternalPackageRef(
                category=ExternalPackageRefCategory.PACKAGE_MANAGER,
                reference_type="purl",
                locator=component.purl,
            )
        ]

        # This requires another lookup to the database to fetch component.productvariants relations
        if include_cpes:
            external_references.extend(self.build_external_cpe_references(component.cpes))

        # RPM related_url is unreliable
        non_rpm_related_url = None
        if component.related_url and component.type != Component.Type.RPM:
            non_rpm_related_url = component.related_url

        version_info = self.version_info(component.epoch, component.version, component.release)
        file_name = component.filename if component.filename else None
        if not package_id:
            package_id = f"{self.REF_PREFIX}{component.pk}"
        return Package(
            copyright_text=self.no_assert_if_empty(component.copyright_text),
            download_location=self.no_assert_if_empty(component.download_url),
            external_references=external_references,
            files_analyzed=False,
            homepage=non_rpm_related_url,
            # license_concluded=self.no_assert_if_empty(component.license_concluded),
            # license_declared=self.no_assert_if_empty(component.license_declared),
            name=component.name,
            originator=SpdxNoAssertion(),
            file_name=file_name,
            spdx_id=package_id,
            supplier=self.PACKAGE_SUPPLIER,
            version=version_info,
        )

    def packages_generator(
        self, manager: Union[Manager["Component"], QuerySet["Component"]]
    ) -> Generator[Package, None, None]:
        for component in manager.only(*self.PACKAGE_FIELDS).iterator():
            yield self.build_package(component)


class ComponentManifestFile(ManifestFile):
    """A data file that represents a component manifest in machine-readable SPDX / JSON format."""

    def __init__(self, component: Component) -> None:
        self.component = component
        self.document_uuid = f"{self.REF_PREFIX}{component.pk}"

    def render_content(
        self, created_at: datetime = datetime.now(), document_uuid: str = ""
    ) -> dict:

        document_name = f"{self.component.name.replace('/', '_')}-{self.component.version}"
        document_namespace = self.get_document_namespace(document_name)

        creation_info = self.build_creation_info(created_at, document_name, document_namespace)
        document: Document = Document(creation_info)

        # Build packages
        packages: list[Package] = []
        packages.extend(self.packages_generator(self.component.provides.db_manager("read_only")))
        # RPM upstream data is human-generated and unreliable
        if self.component.type != Component.Type.RPM:
            packages.extend(
                self.packages_generator(self.component.upstreams.db_manager("read_only"))
            )
        if not document_uuid:
            document_uuid = f"{self.REF_PREFIX}{self.component.pk}"
        packages.append(self.build_package(self.component, True, package_id=document_uuid))
        document.packages = packages

        # Build relationships
        # provided component relationships
        relationships: list[Relationship] = []
        relationships.extend(self.get_relationships_for_component(self.component))
        relationships.append(
            Relationship(self.DOCUMENT_REF, RelationshipType.DESCRIBES, self.document_uuid)
        )
        document.relationships = relationships

        # validation_messages: list[ValidationMessage] = validate_full_spdx_document(
        #     document, spdx_version=self.SPDX_VERSION
        # )

        # for message in validation_messages:
        #     logging.error(message.context)
        #     raise ValueError(
        #         f"SPDX validation failed for component {document_name}: "
        #         f"{message.validation_message}"
        #     )

        return DocumentConverter().convert(document)


class ProductManifestFile(ManifestFile):
    """A data file that represents a product manifest in machine-readable SPDX / JSON format."""

    license_comment = (
        "Licensing information is provided for individual components only at this time."
    )

    def __init__(self, stream: ProductStream) -> None:
        self.stream = stream

    def render_content(
        self, created_at: datetime = datetime.now(), document_uuid: str = ""
    ) -> dict:

        document_name = self.stream.external_name
        document_namespace = self.get_document_namespace(document_name)
        if not document_uuid:
            document_uuid = self.DOCUMENT_UUID

        creation_info = self.build_creation_info(created_at, document_name, document_namespace)
        document: Document = Document(creation_info)

        # Build packages and relationships
        packages: list[Package] = []
        relationships: list[Relationship] = []

        # add a package and a relationship for each root component and it's provided and upstream
        # dependencies
        for root_component in self.stream.components.manifest_components(
            ofuri=self.stream.ofuri
        ).only(*self.PACKAGE_FIELDS):
            packages.append(self.build_package(root_component, include_cpes=True))
            relationships.extend(self.get_relationships_for_component(root_component))
            root_package_of_document_relationship = Relationship(
                f"{self.REF_PREFIX}{root_component.pk}", RelationshipType.PACKAGE_OF, document_uuid
            )
            relationships.append(root_package_of_document_relationship)

        # add a package for each root component's upstream and provided dependencies
        packages.extend(self.packages_generator(self.stream.upstreams_queryset))
        packages.extend(self.packages_generator(self.stream.provides_queryset))

        # add a package for the stream
        document_package = self.build_document_package(document_name, document_uuid)
        packages.append(document_package)

        # add a relationship for the stream to the document
        relationships.append(
            Relationship(self.DOCUMENT_REF, RelationshipType.DESCRIBES, document_uuid)
        )

        document.packages = packages
        document.relationships = relationships

        return DocumentConverter().convert(document)

    def build_document_package(self, document_name: str, document_uuid: str) -> Package:
        external_references = self.build_external_cpe_references(self.stream.cpes)
        homepage = (
            self.stream.lifecycle_url if self.stream.lifecycle_url else "https://www.redhat.com/"
        )
        if not document_uuid:
            document_uuid = self.DOCUMENT_UUID
        document_package = Package(
            copyright_text=SpdxNoAssertion(),
            download_location=SpdxNoAssertion(),
            external_references=list(external_references),
            files_analyzed=False,
            homepage=homepage,
            license_comment=self.license_comment,
            license_concluded=SpdxNoAssertion(),
            license_declared=SpdxNoAssertion(),
            name=document_name,
            spdx_id=document_uuid,
            supplier=self.PACKAGE_SUPPLIER,
            version=self.stream.version,
        )
        return document_package
