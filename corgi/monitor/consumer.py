import json
import logging
from collections.abc import Callable

from django.conf import settings
from proton import Event, SSLDomain
from proton.handlers import MessagingHandler
from proton.reactor import Container, Selector

from corgi.tasks.brew import slow_fetch_brew_build, slow_update_brew_tags
from corgi.tasks.errata_tool import slow_handle_shipped_errata

logger = logging.getLogger(__name__)

# A method which receives an event, and returns true if the message was accepted,
# or false if it should be released back into the queue.
HandleMethod = Callable[[Event], bool]

VIRTUAL_TOPIC_PREFIX = f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng"


class UMBHandler:
    """Handler to deal with received messages from UMB. Defines topics to listen to and
    methods to invoke for handle messages in those topics."""

    def __init__(self, addresses: dict[str, HandleMethod], selectors: dict[str, str]):
        if not addresses:
            raise ValueError("UMBHandler has no addresses")
        if not all(callable(handler) for handler in addresses.values()):
            raise ValueError("UMBHandler is missing handler function(s)")
        # A mapping of virtual topic addresses to functions that handle topic messages
        # as determined by a specific listener.
        self.virtual_topic_addresses = addresses

        # A set of filters used to narrow down the received messages from UMB for this handler, if
        # none, handle all messages
        self.selectors = selectors


class BrewUMBHandler(UMBHandler):
    """Handle messages about completed Brew builds, tagged builds, and untagged builds, listen
    for messages about shipped ET advisories, only to update released tags on Brew builds."""

    def __init__(self):
        addresses = {
            f"{VIRTUAL_TOPIC_PREFIX}.brew.build.complete": self.handle_builds,
            f"{VIRTUAL_TOPIC_PREFIX}.brew.build.tag": self.handle_tags,
            f"{VIRTUAL_TOPIC_PREFIX}.brew.build.untag": self.handle_tags,
            f"{VIRTUAL_TOPIC_PREFIX}.errata.activity.status": self.handle_shipped_errata,
        }
        # By default, listen for all messages on a topic
        selectors = {key: "" for key in addresses}
        # Only listen for messages about SHIPPED_LIVE errata
        selectors[
            f"{VIRTUAL_TOPIC_PREFIX}.errata.activity.status"
        ] = "errata_status = 'SHIPPED_LIVE'"

        super(BrewUMBHandler, self).__init__(addresses=addresses, selectors=selectors)

    @staticmethod
    def handle_builds(event: Event) -> bool:
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
    def handle_shipped_errata(event: Event) -> bool:
        """Handle messages about ET advisories that enter the SHIPPED_LIVE state"""
        logger.info("Handling UMB event for shipped erratum: %s", event.message.id)
        message = json.loads(event.message.body)
        errata_id = message["errata_id"]
        errata_status = message["errata_status"]
        if errata_status != "SHIPPED_LIVE":
            # Don't raise an error here because it will kill the whole listener
            logger.error(
                f"Received event with wrong status for erratum {errata_id}: {errata_status}"
            )

        try:
            # If an erratum has the wrong status, we'll raise an error in the task
            slow_handle_shipped_errata.apply_async(args=(errata_id, errata_status))
        except Exception as exc:
            logger.error(
                "Failed to schedule slow_update_brew_tags task for build ID %s: %s",
                errata_id,
                str(exc),
            )
            return False
        else:
            return True

    @staticmethod
    def handle_tags(event: Event) -> bool:
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


class UMBDispatcher(MessagingHandler):
    """Maintains a collection of UMBHandlers and dispatches messages to them"""

    def __init__(self):
        """Set up a handler that listens to many topics and processes messages from each"""
        super(UMBDispatcher, self).__init__()

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

        # Ack messages manually so that we can ensure we successfully acted upon a message when
        # it was received. See accept condition logic in the on_message() method.
        self.auto_accept = False

        # Each message that is accepted also needs to be settled locally. Auto-settle each
        # message that is accepted automatically (this is the default value).
        self.auto_settle = True

        # A list of UMBHandlers to which messages will be dispatched. If you add new handlers,
        # make sure they're added here.
        self.handlers: list[UMBHandler] = [BrewUMBHandler()]

    @property
    def virtual_topic_addresses(self) -> dict[str, HandleMethod]:
        return {
            addr: handler.virtual_topic_addresses[addr]
            for handler in self.handlers
            for addr in handler.virtual_topic_addresses
        }

    @property
    def selectors(self) -> dict[str, str]:
        return {
            addr: handler.selectors[addr] for handler in self.handlers for addr in handler.selectors
        }

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

        # Look up the function registered for this address
        callback = self.virtual_topic_addresses.get(address)

        if not address:
            raise ValueError(f"UMB event {event.message.id} had no address!")
        elif callback:
            accepted = callback(event)
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
        """Run a single message handler, which can listen to multiple virtual topic addresses"""
        logger.info("Starting consumer for virtual topic(s): %s", self.virtual_topic_addresses)
        Container(self).run()
