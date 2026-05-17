"""Structural checks on the Alpaca adapter.

AlpacaBroker is the network edge — its method bodies talk to Alpaca and are
covered by integration testing, not unit tests (same as the pre-existing
``submit_bracket`` / ``list_positions``). What we CAN verify offline, and what
matters most, is that the adapter still implements the full Broker protocol —
``issubclass`` against the @runtime_checkable protocol catches a missing or
misnamed method without any network or credentials.
"""
from __future__ import annotations

from trident.execution.alpaca import AlpacaBroker
from trident.execution.broker import Broker


def test_alpaca_broker_implements_the_full_broker_protocol() -> None:
    assert issubclass(AlpacaBroker, Broker)


def test_alpaca_broker_has_the_management_methods() -> None:
    for method in ("submit_order", "cancel_order", "replace_order", "close_position"):
        assert callable(getattr(AlpacaBroker, method, None)), method
