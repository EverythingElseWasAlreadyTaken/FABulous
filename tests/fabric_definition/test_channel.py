"""Tests for routing channel declarations and their fabric-wide resolution."""

import pickle
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest

from fabulous.custom_exception import InvalidFabricDefinition
from fabulous.fabric_cad.gen_bitstream_spec import generateBitstreamSpec
from fabulous.fabric_definition.bel import Bel
from fabulous.fabric_definition.channel import (
    ChannelDeclaration,
    RoutingChannel,
    resolve_channels,
)
from fabulous.fabric_definition.connection import (
    JumpConnection,
    StagingConnection,
    SwitchMatrixConnection,
    TilePortConnection,
)
from fabulous.fabric_definition.define import IO, BelPortKind, Direction
from fabulous.fabric_definition.fabric import Fabric
from fabulous.fabric_definition.instance import TileInstance
from fabulous.fabric_definition.port import BelPort
from fabulous.fabric_definition.switch_matrix import SwitchMatrix, switch_matrix_ports
from fabulous.fabric_definition.tile import Tile
from fabulous.fabric_generator.parser.parse_csv import parse_port_line
from tests.conftest import make_muladd_bel
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

    @pytest.mark.parametrize(
        "names",
        [["BOT", "TOP"], ["TOP", None, "BOT"]],
        ids=["out of the grid", "into an empty cell"],
    )
    def test_dangling_line_has_no_hop(
        self, make_fabric: Callable[..., Fabric], names: list[str | None]
    ) -> None:
        """A line leading nowhere has no hop, and never wraps around the grid."""
        fabric = self._column(make_fabric, names)

        assert not any(
            isinstance(pc, TilePortConnection) for pc in fabric.port_connections
        )
        assert not any(
            isinstance(c.port_connection, TilePortConnection)
            for c in fabric.fixed_connections()
        )

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

    def test_spanning_channel_stages_inside_the_tile(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """A two-tile line shifts inside the tile; the hop keeps the index."""
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
                for c in fabric.fixed_connections()
                if c.source[0] is instance
            }

        assert [
            (hop.source[0].y, hop.source[1].name, hop.sink[0].y, hop.sink[1].name)
            for hop in fabric.port_connections
            if isinstance(hop, TilePortConnection)
        ] == [(1, "A", 0, "B"), (2, "A", 1, "B")]
        assert named(fabric.instances[1][0]) == {
            (1, "B1", 1, "A0"),  # staging: END[i + wire_count] -> BEG[i]
            (1, "A0", 0, "B0"),  # hops keep the pin index
            (1, "A1", 0, "B1"),
        }
        assert named(fabric.instances[2][0]) == {(2, "A0", 1, "B0"), (2, "A1", 1, "B1")}

    def test_switch_matrix_boundary(self) -> None:
        """Matrix pins are wired to the tile port's matrix-facing pins."""
        ports, _ = parse_port_line("NORTH,A,0,-2,B,1")
        tile = Tile(
            name="MID",
            ports=ports,
            bels=[],
            tileDir=Path(),
            switch_matrix=SwitchMatrix(
                Path(), switch_matrix_ports(ports, []), {}, "MID"
            ),
            gen_ios=[],
            pinOrderConfig={},
        )

        assert {
            (c.source.full_name(), c.sink.full_name()) for c in tile.fixed_connections
        } == {
            ("B1", "A0"),  # staging
            ("Inst_MID_switch_matrix__A0", "A1"),  # the matrix drives the last slice
            ("B0", "Inst_MID_switch_matrix__B0"),  # and reads the first one
        }

    def test_wire_names_follow_the_rtl_hierarchy(self) -> None:
        """Matrix and BEL pins carry their instance; constants and tile pins not."""
        ports, _ = parse_port_line("NORTH,A,0,-1,B,1")
        jumps = [
            parse_port_line("JUMP,J_BEG,0,0,J_END,1")[1],
            parse_port_line("JUMP,NULL,0,0,GND,1")[1],
        ]
        bel = make_muladd_bel([("SUPER_I0", IO.INPUT)], prefix="LA_")
        tile = Tile(
            name="T",
            ports=ports,
            bels=[bel],
            tileDir=Path(),
            switch_matrix=SwitchMatrix(
                Path(), switch_matrix_ports(ports, [bel], jumps), {}, "T"
            ),
            gen_ios=[],
            pinOrderConfig={},
            jump_wires=jumps,
        )

        names = {p.full_name() for port in tile.switch_matrix.ports for p in port}
        assert names == {
            "Inst_T_switch_matrix__A0",
            "Inst_T_switch_matrix__B0",
            "Inst_T_switch_matrix__SUPER_I0",
            "Inst_T_switch_matrix__J_BEG0",
            "Inst_T_switch_matrix__J_END0",
            # constants, also the source-less jump's GND0: no module ports
            *("GND", "GND0", "VCC", "VCC0", "VDD", "VDD0"),
        }
        assert bel.ports[0][0].full_name() == "Inst_LA_MULADD__SUPER_I0"
        assert ports[0][0].full_name() == "A0"

    def test_fabric_loads_and_warns(
        self,
        make_fabric: Callable[..., Fabric],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An inconsistent fabric still loads; its problems are logged."""
        self._column(make_fabric, [None, "BOT"])

        assert "has nothing receiving it at X0Y0" in caplog.text

    def test_staging_and_hops_are_port_connections(self) -> None:
        """Staging stays in a tile; a hop of the same line joins two tiles."""
        ports, _ = parse_port_line("NORTH,A,0,-2,B,1")
        tile = Tile(
            name="T",
            ports=ports,
            bels=[],
            tileDir=Path(),
            switch_matrix=SwitchMatrix(Path(), switch_matrix_ports(ports, []), {}, "T"),
            gen_ios=[],
            pinOrderConfig={},
        )
        line, *_ = tile.port_connections
        staging, *boundary = tile.fixed_connections
        here, there = TileInstance(0, 1, tile), TileInstance(0, 0, tile)
        out, into = ports
        (hop,) = TilePortConnection(
            (here, out), (there, into), line.declaration, 1
        ).pin_connections

        assert isinstance(line, StagingConnection)
        assert (line.source, line.sink, line.source_offset, line.width) == (
            into,
            out,
            1,
            1,
        )
        assert staging.port_connection is line
        assert TileInstance(0, 0, tile).fixed_connections[0].port_connection is line
        assert not any(
            isinstance(c.port_connection, StagingConnection | TilePortConnection)
            for c in boundary
        )
        assert isinstance(hop.port_connection, TilePortConnection)
        assert hop.signal == "Tile_X0Y1_A[0]"

    def test_signal_names_the_tile_rtl_signal(self) -> None:
        """A hard wire is the signal of its tile port, BEL pin or jump source."""
        ports, _ = parse_port_line("NORTH,A,0,-2,B,1")
        jumps = [parse_port_line("JUMP,J_BEG,0,0,J_END,1")[1]]
        bel = make_muladd_bel([("I0", IO.INPUT), ("O0", IO.OUTPUT)])
        tile = Tile(
            name="T",
            ports=ports,
            bels=[bel],
            tileDir=Path(),
            switch_matrix=SwitchMatrix(
                Path(), switch_matrix_ports(ports, [bel], jumps), {}, "T"
            ),
            gen_ios=[],
            pinOrderConfig={},
            jump_wires=jumps,
        )
        staging, *wires = tile.fixed_connections
        boundary = tile.port_connections[1]

        assert [type(pc) for pc in tile.port_connections] == [
            StagingConnection,
            *[SwitchMatrixConnection] * 4,
            JumpConnection,
        ]
        assert (boundary.matrix_port.name, boundary.outer.name) == ("A", "A")
        assert boundary.matrix_port in tile.switch_matrix.ports
        assert [c.signal for c in wires] == ["A[1]", "B[0]", "I0", "O0", "J_BEG[0]"]
        with pytest.raises(ValueError, match="buffer cells"):
            _ = staging.signal
        placed = TileInstance(0, 0, tile).fixed_connections[1]
        with pytest.raises(TypeError, match="no line between placed tiles"):
            _ = placed.signal

    def test_bel_external_ports_wire_external_bel_ports_out(self) -> None:
        """An EXTERNAL BEL port is wired to a tile-module port named like it."""

        def io_bel() -> Bel:
            ports = [
                BelPort("O_top", IO.OUTPUT, 1, BelPortKind.EXTERNAL, prefix="A_"),
                BelPort("C", IO.INPUT, 2, BelPortKind.EXTERNAL, prefix="A_"),
            ]
            return Bel(Path("IO.v"), "A_", "IO", ports)

        tile = make_empty_tile("T")
        tile.add_bels([io_bel()])

        assert [p.name for p in tile.bel_external_ports] == ["A_C", "A_O_top"]
        assert [
            (c.source.full_name(), c.sink.full_name(), c.signal)
            for c in tile.fixed_connections
        ] == [
            ("A_C0", "Inst_A_IO__C0", "A_C0"),
            ("A_C1", "Inst_A_IO__C1", "A_C1"),
            ("Inst_A_IO__O_top", "A_O_top", "A_O_top"),
        ]
        with pytest.raises(RuntimeError, match="already derived"):
            tile.add_bels([io_bel()])
