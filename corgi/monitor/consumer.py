import json
import logging
from typing import Optional

from django.conf import settings
from proton import Event, SSLDomain
from proton.handlers import MessagingHandler
from proton.reactor import Container, Selector

from corgi.tasks.brew import slow_fetch_brew_build, slow_update_brew_tags
from corgi.tasks.pnc import slow_fetch_pnc_sbom

logger = logging.getLogger(__name__)


class UMBReceiverHandler(MessagingHandler):
    """Handler to deal with received messages from UMB."""

    def __init__(self, virtual_topic_addresses: dict[str, str], selector: Optional[str] = None):
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
        self.selector = None if selector is None else Selector(selector)

        # Ack messages manually so that we can ensure we successfully acted upon a message when
        # it was received. See accept condition logic in the on_message() method.
        self.auto_accept = False

        # Each message that is accepted also needs to be settled locally. Auto-settle each
        # message that is accepted automatically (this is the default value).
        self.auto_settle = True

    def on_start(self, event: Event) -> None:
        """Connect to UMB broker(s) and set up a receiver for each virtual topic address"""
        recv_opts = [self.selector] if self.selector is not None else []
        logger.info("Connecting to broker(s): %s", self.urls)
        conn = event.container.connect(urls=self.urls, ssl_domain=self.ssl_domain, heartbeat=500)
        for virtual_topic_address in self.virtual_topic_addresses:
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
        callback_name = self.virtual_topic_addresses.get(address, "")
        callback_function = getattr(self, callback_name, None)

        if not address:
            raise ValueError(f"UMB event {event.message.id} had no address!")
        elif callback_function:
            callback_function(event)
        else:
            raise ValueError(
                f"UMB event {event.message.id} had unrecognized address: {event.message.address}"
            )


class BrewUMBReceiverHandler(UMBReceiverHandler):
    """Handle messages about completed Brew builds, tagged builds, and untagged builds"""

    def handle_builds(self, event: Event) -> None:
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
            # Release message back to the queue but report back that it was delivered. The
            # message will be re-delivered to any available client again.
            self.release(event.delivery, delivered=True)
        else:
            # Accept the delivered message to remove it from the queue.
            self.accept(event.delivery)

    def handle_tags(self, event: Event) -> None:
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
            # Release message back to the queue but report back that it was delivered. The
            # message will be re-delivered to any available client again.
            self.release(event.delivery, delivered=True)
        else:
            # Accept the delivered message to remove it from the queue.
            self.accept(event.delivery)


class UMBListener:
    """Base class that listens for and handles messages on certain UMB topics"""

    handler_class: Optional[MessagingHandler] = None
    virtual_topic_addresses: dict[str, str] = {}
    selector = None

    @classmethod
    def consume(cls):
        """Run a single message handler, which can listen to multiple virtual topic addresses"""
        if not cls.handler_class or not cls.virtual_topic_addresses:
            raise NotImplementedError(
                "Subclass must define handler class and virtual topic address(es)"
            )

        logger.info("Starting consumer for virtual topic(s): %s", cls.virtual_topic_addresses)
        handler = cls.handler_class(virtual_topic_addresses=cls.virtual_topic_addresses)
        Container(handler).run()


class BrewUMBListener(UMBListener):
    """Listen for messages about completed Brew builds, tagged builds, and untagged builds."""

    handler_class = BrewUMBReceiverHandler
    virtual_topic_addresses = {
        f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.complete": "handle_builds",
        f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.tag": "handle_tags",
        f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.untag": "handle_tags",
    }


class SbomerUMBHandler(UMBReceiverHandler):
    """Handle messages about new SBOMs available from PNC"""

    def sbom(self, event: Event) -> None:
        logger.info(f"Handling UMB message for PNC SBOM {event.message.id}")
        message = json.loads(event.message.body)
        try:
            slow_fetch_pnc_sbom(
                message["purl"],
                message["productConfig"]["errataTool"],
                message["build"],
                message["sbom"],
            )
        except Exception as e:
            logger.error(f"Failed to schedule fetch PNC SBOM {event.message.id}: {str(e)}")
            self.release(event.delivery, delivered=True)
        else:
            self.accept(event.delivery)


class SbomerUMBListener(UMBListener):
    """Listen for messages from sbomer about SBOMs from PNC"""

    handler_class = SbomerUMBHandler
    virtual_topic_addresses = {
        f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.": "sbom",
    }
