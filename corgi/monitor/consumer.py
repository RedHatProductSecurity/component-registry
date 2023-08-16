import json
import logging
from collections.abc import Callable

from django.conf import settings
from proton import Event, SSLDomain
from proton.handlers import MessagingHandler
from proton.reactor import Container, Selector

from corgi.collectors.pnc import is_sbomer_product
from corgi.tasks.brew import slow_fetch_brew_build, slow_update_brew_tags
from corgi.tasks.errata_tool import slow_handle_shipped_errata
from corgi.tasks.pnc import slow_fetch_pnc_sbom, slow_handle_pnc_errata_released
from corgi.tasks.pyxis import slow_fetch_pyxis_manifest

logger = logging.getLogger(__name__)

# A method which receives an event, and returns true if the message was accepted,
# or false if it should be released back into the queue.
HandleMethod = Callable[[Event], bool]


class UMBReceiverHandler(MessagingHandler):
    """Handler to deal with received messages from UMB."""

    def __init__(self, virtual_topic_addresses: dict[str, HandleMethod], selectors: dict[str, str]):
        """Set up a handler that listens to many topics and processes messages from each"""
        super(UMBReceiverHandler, self).__init__()

        # A mapping of virtual topic addresses to functions that handle topic messages
        # as determined by a specific listener.
        self.virtual_topic_addresses = virtual_topic_addresses

        # Set of URLs where UMB brokers are running. Use AMQP protocol port numbers only. See
        # specific URLs in settings; use env vars to select the appropriate broker.
        self.urls = [settings.UMB_BROKER_URL]

        # The UMB cert and key are generated for an LDAP user (developer) or service account
        # (this app). The CA cert is the standard internal root CA cert installed in the Dockerfile.
        # To request a new set of UMB certs, see docs/developer.md or docs/operations.md.
        self.ssl_domain = SSLDomain(SSLDomain.MODE_CLIENT)
        self.ssl_domain.set_credentials(
            cert_file=settings.UMB_CERT, key_file=settings.UMB_KEY, password=None
        )
        self.ssl_domain.set_trusted_ca_db(settings.CA_CERT)
        self.ssl_domain.set_peer_authentication(SSLDomain.VERIFY_PEER)

        # A set of filters used to narrow down the received messages from UMB; see individual
        # listeners to see if they define any selectors or consume all messages without any
        # filtering.
        self.selectors = selectors

        # Ack messages manually so that we can ensure we successfully acted upon a message when
        # it was received. See accept condition logic in the on_message() method.
        self.auto_accept = False

        # Each message that is accepted also needs to be settled locally. Auto-settle each
        # message that is accepted automatically (this is the default value).
        self.auto_settle = True

    def on_start(self, event: Event) -> None:
        """Connect to UMB broker(s) and set up a receiver for each virtual topic address"""
        logger.info("Connecting to broker(s): %s", self.urls)
        conn = event.container.connect(urls=self.urls, ssl_domain=self.ssl_domain, heartbeat=500)
        for virtual_topic_address in self.virtual_topic_addresses:
            topic_selector = self.selectors.get(virtual_topic_address, "")
            recv_opts = [Selector(topic_selector)] if topic_selector else []
            event.container.create_receiver(
                conn, virtual_topic_address, name=None, options=recv_opts
            )

    def on_message(self, event: Event) -> None:
        """Route message to a handler function, based on the virtual topic it was received on"""
        logger.info("Received UMB event on %s: %s", event.message.address, event.message.id)
        # Convert general topic address into specific address we listen on
        address = event.message.address or ""
        address = address.replace("topic://", f"Consumer.{settings.UMB_CONSUMER}.")

        # Turn function name (str) into callable so we can pass event to it below
        # We don't pass the callable itself because it needs a self arg
        callback = self.virtual_topic_addresses.get(address)

        if not address:
            raise ValueError(f"UMB event {event.message.id} had no address!")
        elif callback:
            handled = callback(event)
        else:
            raise ValueError(
                f"UMB event {event.message.id} had unrecognized address: {event.message.address}"
            )

        if handled:
            # Accept the delivered message to remove it from the queue.
            self.accept(event.delivery)
        else:
            # Release message back to the queue but report back that it was delivered. The
            # message will be re-delivered to any available client again.
            self.release(event.delivery, delivered=True)

    ##########################
    # Message Handlers: Brew #
    ##########################
    @staticmethod
    def brew_builds(event: Event) -> bool:
        """Handle messages about completed Brew builds"""
        logger.info("Handling UMB event for completed builds: %s", event.message.id)
        message = json.loads(event.message.body)
        build_id = message["info"]["build_id"]

        try:
            slow_fetch_brew_build.apply_async(args=(build_id,))
        except Exception as exc:
            logger.error(
                "Failed to schedule slow_fetch_brew_build task for build ID %s: %s",
                build_id,
                str(exc),
            )
            return False
        else:
            return True

    @staticmethod
    def brew_tags(event: Event) -> bool:
        """Handle messages about Brew builds that have tags added or removed"""
        logger.info("Handling UMB event for added or removed tags: %s", event.message.id)
        message = json.loads(event.message.body)
        build_id = message["build"]["build_id"]

        tag_added_or_removed = message["tag"]["name"]
        if event.message.address.endswith(".tag"):
            kwargs = {"tag_added": tag_added_or_removed}
        else:
            kwargs = {"tag_removed": tag_added_or_removed}

        try:
            slow_update_brew_tags.apply_async(args=(build_id,), kwargs=kwargs)
        except Exception as exc:
            logger.error(
                "Failed to schedule slow_update_brew_tags task for build ID %s: %s",
                build_id,
                str(exc),
            )
            return False
        else:
            return True

    ########################
    # Message Handlers: ET #
    ########################
    @staticmethod
    def et_shipped_errata(event: Event) -> bool:
        """Handle messages about ET advisories that enter the SHIPPED_LIVE state"""
        logger.info("Handling UMB event for shipped erratum: %s", event.message.id)
        message = json.loads(event.message.body)
        errata_id = message["errata_id"]
        errata_status = message["errata_status"]
        errata_product = message["product"]
        errata_release = message["release"]
        if errata_status != "SHIPPED_LIVE":
            # Don't raise an error here because it will kill the whole listener
            logger.error(
                f"Received event with wrong status for erratum {errata_id}: {errata_status}"
            )

        try:
            # If an erratum has the wrong status, we'll raise an error in the task
            # Errata for PNC/SBOMer products won't have attached artifacts, so handle
            # those separately
            if is_sbomer_product(errata_product, errata_release):
                slow_handle_pnc_errata_released.apply_async(args=(errata_id, errata_status))
            else:
                slow_handle_shipped_errata.apply_async(args=(errata_id, errata_status))
        except Exception as exc:
            logger.error(
                "Failed to schedule slow_handle_shipped_errata task for erratum ID %s: %s",
                errata_id,
                str(exc),
            )
            return False
        else:
            return True

    ############################
    # Message Handlers: SBOMer #
    ############################
    @staticmethod
    def sbomer_complete(event: Event) -> bool:
        logger.info(f"Handling UMB message for PNC SBOM {event.message.id}")
        message = json.loads(event.message.body)
        try:
            slow_fetch_pnc_sbom.delay(
                message["purl"],
                message["productConfig"]["errataTool"],
                message["build"],
                message["sbom"],
            )
        except Exception as e:
            logger.error(f"Failed to schedule PNC SBOM fetch {event.message.id}: {str(e)}")
            return False
        else:
            return True

    ############################
    # Message Handlers: pyxis  #
    ############################
    @staticmethod
    def pyxis_manifest_create(event: Event) -> bool:
        logger.info(f"Handling UMB message for pyxis manifest {event.message.id}")
        message = json.loads(event.message.body)
        try:
            slow_fetch_pyxis_manifest.delay(
                message["entityData"]["_id"]["$oid"],
            )
        except Exception as e:
            logger.error(f"Failed to schedule pyxis manifest fetch {event.message.id}: {str(e)}")
            return False
        else:
            return True


class UMBListener:
    """Base class that listens for and handles messages on certain UMB topics"""

    VIRTUAL_TOPIC_PREFIX = f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng"
    virtual_topic_addresses = {
        # Brew Addresses
        f"{VIRTUAL_TOPIC_PREFIX}.brew.build.complete": UMBReceiverHandler.brew_builds,
        f"{VIRTUAL_TOPIC_PREFIX}.brew.build.tag": UMBReceiverHandler.brew_tags,
        f"{VIRTUAL_TOPIC_PREFIX}.brew.build.untag": UMBReceiverHandler.brew_tags,
        # ET Addresses
        f"{VIRTUAL_TOPIC_PREFIX}.errata.activity.status": UMBReceiverHandler.et_shipped_errata,
        # SBOMer Addresses
        f"{VIRTUAL_TOPIC_PREFIX}.pnc.sbom.spike.complete": UMBReceiverHandler.sbomer_complete,
        # Pyxis Manifests
        f"{VIRTUAL_TOPIC_PREFIX}.snitch.contentmanifest.create": (
            UMBReceiverHandler.pyxis_manifest_create
        ),
    }
    # By default, listen for all messages on a topic
    selectors = {key: "" for key in virtual_topic_addresses}
    # ET Selectors
    # Only listen for messages about SHIPPED_LIVE errata
    selectors[f"{VIRTUAL_TOPIC_PREFIX}.errata.activity.status"] = "errata_status = 'SHIPPED_LIVE'"

    @classmethod
    def consume(cls):
        """Run a single message handler, which can listen to multiple virtual topic addresses"""
        logger.info("Starting consumer for virtual topic(s): %s", cls.virtual_topic_addresses)
        Container(
            UMBReceiverHandler(
                virtual_topic_addresses=cls.virtual_topic_addresses,
                selectors=cls.selectors,
            )
        ).run()
