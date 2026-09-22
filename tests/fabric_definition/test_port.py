"""Unit tests for the Port class hierarchy introduced by the bel/port migration."""

import pytest

from fabulous.fabric_definition.define import (
    IO,
    Direction,
    FeatureType,
    FeatureValue,
    Side,
)
from fabulous.fabric_definition.port import (
    BelPort,
    ConfigPort,
    Pin,
    Port,
    SharedPort,
    SJumpPort,
    SwitchMatrixPort,
    TilePort,
)
from tests.conftest import sjump_port


class TestPort:
    """Tests for the base Port class."""

    def test_construct_valid(self) -> None:
        """A valid Port exposes its name, direction and width."""
        port = Port(name="A", io_direction=IO.INPUT, width=4)
        assert port.name == "A"
        assert port.io_direction == IO.INPUT
        assert port.width == 4

    def test_zero_width_raises(self) -> None:
        """A non-positive width is rejected."""
        with pytest.raises(ValueError, match="Width must be greater than 0"):
            Port(name="A", io_direction=IO.INPUT, width=0)

    def test_bad_io_direction_raises(self) -> None:
        """A non-IO io_direction is rejected."""
        with pytest.raises(TypeError):
            Port(name="A", io_direction="INPUT", width=1)

    def test_non_string_name_raises(self) -> None:
        """A non-string name is rejected."""
        with pytest.raises(TypeError):
            Port(name=123, io_direction=IO.INPUT, width=1)

    @pytest.mark.parametrize(
        ("io_direction", "is_input", "is_output", "is_inout"),
        [
            (IO.INPUT, True, False, False),
            (IO.OUTPUT, False, True, False),
            (IO.INOUT, False, False, True),
        ],
    )
    def test_direction_predicates(
        self, io_direction: IO, is_input: bool, is_output: bool, is_inout: bool
    ) -> None:
        """Exactly one direction predicate holds for each IO direction."""
        port = Port(name="A", io_direction=io_direction, width=1)
        assert port.is_input is is_input
        assert port.is_output is is_output
        assert port.is_inout is is_inout

    @pytest.mark.parametrize(
        ("name", "expected"), [("NULL", True), ("N1BEG", False), ("null", False)]
    )
    def test_name_is_null(self, name: str, expected: bool) -> None:
        """Only the exact NULL placeholder name marks an unconnected wire end."""
        port = Port(name=name, io_direction=IO.INPUT, width=1)
        assert port.name_is_null is expected

    def test_expand_single_bit(self) -> None:
        """A width-1 port expands to a single bare name."""
        assert Port(name="x", io_direction=IO.INPUT, width=1).expand() == ["x"]

    def test_pins_are_hashable_bit_handles(self) -> None:
        """Every bit is a value-equal Pin that names itself in both spellings."""
        port = Port(name="A", io_direction=IO.INPUT, width=3)
        assert port[1] == Pin(port, 1)
        assert port[1] is port.pins[1]
        assert len({port[1], Pin(port, 1), port[2]}) == 2
        assert port[1].name() == "A1"
        assert port[1].name(indexed=True, prefix="p_") == "p_A[1]"
        assert port.expand() == [pin.name(indexed=True) for pin in port.pins]

    def test_expand_multi_bit(self) -> None:
        """A multi-bit port expands to indexed names."""
        assert Port(name="y", io_direction=IO.OUTPUT, width=3).expand() == [
            "y[0]",
            "y[1]",
            "y[2]",
        ]

    def test_equality_is_identity(self) -> None:
        """Two distinct ports with equal data are not equal; identity holds."""
        p1 = Port(name="A", io_direction=IO.INPUT, width=1)
        p2 = Port(name="A", io_direction=IO.INPUT, width=1)
        assert p1 != p2
        assert p1 == p1
        assert hash(p1) == id(p1)

    def test_serialize(self) -> None:
        """Serialization contains name, io_direction value, width and net info."""
        port = Port(name="A", io_direction=IO.OUTPUT, width=2)
        assert port.serialize() == {
            "name": "A",
            "io_direction": IO.OUTPUT.value,
            "width": 2,
            "is_clock": False,
            "is_global": False,
            "net": "",
        }


class TestPortClockFields:
    """Tests for the clock and net metadata carried by every Port."""

    def test_defaults_are_a_local_non_clock_port(self) -> None:
        """A Port is a non-clock, non-global port on the global net by default."""
        port = Port(name="A", io_direction=IO.INPUT, width=1)
        assert port.is_clock is False
        assert port.is_global is False
        assert port.net == ""

    def test_user_clock_fields(self) -> None:
        """The global user clock is described by the base Port fields."""
        port = Port(
            name="UserCLK",
            io_direction=IO.INPUT,
            width=1,
            is_clock=True,
            is_global=True,
        )
        assert port.is_clock is True
        assert port.is_global is True
        assert port.net == ""

    def test_local_clock_on_a_named_net(self) -> None:
        """A locally generated clock names the net it drives."""
        port = Port(
            name="clk2",
            io_direction=IO.INPUT,
            width=1,
            is_clock=True,
            net="dsp",
        )
        assert port.is_clock is True
        assert port.is_global is False
        assert port.net == "dsp"

    @pytest.mark.parametrize("field", ["is_clock", "is_global"])
    def test_non_bool_flag_raises(self, field: str) -> None:
        """A non-bool clock flag is rejected."""
        with pytest.raises(TypeError, match=f"{field} must be a bool"):
            Port(name="A", io_direction=IO.INPUT, width=1, **{field: "yes"})

    def test_serialize_includes_clock_fields(self) -> None:
        """Serialization carries is_clock, is_global and net."""
        port = Port(
            name="clk_fast",
            io_direction=IO.INPUT,
            width=1,
            is_clock=True,
            net="fast",
        )
        assert port.serialize() == {
            "name": "clk_fast",
            "io_direction": IO.INPUT.value,
            "width": 1,
            "is_clock": True,
            "is_global": False,
            "net": "fast",
        }


class TestBelPort:
    """Tests for BelPort."""

    def test_name_prepends_prefix(self) -> None:
        """The BelPort name is the prefix concatenated with the base name."""
        port = BelPort(name="sig", io_direction=IO.INPUT, width=1, prefix="lut_")
        assert port.name == "lut_sig"

    def test_external_and_control_flags(self) -> None:
        """External and control flags are exposed verbatim."""
        port = BelPort(
            name="io",
            io_direction=IO.OUTPUT,
            width=1,
            prefix="",
            external=True,
            control=False,
        )
        assert port.external is True
        assert port.control is False

    def test_expand_uses_prefixed_name(self) -> None:
        """Expansion uses the prefixed name."""
        port = BelPort(name="sig", io_direction=IO.INPUT, width=1, prefix="lut_")
        assert port.expand() == ["lut_sig"]

    def test_clock_fields_reach_the_base_port(self) -> None:
        """Clock metadata passed to a BelPort is stored on the base Port."""
        port = BelPort(
            name="UserCLK",
            io_direction=IO.INPUT,
            width=1,
            is_clock=True,
            net="UserCLK",
        )
        assert port.is_clock is True
        assert port.is_global is False
        assert port.net == "UserCLK"

    def test_serialize_includes_belport_fields(self) -> None:
        """Serialization adds prefix, external and control."""
        port = BelPort(name="sig", io_direction=IO.INPUT, width=1, prefix="lut_")
        data = port.serialize()
        assert data["name"] == "lut_sig"
        assert data["prefix"] == "lut_"
        assert data["external"] is False
        assert data["control"] is False


class TestConfigPort:
    """Tests for ConfigPort."""

    def test_defaults(self) -> None:
        """Features default to empty and feature_type to ENUMERATE."""
        port = ConfigPort(name="cfg", io_direction=IO.INPUT, width=8)
        assert port.features == []
        assert port.feature_type == FeatureType.ENUMERATE

    def test_custom_features(self) -> None:
        """Custom features are stored as given."""
        features = [FeatureValue("INIT", 0), FeatureValue("MODE", None)]
        port = ConfigPort(
            name="cfg",
            io_direction=IO.INPUT,
            width=2,
            features=features,
        )
        assert port.features == features


class TestSharedPort:
    """Tests for SharedPort."""

    def test_shared_with(self) -> None:
        """The shared_with target is exposed."""
        port = SharedPort(
            name="clk", io_direction=IO.INPUT, width=1, shared_with="global_clk"
        )
        assert port.shared_with == "global_clk"

    def test_share_expand_single_bit(self) -> None:
        """A width-1 shared port expands to the bare shared_with name."""
        port = SharedPort(
            name="clk", io_direction=IO.INPUT, width=1, shared_with="global_clk"
        )
        assert port.share_expand() == ["global_clk"]

    def test_share_expand_multi_bit(self) -> None:
        """A multi-bit shared port expands to indexed shared_with names."""
        port = SharedPort(
            name="bus", io_direction=IO.INPUT, width=3, shared_with="shared_bus"
        )
        assert port.share_expand() == [
            "shared_bus[0]",
            "shared_bus[1]",
            "shared_bus[2]",
        ]


class TestTilePort:
    """Tests for TilePort ordering and construction."""

    def test_construct_with_side(self) -> None:
        """A TilePort exposes its side of the tile."""
        port = TilePort(name="N1", io_direction=IO.OUTPUT, side_of_tile=Side.NORTH)
        assert port.side_of_tile == Side.NORTH

    def test_ordering_by_side(self) -> None:
        """Ports are ordered by tile side (north before east)."""
        north = TilePort(name="n", io_direction=IO.OUTPUT, side_of_tile=Side.NORTH)
        east = TilePort(name="e", io_direction=IO.INPUT, side_of_tile=Side.EAST)
        assert north < east
        assert east > north

    def test_ordering_by_io_within_side(self) -> None:
        """Within a side, outputs are ordered before inputs."""
        out = TilePort(name="o", io_direction=IO.OUTPUT, side_of_tile=Side.NORTH)
        inp = TilePort(name="i", io_direction=IO.INPUT, side_of_tile=Side.NORTH)
        assert out < inp
        assert out <= inp
        assert inp >= out

    def test_comparison_with_non_tileport_raises(self) -> None:
        """Ordering is only defined against another TilePort."""
        port = TilePort(name="n", io_direction=IO.OUTPUT, side_of_tile=Side.NORTH)
        with pytest.raises(TypeError, match="Cannot compare"):
            port < 1  # noqa: B015

    def test_tile_back_reference_defaults_to_none(self) -> None:
        """An unattached port has no owning tile."""
        port = TilePort(name="n", io_direction=IO.OUTPUT, side_of_tile=Side.NORTH)
        assert port.tile is None


def make_wire_port(
    x_offset: int, y_offset: int, wire_count: int, source: str, destination: str
) -> TilePort:
    """Build the OUTPUT side of a CSV wire line for the pin-slice tests."""
    direction = Direction.NORTH if x_offset or y_offset else Direction.JUMP
    return TilePort(
        name=source if source != "NULL" else destination,
        io_direction=IO.OUTPUT,
        side_of_tile=Side.NORTH,
        wire_direction=direction,
        source_name=source,
        x_offset=x_offset,
        y_offset=y_offset,
        destination_name=destination,
        wire_count=wire_count,
    )


class TestTilePortPins:
    """`width` is the HDL vector; `sm_pins`/`top_pins` are its two slices."""

    @pytest.mark.parametrize(
        ("x_offset", "y_offset", "wire_count", "width"),
        [(0, 0, 1, 1), (0, 0, 4, 4), (0, 1, 4, 4), (0, 2, 4, 8), (3, 0, 2, 6)],
    )
    def test_width_is_wire_count_times_distance(
        self, x_offset: int, y_offset: int, wire_count: int, width: int
    ) -> None:
        """A spanning wire occupies one `wire_count` slice per hop it crosses."""
        port = make_wire_port(x_offset, y_offset, wire_count, "NBEG", "NEND")
        assert port.width == width
        assert len(port.pins) == width

    @pytest.mark.parametrize(
        ("source", "destination", "x_offset", "sm", "top"),
        [
            # named both ends: SM sees the first slice, the top level the last
            ("NBEG", "NEND", 2, [0, 1], [2, 3]),
            # NULL-terminated: every slice is local, so both see all of it
            ("NBEG", "NULL", 2, [0, 1, 2, 3], [0, 1, 2, 3]),
            # NULL-sourced JUMP: a dangling wire declares no port at all
            ("NULL", "GND", 0, [], []),
        ],
    )
    def test_pin_slices(
        self, source: str, destination: str, x_offset: int, sm: list, top: list
    ) -> None:
        """`sm_pins`/`top_pins` select the same bits the string expansion did."""
        port = make_wire_port(x_offset, 0, 2, source, destination)
        assert [pin.index for pin in port.sm_pins] == sm
        assert [pin.index for pin in port.top_pins] == top
        assert port.expand_port_info_by_name() == [f"{port.name}{i}" for i in sm]
        assert port.expand_port_info_by_name_top(indexed=True, escape=True) == [
            rf"{port.name}\[{i}\]" for i in top
        ]

    def test_sjump_exposes_all_pins(self) -> None:
        """An SJUMP port has zero offset and is fully visible on both sides."""
        port = sjump_port("J", IO.OUTPUT, wire_count=3)
        assert port.sm_pins == port.pins
        assert port.top_pins == port.pins
        assert port.expand_port_info_by_name_top() == ["J0", "J1", "J2"]


class TestSwitchMatrixPort:
    """A matrix port names its pins after the tile pins it wires to."""

    def test_tile_port_gives_width_origin_and_flat_names(self) -> None:
        """Width is the tile port's `sm_pins` count; pins are the port's own."""
        tile_port = make_wire_port(2, 0, 2, "NBEG", "NEND")
        sm_port = SwitchMatrixPort.from_tile_port(tile_port, prefix="T_")
        assert sm_port.origin is tile_port
        assert sm_port.width == len(tile_port.sm_pins) == 2
        assert sm_port[1] == Pin(sm_port, 1)
        assert sm_port[1] != tile_port[1]
        assert [pin.name() for pin in sm_port.pins] == ["T_NBEG0", "T_NBEG1"]
        assert sm_port[1].name(indexed=True) == "T_NBEG[1]"

    def test_bel_port_is_a_bare_scalar(self) -> None:
        """A literal (BEL / constant) port keeps its name as written."""
        sm_port = SwitchMatrixPort("A_I0", IO.OUTPUT, literal=True)
        assert sm_port.origin is None
        assert sm_port.width == 1
        assert sm_port[0].name() == "A_I0"
        assert sm_port[0] == Pin(sm_port, 0)


class TestSJumpPort:
    """An SJUMP port has no offset and no spanning expansion."""

    @pytest.mark.parametrize("io", [IO.OUTPUT, IO.INPUT])
    def test_every_pin_faces_both_matrices(self, io: IO) -> None:
        """Width is the wire count; the matrix and top level see all of it."""
        port = SJumpPort("A", io, 4)
        assert port.width == 4
        assert port.sm_pins == port.pins
        assert port.top_pins == port.pins
        assert port.x_offset == 0
        assert port.y_offset == 0
        assert port.side_of_tile is Side.ANY
        assert port.wire_direction is Direction.SJUMP

    def test_one_way_naming(self) -> None:
        """Only the driven end carries the name; the other is NULL."""
        out = SJumpPort("A", IO.OUTPUT, 1)
        assert (out.source_name, out.destination_name) == ("A", "NULL")
        inp = SJumpPort("Q", IO.INPUT, 1)
        assert (inp.source_name, inp.destination_name) == ("NULL", "Q")
