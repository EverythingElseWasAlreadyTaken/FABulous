"""Tests for routing channel declarations and their fabric-wide resolution."""

import pickle
from collections.abc import Callable
from copy import deepcopy

import pytest

from fabulous.custom_exception import InvalidFabricDefinition
from fabulous.fabric_cad.gen_bitstream_spec import generateBitstreamSpec
from fabulous.fabric_definition.channel import (
    ChannelDeclaration,
    RoutingChannel,
    resolve_channels,
)
from fabulous.fabric_definition.define import Direction
from fabulous.fabric_definition.fabric import Fabric
from fabulous.fabric_definition.instance import TileInstance
from fabulous.fabric_generator.parser.parse_csv import parse_port_line
from tests.fabric_definition.conftest import make_empty_tile

N = Direction.NORTH


def _decl(begin: str | None, end: str | None, y: int = -4) -> ChannelDeclaration:
    return ChannelDeclaration(N, 0, y, 4, begin, end)


@pytest.mark.parametrize(
    "copy",
    [
        pytest.param(lambda _: _decl("N4BEG", "N4END"), id="rebuilt"),
        pytest.param(deepcopy, id="deepcopy"),
        pytest.param(lambda d: pickle.loads(pickle.dumps(d)), id="pickle"),
    ],
)
def test_equal_declarations_are_one_object(
    copy: Callable[[ChannelDeclaration], ChannelDeclaration],
) -> None:
    declaration = _decl("N4BEG", "N4END")
    assert copy(declaration) is declaration


class TestResolveChannels:
    """A line with a NULL end resolves to the channel it takes part in."""

    def test_halves_resolve_against_the_full_line(self) -> None:
        full, start, end = (
            _decl("N4BEG", "N4END"),
            _decl("N4BEG", None),
            _decl(None, "N4END"),
        )
        channels = resolve_channels([full, start, end])

        expected = RoutingChannel(N, 0, -4, 4, "N4BEG", "N4END")
        assert channels[full] is channels[start] is channels[end] is expected

    def test_halves_without_a_full_line_pair_by_name(self) -> None:
        start, end = _decl("bot2top", None, -1), _decl(None, "bot2top", -1)
        channels = resolve_channels([start, end])

        assert channels[start] == channels[end]
        assert (channels[start].begin, channels[start].end) == ("bot2top", "bot2top")

    def test_two_far_ends_for_one_end_raise(self) -> None:
        with pytest.raises(ValueError, match="two different far ends"):
            resolve_channels([_decl("N4BEG", "N4END"), _decl("N4BEG", "OTHER")])


class TestRoutingChannelProblems:
    """Every channel end needs its partner one hop along the channel."""

    @staticmethod
    def _column(make_fabric: Callable[..., Fabric], names: list[str | None]) -> Fabric:
        lines = {
            "TOP": "NORTH,NULL,0,-1,X,1",  # ends the channel
            "BOT": "NORTH,X,0,-1,NULL,1",  # starts it, one row further south
        }
        tiles = {n: make_empty_tile(n, parse_port_line(lines[n])[0]) for n in lines}
        return make_fabric(tile=[[tiles.get(n)] for n in names])

    def test_start_below_end_is_consistent(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        assert (
            self._column(make_fabric, ["TOP", "BOT"]).routing_channel_problems() == []
        )

    @pytest.mark.parametrize(
        ("names", "problem"),
        [
            pytest.param(
                [None, "BOT"], "has nothing receiving it at X0Y0", id="dangling"
            ),
            pytest.param(["TOP", None], "is not driven at X0Y1", id="undriven"),
        ],
    )
    def test_missing_partner_is_reported(
        self, make_fabric: Callable[..., Fabric], names: list[str | None], problem: str
    ) -> None:
        (message,) = self._column(make_fabric, names).routing_channel_problems()
        assert problem in message

    def test_bitstream_spec_needs_consistent_channels(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """The fabric loads, but outputs needing the routing graph refuse it."""
        fabric = self._column(make_fabric, [None, "BOT"])

        with pytest.raises(InvalidFabricDefinition, match="has nothing receiving"):
            generateBitstreamSpec(fabric)

    def test_spanning_channel_shifts_on_the_hop(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """A two-tile line passes bit 1 through and shifts the hop by one."""
        lines = {
            "TOP": "NORTH,NULL,0,-2,B,1",
            "MID": "NORTH,A,0,-2,B,1",
            "BOT": "NORTH,A,0,-2,NULL,1",
        }
        tiles = {n: make_empty_tile(n, parse_port_line(lines[n])[0]) for n in lines}
        fabric = make_fabric(tile=[[tiles["TOP"]], [tiles["MID"]], [tiles["BOT"]]])
        fabric.check_routing_channels()

        def named(instance: TileInstance) -> set[tuple]:
            return {
                (c.source[0].y, c.source[1].name(), c.sink[0].y, c.sink[1].name())
                for c in fabric.fixed_connections(instance)
            }

        assert named(fabric.instances[1][0]) == {
            (1, "B1", 1, "A1"),  # pass-through inside the tile
            (1, "A0", 0, "B1"),  # the matrix bit lands on the far slice
            (1, "A1", 0, "B0"),
        }
        # A start tap drives every slice one to one.
        assert named(fabric.instances[2][0]) == {(2, "A0", 1, "B0"), (2, "A1", 1, "B1")}

    def test_fabric_loads_and_warns(
        self,
        make_fabric: Callable[..., Fabric],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An inconsistent fabric still loads; its problems are logged."""
        self._column(make_fabric, [None, "BOT"])

        assert "has nothing receiving it at X0Y0" in caplog.text
