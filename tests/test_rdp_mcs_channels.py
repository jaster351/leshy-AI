"""Wire-level MCS channel handshake examples and strict confirmation checks."""
from __future__ import annotations

import pytest

from leshy.rdp_mcs_channels import (
    MCSChannelError, erect_domain_request, attach_user_request,
    parse_attach_user_confirm, channel_join_request, parse_channel_join_confirm,
    required_channel_ids,
)


def test_microsoft_wire_examples():
    assert erect_domain_request().hex() == "0300000c02f0800401000100"
    assert attach_user_request().hex() == "0300000802f08028"
    assert parse_attach_user_confirm(bytes.fromhex("0300000b02f0802e000006")) == 1007
    assert channel_join_request(1007, 1007).hex() == "0300000c02f08038000603ef"
    assert channel_join_request(1007, 1003).hex() == "0300000c02f08038000603eb"
    parse_channel_join_confirm(
        bytes.fromhex("0300000f02f0803e00000603ef03ef"),
        user_id=1007, requested_channel=1007,
    )
    parse_channel_join_confirm(
        bytes.fromhex("0300000f02f0803e00000603eb03eb"),
        user_id=1007, requested_channel=1003,
    )


@pytest.mark.parametrize("corrupt", [
    "0300000b02f0802e010006",
    "0300000b02f0802e050006",
    "0300000b02f0802c000006",
    "0300000b02f0802e00ffff",
    "0300000a02f0802e000006",
    "0300000b03f0802e000006",
    "0300000b02f0802e00000600",
])
def test_attach_confirm_rejects(corrupt):
    with pytest.raises(MCSChannelError):
        parse_attach_user_confirm(bytes.fromhex(corrupt))


@pytest.mark.parametrize("corrupt", [
    "0300000f02f0803e01000603ef03ef",
    "0300000f02f0803e00000703ef03ef",
    "0300000f02f0803e00000603eb03ef",
    "0300000f02f0803e00000603ef03eb",
    "0300000f02f0803c00000603ef03ef",
    "0300000f02f0803e00000603ef03ef00",
    "0300000e02f0803e00000603ef03ef",
])
def test_join_confirm_rejects(corrupt):
    with pytest.raises(MCSChannelError):
        parse_channel_join_confirm(
            bytes.fromhex(corrupt), user_id=1007, requested_channel=1007
        )


@pytest.mark.parametrize("channel", [0, 1000, 65536, -1, True, "1007"])
def test_bad_channel_ids(channel):
    with pytest.raises(MCSChannelError):
        channel_join_request(channel, 1003)


def test_distinct_channel_guards():
    assert required_channel_ids(1007, 1003) == (1007, 1003)
    with pytest.raises(MCSChannelError):
        required_channel_ids(1007, 1007)
