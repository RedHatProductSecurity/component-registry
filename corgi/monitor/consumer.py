import json
import logging
from collections.abc import Callable
from typing import Optional, Protocol

from django.conf import settings
from proton import Event, SSLDomain
from proton.handlers import MessagingHandler
from proton.reactor import Container

from corgi.tasks.brew import slow_fetch_brew_build, slow_update_brew_tags
from corgi.tasks.pnc import slow_fetch_pnc_sbom

logger = logging.getLogger(__name__)


class UMBTopicHandler(Protocol):
    """Defines a list of topics to handle and the methods that handle them"""

    virtual_topic_addresses: dict[str, Callable]


class UMBDispatcher(MessagingHandler):
    """Handler to deal with received messages from UMB."""

    def __init__(self, selector: Optional[str] = None):
        """Set up a handler that listens to many topics and processes messages from each"""
        super(UMBDispatcher, self).__init__()

        # A list of handlers, each of which defines which topics they're interested in and which
        # methods should be invoked to handle those topics
        self.handlers: list[UMBTopicHandler] = []

        # A map of all handled topics to methods
        self.dispatch_map: dict[str, Callable] = {}

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
        # TODO: Research combining selector strings to create a union of all registered selectors
        self.selector = None

        # Ack messages manually so that we can ensure we successfully acted upon a message when
        # it was received. See accept condition logic in the on_message() method.
        self.auto_accept = False

        # Each message that is accepted also needs to be settled locally. Auto-settle each
        # message that is accepted automatically (this is the default value).
        self.auto_settle = True

    def register_handler(self, handler: UMBTopicHandler) -> None:
        """Register a class which declares which topics it handles, via which methods."""
        if not handler.virtual_topic_addresses:
            raise ValueError("Handlers must define virtual topic address(es)")

        if any(
            a in list(self.dispatch_map.keys())
            for a in list(handler.virtual_topic_addresses.keys())
        ):
            overrides = [
                addr for addr in self.dispatch_map.keys() if addr in handler.virtual_topic_addresses
            ]
            logger.warning(f"Overriding handler for addresses: {overrides}")

        self.handlers.append(handler)
        self.dispatch_map.update(handler.virtual_topic_addresses)

    def on_start(self, event: Event) -> None:
        """Connect to UMB broker(s) and set up a receiver for each virtual topic address"""
        recv_opts: list[Optional[str]] = [self.selector] if self.selector is not None else []
        logger.info("Connecting to broker(s): %s", self.urls)
        conn = event.container.connect(urls=self.urls, ssl_domain=self.ssl_domain, heartbeat=500)
        for virtual_topic_address in self.dispatch_map.keys():
            event.container.create_receiver(
                conn, virtual_topic_address, name=None, options=recv_opts
            )

    def on_message(self, event: Event) -> None:
        """Route message to a handler function, based on the virtual topic it was received on"""
        logger.info("Received UMB event on %s: %s", event.message.address, event.message.id)
        # Convert general topic address into specific address we listen on
        address = event.message.address or ""
        address = address.replace("topic://", f"Consumer.{settings.UMB_CONSUMER}.")

        callback_function = self.dispatch_map.get(address, None)

        if not address:
            raise ValueError(f"UMB event {event.message.id} had no address!")
        elif callback_function:
            accepted = callback_function(event)
            if accepted:
                # Accept the delivered message to remove it from the queue.
                self.accept(event.delivery)
            else:
                # Release message back to the queue but report back that it was delivered. The
                # message will be re-delivered to any available client again.
                self.release(event.delivery, delivered=True)
        else:
            raise ValueError(
                f"UMB event {event.message.id} had unrecognized address: {event.message.address}"
            )

    def consume(self):
        logger.info("Starting consumer for virtual topic(s): %s", self.dispatch_map.keys())
        Container(self).run()


class BrewUMBTopicHandler(UMBTopicHandler):
    """Handle messages about completed Brew builds, tagged builds, and untagged builds"""

    def __init__(self):
        self.virtual_topic_addresses = {
            f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.complete": self.handle_builds,  # noqa: E501
            f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.tag": self.handle_tags,
            f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.untag": self.handle_tags,
        }

    def handle_builds(self, event: Event) -> bool:
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

    def handle_tags(self, event: Event) -> bool:
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


class SbomerUMBTopicHandler(UMBTopicHandler):
    """Handle messages about new SBOMs available from PNC"""

    def __init__(self):
        self.virtual_topic_addresses = {
            f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.pnc.sbom.complete": self.sbom_complete,  # noqa: E501
        }

    def sbom_complete(self, event: Event) -> bool:
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
            logger.error(f"Failed to schedule fetch PNC SBOM {event.message.id}: {str(e)}")
            return False
        else:
            return True
