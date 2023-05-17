from unittest.mock import MagicMock, call, patch

import pytest
from django.conf import settings

from corgi.monitor.consumer import BrewUMBListener, BrewUMBReceiverHandler, UMBListener

pytestmark = pytest.mark.unit

VIRTUAL_TOPIC_ADDRESS_PREFIX = f"Consumer.{settings.UMB_CONSUMER}."


def test_umb_listener_requires_settings():
    """Test UMBListener base class raises NotImplementedError
    for missing address or missing handler class"""
    listener = UMBListener()
    with patch.object(listener, "handler_class", BrewUMBReceiverHandler):
        assert listener.virtual_topic_addresses == {}
        with pytest.raises(NotImplementedError):
            listener.consume()

    with patch.object(listener, "virtual_topic_addresses", {"1": "1"}):
        assert listener.handler_class is None
        with pytest.raises(NotImplementedError):
            listener.consume()


def test_brew_umb_listener_defines_settings():
    """Test BrewUMBListener subclass listens for messages on defined address
    and handles them with correct class"""
    listener = BrewUMBListener()
    assert listener.virtual_topic_addresses == {
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng.brew.build.complete": "handle_builds",
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng.brew.build.tag": "handle_tags",
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng.brew.build.untag": "handle_tags",
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng."
        f"errata.activity.status": "handle_shipped_errata",
    }
    assert listener.handler_class == BrewUMBReceiverHandler

    # Stub out the real Container class with a mock we can assert on
    with patch("corgi.monitor.consumer.Container") as mock_container_constructor:
        # Stub out a different class that we don't want to test here
        with patch.object(BrewUMBListener, "handler_class") as mock_receiver_constructor:
            listener.consume()

    # We call the BrewUMBReceiverHandler() constructor with the class's virtual topic addresses
    # and a dict of selectors for each address
    mock_receiver_constructor.assert_called_once_with(
        virtual_topic_addresses=listener.virtual_topic_addresses, selectors=listener.selectors
    )

    # We call the Container() constructor with an instance of BrewUMBReceiverHandler()
    # returned by mock_receiver_constructor above so we don't need real UMB certs in tests
    mock_receiver_instance = mock_receiver_constructor.return_value
    mock_container_constructor.assert_called_once_with(mock_receiver_instance)

    # We call run() on the Container() instance returned by mock_container_constructor
    mock_container_instance = mock_container_constructor.return_value
    mock_container_instance.run.assert_called_once_with()


def test_brew_umb_receiver_setup():
    """Test that the BrewUMBReceiverHandler class is set up correctly"""
    listener = BrewUMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain") as mock_ssl_domain_constructor:
        handler = BrewUMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

    # We listen (in client mode) to the address passed in, and connect to the UMB URL from settings
    mock_ssl_domain_constructor.assert_called_once_with(mock_ssl_domain_constructor.MODE_CLIENT)
    mock_ssl_domain_instance = mock_ssl_domain_constructor.return_value
    assert handler.virtual_topic_addresses == addresses
    assert handler.selectors == selectors
    assert handler.urls == [settings.UMB_BROKER_URL]

    # Messages should be accepted manually (only if no exception)
    assert handler.auto_accept is False
    # Accepted messages should be automatically settled (this is the default)
    assert handler.auto_settle is True

    mock_umb_event = MagicMock()
    mock_connection_constructor = mock_umb_event.container.connect
    mock_connection_instance = mock_connection_constructor.return_value
    with patch("corgi.monitor.consumer.Selector") as mock_selector:
        handler.on_start(mock_umb_event)
        mock_selector.assert_called_once_with("errata_status = 'SHIPPED_LIVE'")
        shipped_errata_selector = mock_selector.return_value

    mock_connection_constructor.assert_called_once_with(
        urls=handler.urls, ssl_domain=mock_ssl_domain_instance, heartbeat=500
    )

    # One receiver per virtual topic address should be created
    create_receiver_calls = [
        call(
            mock_connection_instance,
            address,
            name=None,
            options=[shipped_errata_selector] if handler.selectors.get(address) else [],
        )
        for address in handler.virtual_topic_addresses
    ]
    assert len(handler.virtual_topic_addresses) == 4
    mock_umb_event.container.create_receiver.assert_has_calls(create_receiver_calls)


def test_brew_umb_receiver_():
    """Test that the BrewUMBReceiverHandler class raises an error
    when a message is missing its address or is for an unknown topic"""
    listener = BrewUMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        handler = BrewUMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

    mock_umb_event = MagicMock()
    mock_id = "1"
    mock_umb_event.message.address = "invalid_virtual_topic_address"
    assert mock_umb_event.message.address not in addresses

    mock_umb_event.message.body = '{"info": {"build_id": MOCK_ID}}'.replace("MOCK_ID", mock_id)
    with pytest.raises(ValueError):
        handler.on_message(mock_umb_event)

    mock_umb_event.message.address = None
    with pytest.raises(ValueError):
        handler.on_message(mock_umb_event)


def test_brew_umb_receiver_handles_builds():
    """Test that the BrewUMBReceiverHandler class either
    accepts a "build complete" message, when no exception is raised
    OR rejects the message if any exception is raised"""
    listener = BrewUMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        handler = BrewUMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

    mock_umb_event = MagicMock()
    mock_id = "1"
    mock_umb_event.message.address = "topic://VirtualTopic.eng.brew.build.complete"
    assert (
        mock_umb_event.message.address.replace("topic://", VIRTUAL_TOPIC_ADDRESS_PREFIX)
        in addresses
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
        with patch.object(handler, "accept") as mock_accept:
            handler.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)

        # Second call raises exception given above, message should be rejected
        with patch.object(handler, "release") as mock_release:
            handler.on_message(mock_umb_event)
            mock_release.assert_called_once_with(mock_umb_event.delivery, delivered=True)

    # slow_fetch_brew_build.apply_async is called once per message with a build_id arg
    slow_fetch_brew_build_mock.assert_has_calls((call(args=(mock_id,)), call(args=(mock_id,))))


def test_handle_tag_and_untag_messages():
    """Test that the BrewUMBReceiverHandler class either
    accepts tag / untag messages, when no exceptions are raised
    OR rejects tag / untag messages if any exception is raised"""
    listener = BrewUMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    _, tag_address, untag_address, _ = addresses.keys()
    assert VIRTUAL_TOPIC_ADDRESS_PREFIX in tag_address
    assert VIRTUAL_TOPIC_ADDRESS_PREFIX in untag_address
    assert ".tag" in tag_address
    assert ".untag" in untag_address
    tag_address = tag_address.replace(VIRTUAL_TOPIC_ADDRESS_PREFIX, "topic://")
    untag_address = untag_address.replace(VIRTUAL_TOPIC_ADDRESS_PREFIX, "topic://")

    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        handler = BrewUMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

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
        with patch.object(handler, "accept") as mock_accept:
            # First tag call raises no exception, message should be accepted
            mock_umb_event.message.address = tag_address
            handler.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)
            mock_accept.reset_mock()

            # First untag call raises no exception, message should be accepted
            mock_umb_event.message.address = untag_address
            handler.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)

        with patch.object(handler, "release") as mock_release:
            # Second tag call raises exception given above, message should be rejected
            mock_umb_event.message.address = tag_address
            handler.on_message(mock_umb_event)
            mock_release.assert_called_once_with(mock_umb_event.delivery, delivered=True)
            mock_release.reset_mock()

            # Second untag call raises exception given above, message should be rejected
            mock_umb_event.message.address = untag_address
            handler.on_message(mock_umb_event)
            mock_release.assert_called_once_with(mock_umb_event.delivery, delivered=True)

    # slow_update_brew_tags.apply_async is called once per message with a build_id arg
    # and either a "tag_added" or "tag_removed" kwarg
    mock_tag_call = call(args=(mock_id,), kwargs={"tag_added": mock_tag})
    mock_untag_call = call(args=(mock_id,), kwargs={"tag_removed": mock_tag})

    # Four messages / calls total, two tag and two untag, two accepted and two rejected
    call_list = [mock_tag_call, mock_untag_call, mock_tag_call, mock_untag_call]
    slow_update_brew_tags_mock.assert_has_calls(call_list)
