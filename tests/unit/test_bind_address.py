"""Bind-address classification and the network-exposure warning.

The platform's posture is that the dashboard is a local desktop tool holding AV
review data with no network authentication of its own. Binding wider than
loopback is permitted for approved deployments but must never happen silently,
so the classification below is a security invariant rather than cosmetics.
"""

from __future__ import annotations

import pytest

from backend.settings import (
    binds_all_interfaces,
    display_host,
    is_loopback_host,
    network_exposure_warning,
)

# Every spelling of "this machine only". The 127.0.0.0/8 entries matter because
# a string comparison against "127.0.0.1" would misclassify them as remote.
LOOPBACK = ["127.0.0.1", "127.0.0.2", "127.1.2.3", "::1", "localhost", "LocalHost", " 127.0.0.1 "]

# The unspecified address in both families - "bind to every interface".
BIND_ALL = ["0.0.0.0", "::", "0:0:0:0:0:0:0:0"]

# Reachable from other machines: a LAN address, a public address, a hostname.
REMOTE = ["192.168.1.50", "10.0.0.7", "203.0.113.9", "av-laptop.corp", "fe80::1"]


@pytest.mark.parametrize("host", LOOPBACK)
def test_loopback_hosts_are_recognised(host: str) -> None:
    assert is_loopback_host(host)
    assert not binds_all_interfaces(host)
    assert network_exposure_warning(host) is None


@pytest.mark.parametrize("host", BIND_ALL)
def test_bind_all_is_not_mistaken_for_loopback(host: str) -> None:
    assert binds_all_interfaces(host)
    assert not is_loopback_host(host)


@pytest.mark.parametrize("host", REMOTE)
def test_remote_hosts_are_neither_loopback_nor_bind_all(host: str) -> None:
    assert not is_loopback_host(host)
    assert not binds_all_interfaces(host)


@pytest.mark.parametrize("host", BIND_ALL + REMOTE)
def test_every_non_loopback_bind_warns(host: str) -> None:
    """No non-loopback bind may be silent - that is the whole point."""
    warning = network_exposure_warning(host)
    assert warning is not None
    assert host in warning
    # The warning has to tell the tester what to do, not just that something is wrong.
    assert "AV_HOST=127.0.0.1" in warning


@pytest.mark.parametrize("host", BIND_ALL)
def test_bind_all_warning_is_the_stronger_wording(host: str) -> None:
    warning = network_exposure_warning(host)
    assert warning is not None
    assert "EVERY network interface" in warning


@pytest.mark.parametrize("host", LOOPBACK + BIND_ALL)
def test_locally_reachable_binds_are_displayed_as_localhost(host: str) -> None:
    """A URL printed as http://0.0.0.0:8000 is not one a browser can open."""
    assert display_host(host) == "localhost"


def test_remote_binds_are_displayed_verbatim() -> None:
    assert display_host("192.168.1.50") == "192.168.1.50"
    assert display_host("av-laptop.corp") == "av-laptop.corp"


def test_the_shipped_default_binds_to_loopback() -> None:
    """Guards against a default flipped to 0.0.0.0 during some future debugging."""
    from backend.settings import get_settings

    assert is_loopback_host(get_settings().host)
