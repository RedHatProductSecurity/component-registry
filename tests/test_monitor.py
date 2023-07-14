import json
from unittest.mock import MagicMock, call, patch

import koji
import pytest
from django.conf import settings

from corgi.monitor.consumer import (
    BrewUMBHandler,
    SbomerUMBHandler,
    UMBDispatcher,
    UMBHandler,
)

pytestmark = pytest.mark.unit

ADDRESS_PREFIX = f"Consumer.{settings.UMB_CONSUMER}."


def test_dispatcher_setup():
    """Test UMBDispatcher's configuration"""
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain") as mock_ssl_domain_constructor:
        dispatcher = UMBDispatcher()

    # We listen (in client mode) to the address passed in, and connect to the UMB URL from settings
    mock_ssl_domain_constructor.assert_called_once_with(mock_ssl_domain_constructor.MODE_CLIENT)
    assert dispatcher.urls == [settings.UMB_BROKER_URL]

    # Messages should be accepted manually (only if no exception)
    assert dispatcher.auto_accept is False
    # Accepted messages should be automatically settled (this is the default)
    assert dispatcher.auto_settle is True


def test_dispatcher_requires_addresses():
    """Test UMBHandler raises ValueError for missing addresses"""

    class NoAddressHandler(UMBHandler):
        def __init__(self):
            super(NoAddressHandler, self).__init__(None, None)

    with pytest.raises(ValueError):
        NoAddressHandler()


def test_dispatcher_requires_handlers():
    """Test UMBHandler raises ValueError for missing handler functions"""

    class BadHandler(UMBHandler):
        def __init__(self):
            addresses = {"UMB_Topic": None}
            super(BadHandler, self).__init__(addresses, {})

    with pytest.raises(ValueError):
        BadHandler()


def test_brew_umb_handler_defines_settings():
    """Test BrewUMBHandler subclass listens for messages on defined address
    and handles them with correct methods"""
    handler = BrewUMBHandler()
    assert handler.virtual_topic_addresses == {
        f"{ADDRESS_PREFIX}VirtualTopic.eng.brew.build.complete": handler.handle_builds,
        # Disabled due to DB performance issues - see notes in task
        # f"{ADDRESS_PREFIX}VirtualTopic.eng.brew.build.deleted": handler.handle_deleted_builds,
        f"{ADDRESS_PREFIX}VirtualTopic.eng.brew.build.tag": handler.handle_tags,
        f"{ADDRESS_PREFIX}VirtualTopic.eng.brew.build.untag": handler.handle_tags,
        f"{ADDRESS_PREFIX}VirtualTopic.eng."
        f"errata.activity.status": handler.handle_shipped_errata,
    }


def test_brew_umb_handler_setup():
    """Test that the BrewUMBHandler class is set up correctly by the UMBDispatcher"""
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain") as mock_ssl_domain_constructor:
        dispatcher = UMBDispatcher()

    # The addresses and selectors of the dispatcher should match those of a handler
    handler = BrewUMBHandler()
    assert set(dispatcher.virtual_topic_addresses).issuperset(set(handler.virtual_topic_addresses))
    assert set(dispatcher.selectors).issuperset(set(handler.selectors))

    mock_umb_event = MagicMock()
    mock_connection_constructor = mock_umb_event.container.connect
    mock_connection_instance = mock_connection_constructor.return_value
    mock_ssl_domain_instance = mock_ssl_domain_constructor.return_value
    with patch("corgi.monitor.consumer.Selector") as mock_selector:
        dispatcher.on_start(mock_umb_event)
        mock_selector.assert_called_once_with("errata_status = 'SHIPPED_LIVE'")
        shipped_errata_selector = mock_selector.return_value

    mock_connection_constructor.assert_called_once_with(
        urls=dispatcher.urls, ssl_domain=mock_ssl_domain_instance, heartbeat=500
    )

    # One receiver per virtual topic address should be created
    create_receiver_calls = [
        call(
            mock_connection_instance,
            address,
            name=None,
            options=[shipped_errata_selector] if dispatcher.selectors.get(address) else [],
        )
        for address in dispatcher.virtual_topic_addresses
    ]
    assert len(handler.virtual_topic_addresses) == 4
    mock_umb_event.container.create_receiver.assert_has_calls(create_receiver_calls)


def test_bad_addresses():
    """Test that the UMBDispatcher class raises an error
    when a message is missing its address or is for an unknown topic"""
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()

    mock_umb_event = MagicMock()
    mock_id = "1"
    mock_umb_event.message.address = "invalid_virtual_topic_address"
    assert mock_umb_event.message.address not in dispatcher.virtual_topic_addresses

    mock_umb_event.message.body = '{"info": {"build_id": MOCK_ID}}'.replace("MOCK_ID", mock_id)
    with pytest.raises(ValueError):
        dispatcher.on_message(mock_umb_event)

    mock_umb_event.message.address = None
    with pytest.raises(ValueError):
        dispatcher.on_message(mock_umb_event)


def test_brew_umb_handler_handles_builds():
    """Test that the BrewUMBHandler class either
    accepts a "build complete" message, when no exception is raised
    OR rejects the message if any exception is raised"""
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()

    mock_umb_event = MagicMock()
    mock_id = "1"
    mock_umb_event.message.address = "topic://VirtualTopic.eng.brew.build.complete"
    assert (
        mock_umb_event.message.address.replace("topic://", ADDRESS_PREFIX)
        in dispatcher.virtual_topic_addresses
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


def brew_umb_handler_deletes_builds():
    """Test that the BrewUMBHandler class either
    accepts a "build deleted" message, when no exception is raised
    OR rejects the message if any exception is raised"""
    # Disabled due to DB performance issues - see notes in task
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()

    mock_umb_event = MagicMock()
    mock_id = 1
    mock_state = koji.BUILD_STATES["DELETED"]
    mock_umb_event.message.address = "topic://VirtualTopic.eng.brew.build.deleted"
    assert (
        mock_umb_event.message.address.replace("topic://", ADDRESS_PREFIX)
        in dispatcher.virtual_topic_addresses
    )

    mock_umb_event.message.body = '{"info": {"build_id": MOCK_ID, "state": STATE}}'.replace(
        "MOCK_ID", str(mock_id), 1
    ).replace("STATE", str(mock_state), 1)

    umb_message_exceptions = (None, Exception("Second message received raises an exception"))
    # side_effect is a list of return values for each call to slow_fetch_brew_build.apply_async()
    # If any side_effect is an Exception subclass, it will be raised
    # Any other side_effect is just returned instead
    with patch(
        "corgi.monitor.consumer.slow_delete_brew_build.apply_async",
        side_effect=umb_message_exceptions,
    ) as slow_delete_brew_build_mock:
        # First call raises no exception, message should be accepted
        with patch.object(dispatcher, "accept") as mock_accept:
            dispatcher.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)

        # Second call raises exception given above, message should be rejected
        with patch.object(dispatcher, "release") as mock_release:
            dispatcher.on_message(mock_umb_event)
            mock_release.assert_called_once_with(mock_umb_event.delivery, delivered=True)

    # slow_delete_brew_build.apply_async is called once per message
    # with build_id and build_state (should always be 2 / DELETED) args
    slow_delete_brew_build_mock.assert_has_calls(
        (call(args=(mock_id, mock_state)), call(args=(mock_id, mock_state)))
    )


def test_handle_tag_and_untag_messages():
    """Test that the BrewUMBHandler class either
    accepts tag / untag messages, when no exceptions are raised
    OR rejects tag / untag messages if any exception is raised"""
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()

    # Only want addresses from Brew here
    handler = BrewUMBHandler()
    addresses = handler.virtual_topic_addresses
    _, tag_address, untag_address, _ = addresses.keys()
    assert ADDRESS_PREFIX in tag_address
    assert ADDRESS_PREFIX in untag_address
    assert ".tag" in tag_address
    assert ".untag" in untag_address
    tag_address = tag_address.replace(ADDRESS_PREFIX, "topic://")
    untag_address = untag_address.replace(ADDRESS_PREFIX, "topic://")

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


def test_brew_umb_handler_handles_shipped_errata():
    """Test that the BrewUMBHandler class either
    accepts a shipped errata message, when no exception is raised
    OR rejects the message if any exception is raised"""
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()

    addresses = dispatcher.virtual_topic_addresses

    mock_umb_event = MagicMock()
    mock_invalid_event = MagicMock()
    mock_id = "1234"
    address = "topic://VirtualTopic.eng.errata.activity.status"
    mock_umb_event.message.address = address
    mock_invalid_event.message.address = address
    assert address.replace("topic://", ADDRESS_PREFIX) in addresses

    mock_umb_event.message.body = '{"errata_status": "SHIPPED_LIVE", "errata_id": MOCK_ID}'.replace(
        "MOCK_ID", mock_id
    )
    # Messages with an invalid status should get filtered out by the topic selector
    mock_invalid_event.message.body = (
        '{"errata_status": "DROPPED_NO_SHIP", "errata_id": MOCK_ID}'.replace("MOCK_ID", mock_id)
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
        with patch.object(dispatcher, "accept") as mock_accept:
            dispatcher.on_message(mock_umb_event)
            mock_accept.assert_called_once_with(mock_umb_event.delivery)
            mock_accept.reset_mock()

            # Second call raises no exception
            # The task will raise an exception for the invalid status (should never happen)
            # This will log the invalid message status and ID in our Celery task results
            # Raising an exception in the listener would block processing other messages
            dispatcher.on_message(mock_invalid_event)
            mock_accept.assert_called_once_with(mock_invalid_event.delivery)

        # Third call raises exception given above, message should be rejected
        with patch.object(dispatcher, "release") as mock_release:
            dispatcher.on_message(mock_umb_event)
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


def test_sbomer_umb_handler_setup():
    """Test that the SbomerUMBHandler class is set up correctly by the UMBDispatcher"""
    # Stub out the SSLDomain config class to avoid needing real UMB certs in tests
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()

    # The addresses and selectors of the dispatcher should match those of a handler
    handler = SbomerUMBHandler()
    assert set(dispatcher.virtual_topic_addresses).issuperset(set(handler.virtual_topic_addresses))
    assert set(dispatcher.selectors).issuperset(set(handler.selectors))


def test_sbomer_handles_sbom_available():
    """Test that the PNC UMB receiver correctly handles SBOM available messages"""
    with patch("corgi.monitor.consumer.SSLDomain"):
        dispatcher = UMBDispatcher()

    mock_event = MagicMock()

    with open("tests/data/pnc/sbom_complete.json") as test_file:
        test_data = json.load(test_file)
    mock_event.message.address = test_data["topic"].replace("/topic/", ADDRESS_PREFIX)
    mock_event.message.body = json.dumps(test_data["msg"])

    # Call first with a valid message, then with a bad SBOM URL
    fetch_sbom_exceptions = (None, Exception("Bad SBOM URL"))

    with patch(
        "corgi.monitor.consumer.slow_fetch_pnc_sbom.delay", side_effect=fetch_sbom_exceptions
    ) as mock_fetch_sbom:
        with patch.object(dispatcher, "accept") as mock_accept:
            dispatcher.on_message(mock_event)
            mock_accept.assert_called_once_with(mock_event.delivery)
            mock_fetch_sbom.assert_called_once_with(
                test_data["msg"]["purl"],
                test_data["msg"]["productConfig"]["errataTool"],
                test_data["msg"]["build"],
                test_data["msg"]["sbom"],
            )

        with patch.object(dispatcher, "release") as mock_release:
            dispatcher.on_message(mock_event)
            mock_release.assert_called_once_with(mock_event.delivery, delivered=True)
