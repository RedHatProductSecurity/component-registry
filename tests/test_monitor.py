import json
import unittest
from unittest.mock import MagicMock, call, patch

import pytest
from django.conf import settings

from corgi.monitor.consumer import (
    BrewUMBTopicHandler,
    SbomerUMBTopicHandler,
    UMBDispatcher,
    UMBTopicHandler,
)

pytestmark = pytest.mark.unit

VIRTUAL_TOPIC_ADDRESS_PREFIX = f"Consumer.{settings.UMB_CONSUMER}."


class BadHandler(UMBTopicHandler):
    """A test topic handler that intentionally doesn't declare any addresses"""

    def __init__(self):
        self.virtual_topic_addresses = {}


def test_umb_dispatcher_requires_settings():
    """Test UMBDispatcher raises ValueError when handlers register with no topics"""
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()

    bad_handler = BadHandler()
    with pytest.raises(ValueError):
        dispatcher.register_handler(bad_handler)


def test_brew_umb_topic_handler_defines_settings():
    """Test BrewUMBTopicHandler subclass listens for messages on defined address
    and handles them with correct class"""

    handler = BrewUMBTopicHandler()
    assert handler.virtual_topic_addresses == {
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng.brew.build.complete": handler.handle_builds,  # noqa: E501
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng.brew.build.tag": handler.handle_tags,
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng.brew.build.untag": handler.handle_tags,
    }

    # Stub out the real Container class with a mock we can assert on
    with patch("corgi.monitor.consumer.Container") as mock_container_constructor:
        with patch("corgi.monitor.consumer.SSLDomain"):
            dispatcher = UMBDispatcher()
            dispatcher.register_handler(handler)
            dispatcher.consume()

    # We call the Container() constructor with an instance of UMBDispatcher()
    # returned by mock_dispatcher_constructor above so we don't need real UMB certs in tests
    mock_container_constructor.assert_called_once_with(dispatcher)

    # We call run() on the Container() instance returned by mock_container_constructor
    mock_container_instance = mock_container_constructor.return_value
    mock_container_instance.run.assert_called_once_with()


def test_umb_dispatcher_setup():
    """Test that the UMBDispatcher class is set up correctly"""
    handler = BrewUMBTopicHandler()

    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain") as mock_ssl_domain_constructor:
        dispatcher = UMBDispatcher()
        dispatcher.register_handler(handler)

    # We listen (in client mode) to the address passed in, and connect to the UMB URL from settings
    mock_ssl_domain_constructor.assert_called_once_with(mock_ssl_domain_constructor.MODE_CLIENT)
    mock_ssl_domain_instance = mock_ssl_domain_constructor.return_value
    assert dispatcher.dispatch_map == handler.virtual_topic_addresses
    assert dispatcher.urls == [settings.UMB_BROKER_URL]

    # Messages should be accepted manually (only if no exception)
    assert dispatcher.auto_accept is False
    # Accepted messages should be automatically settled (this is the default)
    assert dispatcher.auto_settle is True

    mock_umb_event = MagicMock()
    mock_connection_constructor = mock_umb_event.container.connect
    mock_connection_instance = mock_connection_constructor.return_value
    dispatcher.on_start(mock_umb_event)

    mock_connection_constructor.assert_called_once_with(
        urls=dispatcher.urls, ssl_domain=mock_ssl_domain_instance, heartbeat=500
    )

    # One receiver per virtual topic address should be created
    create_receiver_calls = [
        call(
            mock_connection_instance,
            address,
            name=None,
            options=[dispatcher.selector] if dispatcher.selector else [],
        )
        for address in handler.virtual_topic_addresses
    ]
    assert len(dispatcher.dispatch_map.items()) == 3
    mock_umb_event.container.create_receiver.assert_has_calls(create_receiver_calls)


class TestAddressOverride(unittest.TestCase):
    """Ensure a warning is emitted when handlers override existing addresses"""

    def test_override(self):
        # Overriding addresses should emit a log warning
        handler_a = BadHandler()
        handler_a.virtual_topic_addresses = {
            "umb_topic": None,
        }
        handler_b = BadHandler()
        handler_b.virtual_topic_addresses = {
            "umb_topic": None,
        }

        # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
        with patch("corgi.monitor.consumer.SSLDomain"):
            dispatcher = UMBDispatcher()

        with self.assertLogs("corgi.monitor.consumer", level="WARN") as log:
            dispatcher.register_handler(handler_a)
            dispatcher.register_handler(handler_b)
        self.assertEqual(
            log.output,
            ["WARNING:corgi.monitor.consumer:Overriding handler for addresses: ['umb_topic']"],
        )


def test_umb_dispatcher_():
    """Test that the UMBDispatcher class raises an error
    when a message is missing its address or is for an unknown topic"""
    handler = BrewUMBTopicHandler()

    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()
        dispatcher.register_handler(handler)

    mock_umb_event = MagicMock()
    mock_id = "1"
    mock_umb_event.message.address = "invalid_virtual_topic_address"
    assert mock_umb_event.message.address not in handler.virtual_topic_addresses

    mock_umb_event.message.body = '{"info": {"build_id": MOCK_ID}}'.replace("MOCK_ID", mock_id)
    with pytest.raises(ValueError):
        dispatcher.on_message(mock_umb_event)

    mock_umb_event.message.address = None
    with pytest.raises(ValueError):
        dispatcher.on_message(mock_umb_event)


def test_brew_umb_topic_handler_handles_builds():
    """Test that the BrewUMBTopicHandler class either
    accepts a "build complete" message, when no exception is raised
    OR rejects the message if any exception is raised"""
    handler = BrewUMBTopicHandler()

    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()
        dispatcher.register_handler(handler)

    mock_umb_event = MagicMock()
    mock_id = "1"
    mock_umb_event.message.address = "topic://VirtualTopic.eng.brew.build.complete"
    assert (
        mock_umb_event.message.address.replace("topic://", VIRTUAL_TOPIC_ADDRESS_PREFIX)
        in handler.virtual_topic_addresses
    )

    mock_umb_event.message.body = '{"info": {"build_id": MOCK_ID}}'.replace("MOCK_ID", mock_id)
    mock_id = int(mock_id)

    umb_message_exceptions = (None, Exception("Second message received raises an exception"))
    # side_effect is a list of return values for each call to slow_fetch_brew_build.apply_async()
    # If any side_effect is an Exception subclass, it will be raised
    # Any other side_effect is just returned instead
    with patch(
        "corgi.monitor.consumer.slow_fetch_brew_build.apply_async",
        side_effect=umb_message_exceptions,
    ) as slow_fetch_brew_build_mock:
        # First call raises no exception, message should be accepted
        with patch.object(dispatcher, "accept") as mock_accept:
            dispatcher.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)

        # Second call raises exception given above, message should be rejected
        with patch.object(dispatcher, "release") as mock_release:
            dispatcher.on_message(mock_umb_event)
            mock_release.assert_called_once_with(mock_umb_event.delivery, delivered=True)

    # slow_fetch_brew_build.apply_async is called once per message with a build_id arg
    slow_fetch_brew_build_mock.assert_has_calls((call(args=(mock_id,)), call(args=(mock_id,))))


def test_handle_tag_and_untag_messages():
    """Test that the BrewUMBTopicHandler class either
    accepts tag / untag messages, when no exceptions are raised
    OR rejects tag / untag messages if any exception is raised"""
    handler = BrewUMBTopicHandler()
    _, tag_address, untag_address = handler.virtual_topic_addresses.keys()
    assert VIRTUAL_TOPIC_ADDRESS_PREFIX in tag_address
    assert VIRTUAL_TOPIC_ADDRESS_PREFIX in untag_address
    assert ".tag" in tag_address
    assert ".untag" in untag_address
    tag_address = tag_address.replace(VIRTUAL_TOPIC_ADDRESS_PREFIX, "topic://")
    untag_address = untag_address.replace(VIRTUAL_TOPIC_ADDRESS_PREFIX, "topic://")

    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()
        dispatcher.register_handler(handler)

    mock_umb_event = MagicMock()
    mock_id = "1"
    mock_tag = "RHSA-2023:1234"
    # Both tag and untag events share the same message structure
    mock_umb_event.message.body = '{"build": {"build_id": MOCK_ID}, "tag": {"name": "MOCK_TAG"}}'
    mock_umb_event.message.body = mock_umb_event.message.body.replace("MOCK_ID", mock_id).replace(
        "MOCK_TAG", mock_tag
    )
    mock_id = int(mock_id)

    invalid_message_exception = Exception("Message received raises an exception")
    umb_message_exceptions = (None, None, invalid_message_exception, invalid_message_exception)
    # side_effect is a list of return values for each call to slow_fetch_brew_build.apply_async()
    # If any side_effect is an Exception subclass, it will be raised
    # Any other side_effect is just returned instead
    with patch(
        "corgi.monitor.consumer.slow_update_brew_tags.apply_async",
        side_effect=umb_message_exceptions,
    ) as slow_update_brew_tags_mock:
        with patch.object(dispatcher, "accept") as mock_accept:
            # First tag call raises no exception, message should be accepted
            mock_umb_event.message.address = tag_address
            dispatcher.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)
            mock_accept.reset_mock()

            # First untag call raises no exception, message should be accepted
            mock_umb_event.message.address = untag_address
            dispatcher.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)

        with patch.object(dispatcher, "release") as mock_release:
            # Second tag call raises exception given above, message should be rejected
            mock_umb_event.message.address = tag_address
            dispatcher.on_message(mock_umb_event)
            mock_release.assert_called_once_with(mock_umb_event.delivery, delivered=True)
            mock_release.reset_mock()

            # Second untag call raises exception given above, message should be rejected
            mock_umb_event.message.address = untag_address
            dispatcher.on_message(mock_umb_event)
            mock_release.assert_called_once_with(mock_umb_event.delivery, delivered=True)

    # slow_update_brew_tags.apply_async is called once per message with a build_id arg
    # and either a "tag_added" or "tag_removed" kwarg
    mock_tag_call = call(args=(mock_id,), kwargs={"tag_added": mock_tag})
    mock_untag_call = call(args=(mock_id,), kwargs={"tag_removed": mock_tag})

    # Four messages / calls total, two tag and two untag, two accepted and two rejected
    call_list = [mock_tag_call, mock_untag_call, mock_tag_call, mock_untag_call]
    slow_update_brew_tags_mock.assert_has_calls(call_list)


def test_sbomer_handler():
    """Test that the Sbomer UMB Handler correctly parses UMB messages"""
    with open("tests/data/sbomer/test_sbomer_message.json") as test_file:
        test_data = json.load(test_file)

    event = MagicMock()
    event.message.address = test_data["headers"]["amq6100_originalDestination"]
    event.message.body = json.dumps(test_data["msg"])

    handler = SbomerUMBTopicHandler()

    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()
        dispatcher.register_handler(handler)

    with patch("corgi.monitor.consumer.slow_fetch_pnc_sbom.delay") as fetch_mock:
        dispatcher.on_message(event)

    fetch_mock.assert_called_with(
        test_data["msg"]["purl"],
        test_data["msg"]["productConfig"]["errataTool"],
        test_data["msg"]["build"],
        test_data["msg"]["sbom"],
    )
