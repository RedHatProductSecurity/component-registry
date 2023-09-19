import json
from unittest.mock import MagicMock, call, patch

import pytest
from django.conf import settings

from corgi.monitor.consumer import UMBListener, UMBReceiverHandler

pytestmark = pytest.mark.unit

VIRTUAL_TOPIC_ADDRESS_PREFIX = f"Consumer.{settings.UMB_CONSUMER}."


def test_umb_listener_defines_settings():
    """Test UMBListener subclass listens for messages on defined address
    and handles them with correct class"""
    listener = UMBListener()
    assert listener.virtual_topic_addresses == {
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng."
        f"brew.build.complete": UMBReceiverHandler.brew_builds,
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng."
        f"brew.build.tag": UMBReceiverHandler.brew_tags,
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng."
        f"brew.build.untag": UMBReceiverHandler.brew_tags,
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng."
        f"errata.activity.status": UMBReceiverHandler.et_shipped_errata,
        f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng."
        f"pnc.sbom.spike.complete": UMBReceiverHandler.sbomer_complete,
    }

    # Stub out the real Container class with a mock we can assert on
    with patch("corgi.monitor.consumer.Container") as mock_container_constructor:
        # Stub out a different class that we don't want to test here
        with patch("corgi.monitor.consumer.UMBReceiverHandler") as mock_receiver_constructor:
            listener.consume()

    # We call the UMBReceiverHandler() constructor with the class's virtual topic addresses
    # and a dict of selectors for each address
    mock_receiver_constructor.assert_called_once_with(
        virtual_topic_addresses=listener.virtual_topic_addresses, selectors=listener.selectors
    )

    # We call the Container() constructor with an instance of UMBReceiverHandler()
    # returned by mock_receiver_constructor above so we don't need real UMB certs in tests
    mock_receiver_instance = mock_receiver_constructor.return_value
    mock_container_constructor.assert_called_once_with(mock_receiver_instance)

    # We call run() on the Container() instance returned by mock_container_constructor
    mock_container_instance = mock_container_constructor.return_value
    mock_container_instance.run.assert_called_once_with()


def test_umb_receiver_setup():
    """Test that the UMBReceiverHandler class is set up correctly"""
    listener = UMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain") as mock_ssl_domain_constructor:
        handler = UMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

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
    assert len(handler.virtual_topic_addresses) == 5
    mock_umb_event.container.create_receiver.assert_has_calls(create_receiver_calls)


def test_umb_receiver_():
    """Test that the UMBReceiverHandler class raises an error
    when a message is missing its address or is for an unknown topic"""
    listener = UMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        handler = UMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

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


def test_umb_receiver_handles_builds():
    """Test that the UMBReceiverHandler class either
    accepts a "build complete" message, when no exception is raised
    OR rejects the message if any exception is raised"""
    listener = UMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        handler = UMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

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
    """Test that the UMBReceiverHandler class either
    accepts tag / untag messages, when no exceptions are raised
    OR rejects tag / untag messages if any exception is raised"""
    listener = UMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    tag_address = f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng.brew.build.tag"
    untag_address = f"{VIRTUAL_TOPIC_ADDRESS_PREFIX}VirtualTopic.eng.brew.build.untag"
    assert tag_address in addresses.keys()
    assert untag_address in addresses.keys()
    tag_address = tag_address.replace(VIRTUAL_TOPIC_ADDRESS_PREFIX, "topic://")
    untag_address = untag_address.replace(VIRTUAL_TOPIC_ADDRESS_PREFIX, "topic://")

    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        handler = UMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

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


def test_umb_receiver_handles_shipped_errata():
    """Test that the UMBReceiverHandler class either
    accepts a shipped errata message, when no exception is raised
    OR rejects the message if any exception is raised"""
    listener = UMBListener()
    addresses = listener.virtual_topic_addresses
    selectors = listener.selectors
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        handler = UMBReceiverHandler(virtual_topic_addresses=addresses, selectors=selectors)

    mock_umb_event = MagicMock()
    mock_invalid_event = MagicMock()
    mock_sbomer_event = MagicMock()
    mock_id = "1234"
    address = "topic://VirtualTopic.eng.errata.activity.status"
    mock_umb_event.message.address = address
    mock_invalid_event.message.address = address
    mock_sbomer_event.message.address = address
    assert address.replace("topic://", VIRTUAL_TOPIC_ADDRESS_PREFIX) in addresses

    mock_umb_event.message.body = (
        '{"errata_status": "SHIPPED_LIVE", "errata_id": MOCK_ID,'
        ' "product": "Product", "release": "Red Hat release of Product"}'.replace(
            "MOCK_ID", mock_id
        )
    )
    # Messages with an invalid status should get filtered out by the topic selector
    mock_invalid_event.message.body = (
        '{"errata_status": "DROPPED_NO_SHIP", "errata_id": MOCK_ID,'
        ' "product": "Product", "release": "Red Hat release of Product"}'.replace(
            "MOCK_ID", mock_id
        )
    )
    # Messages for Middleware/SBOMer products should be handled by separately
    mock_sbomer_event.message.body = (
        '{"errata_status": "SHIPPED_LIVE", "errata_id": MOCK_ID,'
        ' "product": "RHBQ", "release": "Red Hat build of Quarkus Middleware"}'.replace(
            "MOCK_ID", mock_id
        )
    )
    mock_id = int(mock_id)

    umb_message_exceptions = (None, None, Exception("Third message received raises an exception"))
    # side_effect is a list of return values for each call to slow_fetch_brew_build.apply_async()
    # If any side_effect is an Exception subclass, it will be raised
    # Any other side_effect is just returned instead
    with patch(
        "corgi.monitor.consumer.slow_handle_shipped_errata.apply_async",
        side_effect=umb_message_exceptions,
    ) as slow_handle_shipped_errata_mock:
        # First call raises no exception, message should be accepted
        with patch.object(handler, "accept") as mock_accept:
            handler.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)
            mock_accept.reset_mock()

            # Second call raises no exception
            # The task will raise an exception for the invalid status (should never happen)
            # This will log the invalid message status and ID in our Celery task results
            # Raising an exception in the listener would block processing other messages
            handler.on_message(mock_invalid_event)
            mock_accept.assert_called_once_with(mock_invalid_event.delivery)

        # Third call raises exception given above, message should be rejected
        with patch.object(handler, "release") as mock_release:
            handler.on_message(mock_umb_event)
            mock_release.assert_called_once_with(mock_umb_event.delivery, delivered=True)

    # slow_handle_shipped_errata.apply_async is called once per message
    # with an erratum_id and erratum_status arg
    slow_handle_shipped_errata_mock.assert_has_calls(
        (
            call(args=(mock_id, "SHIPPED_LIVE")),
            call(args=(mock_id, "DROPPED_NO_SHIP")),
            call(args=(mock_id, "SHIPPED_LIVE")),
        )
    )

    # Test SBOMer release errata
    with patch(
        "corgi.monitor.consumer.slow_handle_pnc_errata_released.apply_async",
    ) as slow_handle_pnc_errata_released_mock:
        # No exception
        with patch.object(handler, "accept") as mock_accept:
            handler.on_message(mock_sbomer_event)
            mock_accept.assert_called_once_with(mock_sbomer_event.delivery)
            mock_accept.reset_mock()

            # Make sure invalid statuses work in the SBOMer erratum handler as well
            mock_sbomer_event.message.body = mock_sbomer_event.message.body.replace(
                "SHIPPED_LIVE", "DROPPED_NO_SHIP"
            )
            handler.on_message(mock_sbomer_event)
            mock_accept.assert_called_once_with(mock_sbomer_event.delivery)

    # slow_handle_pnc_released_errata takes erratum_id, erratum_status as arguments
    slow_handle_pnc_errata_released_mock.assert_has_calls(
        (
            call(args=(mock_id, "SHIPPED_LIVE")),
            call(args=(mock_id, "DROPPED_NO_SHIP")),
        )
    )


def test_sbomer_handles_sbom_available():
    """Test that the UMB receiver correctly handles SBOM available messages"""
    listener = UMBListener()
    with patch("corgi.monitor.consumer.SSLDomain"):
        receiver = UMBReceiverHandler(
            virtual_topic_addresses=listener.virtual_topic_addresses, selectors=listener.selectors
        )

    mock_event = MagicMock()

    with open("tests/data/pnc/sbom_complete.json") as test_file:
        test_data = json.load(test_file)
    mock_event.message.address = test_data["topic"].replace("/topic/", VIRTUAL_TOPIC_ADDRESS_PREFIX)
    mock_event.message.body = json.dumps(test_data["msg"])

    # Call first with a valid message, then with a bad SBOM URL
    fetch_sbom_exceptions = (None, Exception("Bad SBOM URL"))

    with patch(
        "corgi.monitor.consumer.slow_fetch_pnc_sbom.delay", side_effect=fetch_sbom_exceptions
    ) as mock_fetch_sbom:
        with patch.object(receiver, "accept") as mock_accept:
            receiver.on_message(mock_event)
            mock_accept.assert_called_once_with(mock_event.delivery)
            mock_fetch_sbom.assert_called_once_with(
                test_data["msg"]["purl"],
                test_data["msg"]["productConfig"]["errataTool"],
                test_data["msg"]["build"],
                test_data["msg"]["sbom"],
            )

        with patch.object(receiver, "release") as mock_release:
            receiver.on_message(mock_event)
            mock_release.assert_called_once_with(mock_event.delivery, delivered=True)
