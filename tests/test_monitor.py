from unittest.mock import MagicMock, call, patch

import pytest
from django.conf import settings

from corgi.monitor.consumer import BrewUMBListener, UMBListener, UMBReceiverHandler

pytestmark = pytest.mark.unit


def test_umb_listener_requires_address():
    """Test UMBListener base class raises NotImplementedError for missing address"""
    listener = UMBListener()
    assert listener.virtual_topic_address == ""
    with pytest.raises(NotImplementedError):
        listener.consume()


def test_brew_umb_listener_defines_address():
    """Test BrewUMBListener subclass listens for messages on defined address"""
    listener = BrewUMBListener()
    assert (
        listener.virtual_topic_address
        == f"Consumer.{settings.UMB_CONSUMER}.VirtualTopic.eng.brew.build.complete"
    )

    # Stub out the real Container class with a mock we can assert on
    with patch("corgi.monitor.consumer.Container") as mock_container_constructor:
        # Stub out a different class that we don't want to test here
        with patch("corgi.monitor.consumer.UMBReceiverHandler") as mock_receiver_constructor:
            listener.consume()

    # We call the Container() constructor with an instance of UMBReceiverHandler()
    # returned by mock_receiver_constructor above so we don't need real UMB certs in tests
    mock_receiver_instance = mock_receiver_constructor.return_value
    mock_container_constructor.assert_called_once_with(mock_receiver_instance)

    # We call run() on the Container() instance returned by mock_container_constructor
    mock_container_instance = mock_container_constructor.return_value
    mock_container_instance.run.assert_called_once_with()


def test_umb_receiver_handles_messages():
    """Test that the UMBReceiverHandler class is set up correctly, then either
    accepts a message, when no exception is raised
    OR rejects a message if any exception is raised"""
    address = BrewUMBListener().virtual_topic_address
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain") as mock_ssl_domain_constructor:
        handler = UMBReceiverHandler(virtual_topic_address=address)

    # We listen (in client mode) to the address passed in, and connect to the UMB URL from settings
    mock_ssl_domain_constructor.assert_called_once_with(mock_ssl_domain_constructor.MODE_CLIENT)
    assert handler.virtual_topic_address == address
    assert handler.urls == [settings.UMB_BROKER_URL]

    # Messages should be accepted manually (only if no exception)
    assert handler.auto_accept is False
    # Accepted messages should be automatically settled (this is the default)
    assert handler.auto_settle is True

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
