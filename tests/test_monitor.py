from unittest.mock import MagicMock, call, patch

import pytest
from django.conf import settings

from corgi.monitor.consumer import (
    BrewBuildUMBListener,
    BrewBuildUMBReceiverHandler,
    UMBListener,
    UMBReceiverHandler,
)

pytestmark = pytest.mark.unit


def test_umb_listener_requires_address():
    """Test UMBListener base class raises NotImplementedError for missing address"""
    listener = UMBListener()
    assert listener.virtual_topic_address == ""
    with pytest.raises(NotImplementedError):
        listener.consume()


def test_brew_build_umb_listener_defines_address():
    """Test BrewBuildUMBListener subclass listens for messages on defined address"""
    listener = BrewBuildUMBListener()
    assert (
        listener.virtual_topic_address
        == f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.complete"
    )
    assert listener.handler_class == BrewBuildUMBReceiverHandler

    # Stub out the real Container class with a mock we can assert on
    with patch("corgi.monitor.consumer.Container") as mock_container_constructor:
        # Stub out a different class that we don't want to test here
        with patch.object(BrewBuildUMBListener, "handler_class") as mock_receiver_constructor:
            listener.consume()

    # We call the BrewBuildUMBReceiverHandler() constructor with the class's virtual topic address
    mock_receiver_constructor.assert_called_once_with(
        virtual_topic_address=listener.virtual_topic_address
    )

    # We call the Container() constructor with an instance of BrewBuildUMBReceiverHandler()
    # returned by mock_receiver_constructor above so we don't need real UMB certs in tests
    mock_receiver_instance = mock_receiver_constructor.return_value
    mock_container_constructor.assert_called_once_with(mock_receiver_instance)

    # We call run() on the Container() instance returned by mock_container_constructor
    mock_container_instance = mock_container_constructor.return_value
    mock_container_instance.run.assert_called_once_with()


def test_umb_receiver_requires_handling_logic():
    """Test UMBReceiverHandler base class raises NotImplementedError
    for topic-specific message-handling logic"""
    address = UMBListener().virtual_topic_address
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain") as mock_ssl_domain_constructor:
        handler = UMBReceiverHandler(virtual_topic_address=address)
    mock_ssl_domain_constructor.assert_called_once_with(mock_ssl_domain_constructor.MODE_CLIENT)
    mock_umb_event = MagicMock()
    with pytest.raises(NotImplementedError):
        handler.on_message(mock_umb_event)


def test_brew_build_umb_receiver_connects():
    """Test that the BrewBuildUMBReceiverHandler class is set up correctly"""
    address = BrewBuildUMBListener().virtual_topic_address
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain") as mock_ssl_domain_constructor:
        handler = BrewBuildUMBReceiverHandler(virtual_topic_address=address)

    # We listen (in client mode) to the address passed in, and connect to the UMB URL from settings
    mock_ssl_domain_constructor.assert_called_once_with(mock_ssl_domain_constructor.MODE_CLIENT)
    mock_ssl_domain_instance = mock_ssl_domain_constructor.return_value
    assert handler.virtual_topic_address == address
    assert handler.urls == [settings.UMB_BROKER_URL]

    # Messages should be accepted manually (only if no exception)
    assert handler.auto_accept is False
    # Accepted messages should be automatically settled (this is the default)
    assert handler.auto_settle is True

    mock_umb_event = MagicMock()
    mock_connection_constructor = mock_umb_event.container.connect
    mock_connection_instance = mock_connection_constructor.return_value
    handler.on_start(mock_umb_event)

    mock_connection_constructor.assert_called_once_with(
        urls=handler.urls, ssl_domain=mock_ssl_domain_instance, heartbeat=500
    )
    mock_umb_event.container.create_receiver.assert_called_once_with(
        mock_connection_instance,
        handler.virtual_topic_address,
        name=None,
        options=[handler.selector] if handler.selector else [],
    )


def test_brew_build_umb_receiver_handles_messages():
    """Test that the BrewBuildUMBReceiverHandler class either
    accepts a message, when no exception is raised
    OR rejects a message if any exception is raised"""
    address = BrewBuildUMBListener().virtual_topic_address
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        handler = BrewBuildUMBReceiverHandler(virtual_topic_address=address)

    mock_umb_event = MagicMock()
    mock_id = "1"
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
