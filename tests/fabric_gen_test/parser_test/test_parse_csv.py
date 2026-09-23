"""Tests for parsing tile port lines from CSV fabric definitions."""

from pathlib import Path

import pytest

from fabulous.custom_exception import InvalidFabricParameter, InvalidPortType
from fabulous.fabric_definition.define import IO, Direction, Side
from fabulous.fabric_generator.parser.parse_csv import parse_port_line, parseFabricCSV
from fabulous.fabulous_settings import init_context

# (kind, physical side of the OUTPUT/start port, physical side of the INPUT/end port)
DIRECTIONAL_CASES = [
    ("NORTH", Side.NORTH, Side.SOUTH),
    ("SOUTH", Side.SOUTH, Side.NORTH),
    ("EAST", Side.EAST, Side.WEST),
    ("WEST", Side.WEST, Side.EAST),
]


class TestDirectionalPorts:
    """NORTH/SOUTH/EAST/WEST lines produce an OUTPUT/INPUT port pair."""

    @pytest.mark.parametrize(("kind", "startSide", "endSide"), DIRECTIONAL_CASES)
    def test_two_ports_with_expected_io_and_sides(
        self, kind: str, startSide: Side, endSide: Side
    ) -> None:
        ports, _, commonWirePair = parse_port_line(f"{kind},N1BEG,0,-1,N1END,4")

        assert len(ports) == 2
        output, input_ = ports

        assert output.io_direction is IO.OUTPUT
        assert output.name == "N1BEG"
        assert output.side_of_tile is startSide

        assert input_.io_direction is IO.INPUT
        assert input_.name == "N1END"
        assert input_.side_of_tile is endSide

        assert commonWirePair == ("N1BEG", "N1END")

    @pytest.mark.parametrize("kind", [c[0] for c in DIRECTIONAL_CASES])
    def test_shared_attributes_carry_through(self, kind: str) -> None:
        ports, _, _ = parse_port_line(f"{kind},N2BEG,0,-2,N2END,8")

        for port in ports:
            assert port.wire_direction is Direction[kind]
            assert port.source_name == "N2BEG"
            assert port.destination_name == "N2END"
            assert port.x_offset == 0
            assert port.y_offset == -2
            assert port.wire_count == 8

    @pytest.mark.parametrize(
        ("line", "name", "io", "pair"),
        [
            ("SOUTH,S4BEG,0,4,NULL,4", "S4BEG", IO.OUTPUT, ("S4BEG", "NULL")),
            ("NORTH,NULL,0,-4,N4END,4", "N4END", IO.INPUT, ("NULL", "N4END")),
        ],
    )
    def test_null_end_declares_no_port(
        self, line: str, name: str, io: IO, pair: tuple[str, str]
    ) -> None:
        """A NULL end is the far side of the wire, not an interface of the tile."""
        ports, _, commonWirePair = parse_port_line(line)

        assert [(p.name, p.io_direction) for p in ports] == [(name, io)]
        assert commonWirePair == pair

    def test_both_ends_null_is_an_error(self) -> None:
        """A line with no named end declares neither a port nor a wire."""
        with pytest.raises(InvalidPortType, match="both NULL"):
            parse_port_line("SOUTH,NULL,0,4,NULL,4")


class TestJumpPorts:
    """JUMP lines stay within a tile: no tile ports, one jump wire."""

    def test_jump_wire_with_switch_matrix_ports(self) -> None:
        ports, jump, commonWirePair = parse_port_line("JUMP,J_SR_BEG,0,0,J_SR_END,2")

        assert ports == []
        assert commonWirePair is None
        assert jump is not None
        assert jump.source is not None
        assert jump.destination is not None
        assert jump.source.io_direction is IO.OUTPUT
        assert jump.destination.io_direction is IO.INPUT
        assert [p.name() for p in jump.source.pins] == ["J_SR_BEG0", "J_SR_BEG1"]
        assert [p.name() for p in jump.destination.pins] == ["J_SR_END0", "J_SR_END1"]

    def test_null_source_declares_a_constant(self) -> None:
        _, jump, _ = parse_port_line("JUMP,NULL,0,0,GND,1")

        assert jump is not None
        assert jump.source is None
        assert jump.destination is not None
        assert jump.destination[0].name() == "GND0"
        assert jump.wire_count == 1


class TestUnknownPortType:
    """Lines that are not a known wire direction are rejected."""

    @pytest.mark.parametrize("kind", ["BEL", "MATRIX", "north", "FOO"])
    def test_raises_invalid_port_type(self, kind: str) -> None:
        with pytest.raises(InvalidPortType, match="Unknown port type"):
            parse_port_line(f"{kind},SRC_BEG,0,0,DST_END,1")


class TestPortNameTrailingDigit:
    """A declared port name must not end in a digit.

    Trailing digits are reserved for the index that wire expansion appends
    (``N1BEG`` -> `N1BEG0`, `N1BEG1` ...). A declared name that already
    ends in a digit (e.g. ``X0_Y4_2_X0_Y3``) becomes ambiguous once the index
    is appended, because downstream the trailing digits are read back as a bit
    index. Reject such names at the parsing boundary.
    """

    @pytest.mark.parametrize("kind", ["NORTH", "SOUTH", "EAST", "WEST", "JUMP"])
    def test_source_name_ending_in_digit_raises(self, kind: str) -> None:
        line = f"{kind},X0_Y4_2_X0_Y3,0,0,DST_END,1"
        with pytest.raises(InvalidPortType, match="digit"):
            parse_port_line(line)

    @pytest.mark.parametrize("kind", ["NORTH", "SOUTH", "EAST", "WEST", "JUMP"])
    def test_destination_name_ending_in_digit_raises(self, kind: str) -> None:
        line = f"{kind},SRC_BEG,0,0,X0_Y4_2_X0_Y3,1"
        with pytest.raises(InvalidPortType, match="digit"):
            parse_port_line(line)

    def test_single_trailing_digit_raises(self) -> None:
        with pytest.raises(InvalidPortType, match="digit"):
            parse_port_line("JUMP,BUS3,0,0,J_END,1")

    @pytest.mark.parametrize(
        "line",
        [
            "JUMP,J_SR_BEG,0,0,J_SR_END,1",
            "NORTH,N1BEG,0,-1,N1END,4",
            "SOUTH,S4BEG,0,4,NULL,4",
            "NORTH,NULL,0,-1,N1END,4",
        ],
    )
    def test_valid_names_do_not_raise(self, line: str) -> None:
        ports, jump, _ = parse_port_line(line)
        assert ports or jump


class TestUserCLKDirection:
    """`UserCLKDirection` in the Parameters block sets `Fabric.userCLKSide`."""

    @staticmethod
    def _set_direction(project: Path, value: str) -> Path:
        csv = project / "fabric.csv"
        csv.write_text(
            csv.read_text().replace(
                "ParametersBegin,", f"ParametersBegin,\nUserCLKDirection,{value},", 1
            )
        )
        return csv

    @pytest.mark.parametrize(
        ("value", "side"),
        [
            ("S2N", Side.SOUTH),
            ("N2S", Side.NORTH),
            ("W2E", Side.WEST),
            ("E2W", Side.EAST),
        ],
    )
    def test_direction_maps_to_entry_side(
        self, project: Path, value: str, side: Side
    ) -> None:
        init_context(project)
        fabric = parseFabricCSV(str(self._set_direction(project, value)))
        assert fabric.userCLKSide is side

    def test_default_is_south(self, project: Path) -> None:
        init_context(project)
        assert parseFabricCSV(str(project / "fabric.csv")).userCLKSide is Side.SOUTH

    def test_invalid_direction_raises(self, project: Path) -> None:
        init_context(project)
        with pytest.raises(InvalidFabricParameter, match="UP"):
            parseFabricCSV(str(self._set_direction(project, "UP")))
