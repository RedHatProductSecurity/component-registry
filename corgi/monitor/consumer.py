import json
import logging
from typing import Optional

from django.conf import settings
from proton import Event, SSLDomain
from proton.handlers import MessagingHandler
from proton.reactor import Container, Selector

from corgi.tasks.brew import slow_fetch_brew_build, slow_update_brew_tags

logger = logging.getLogger(__name__)


class UMBReceiverHandler(MessagingHandler):
    """Handler to deal with received messages from UMB."""

    def __init__(self, virtual_topic_address: str, selector: Optional[str] = None):
        super(UMBReceiverHandler, self).__init__()

        # A virtual topic address determined by a specific listener.
        self.virtual_topic_address = virtual_topic_address

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
        recv_opts = [self.selector] if self.selector is not None else []
        logger.info("Connecting to broker(s): %s", self.urls)
        conn = event.container.connect(urls=self.urls, ssl_domain=self.ssl_domain, heartbeat=500)
        event.container.create_receiver(
            conn, self.virtual_topic_address, name=None, options=recv_opts
        )

    def on_message(self, event: Event) -> None:
        raise NotImplementedError("Message handling logic is topic-specific")


class BrewBuildUMBReceiverHandler(UMBReceiverHandler):
    """Handle messages about completed Brew builds"""

    def on_message(self, event: Event) -> None:
        logger.info("Received UMB event on %s: %s", self.virtual_topic_address, event.message.id)
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


class BrewTagUMBReceiverHandler(UMBReceiverHandler):
    """Handle messages about Brew builds that have tags added or removed"""

    def on_message(self, event: Event) -> None:
        logger.info("Received UMB event on %s: %s", self.virtual_topic_address, event.message.id)
        message = json.loads(event.message.body)
        build_id = message["build"]["build_id"]

        tag_added_or_removed = message["tag"]["name"]
        if self.virtual_topic_address.endswith(".tag"):
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
    handler_class: Optional[MessagingHandler] = None
    virtual_topic_address = ""
    selector = None

    @classmethod
    def consume(cls):
        if not cls.handler_class or not cls.virtual_topic_address:
            raise NotImplementedError(
                "Subclass must define handler class and virtual topic address"
            )

        logger.info("Starting consumer for virtual topic: %s", cls.virtual_topic_address)
        handler = cls.handler_class(virtual_topic_address=cls.virtual_topic_address)
        Container(handler).run()


class BrewBuildUMBListener(UMBListener):
    """Listen for messages about completed Brew builds."""

    handler_class = BrewBuildUMBReceiverHandler
    virtual_topic_address = f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.complete"


class BrewTagUMBListener(UMBListener):
    """Listen for messages about Brew builds that have tags added"""

    handler_class = BrewTagUMBReceiverHandler
    virtual_topic_address = f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.tag"


class BrewUntagUMBListener(UMBListener):
    """Listen for messages about Brew builds that have tags removed"""

    handler_class = BrewTagUMBReceiverHandler
    virtual_topic_address = f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.untag"
