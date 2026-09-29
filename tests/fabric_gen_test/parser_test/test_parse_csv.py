"""Tests for parsing tile port lines from CSV fabric definitions."""

from pathlib import Path

import pytest

from fabulous.custom_exception import (
    InvalidFabricParameter,
    InvalidPortType,
    InvalidSupertileDefinition,
)
from fabulous.fabric_definition.define import IO, ConfigBitMode, Direction, Side
from fabulous.fabric_definition.tile import Tile
from fabulous.fabric_generator.parser.parse_csv import (
    parse_port_line,
    parse_tile_from_dir,
    parseFabricCSV,
    parseSupertilesCSV,
    parseTilesCSV,
)
from fabulous.fabulous_settings import init_context
from tests.conftest import make_empty_tile, sjump_port

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
        ports, _ = parse_port_line(f"{kind},N1BEG,0,-1,N1END,4")

        assert len(ports) == 2
        output, input_ = ports

        assert output.io_direction is IO.OUTPUT
        assert output.name == "N1BEG"
        assert output.side_of_tile is startSide

        assert input_.io_direction is IO.INPUT
        assert input_.name == "N1END"
        assert input_.side_of_tile is endSide
        # Both ports share the line's declaration.
        assert output.declaration is input_.declaration

    @pytest.mark.parametrize("kind", [c[0] for c in DIRECTIONAL_CASES])
    def test_shared_attributes_carry_through(self, kind: str) -> None:
        ports, _ = parse_port_line(f"{kind},N2BEG,0,-2,N2END,8")

        for port in ports:
            assert port.wire_direction is Direction[kind]
            assert port.source_name == "N2BEG"
            assert port.destination_name == "N2END"
            assert port.x_offset == 0
            assert port.y_offset == -2
            assert port.wire_count == 8

    @pytest.mark.parametrize(
        ("line", "name", "io", "ends"),
        [
            ("SOUTH,S4BEG,0,4,NULL,4", "S4BEG", IO.OUTPUT, ("S4BEG", None)),
            ("NORTH,NULL,0,-4,N4END,4", "N4END", IO.INPUT, (None, "N4END")),
        ],
    )
    def test_null_end_declares_no_port(
        self, line: str, name: str, io: IO, ends: tuple[str | None, str | None]
    ) -> None:
        """A NULL end is the far side of the wire, not an interface of the tile."""
        ports, _ = parse_port_line(line)

        assert [(p.name, p.io_direction) for p in ports] == [(name, io)]
        (port,) = ports
        assert (port.declaration.begin, port.declaration.end) == ends

    def test_both_ends_null_is_an_error(self) -> None:
        """A line with no named end declares neither a port nor a wire."""
        with pytest.raises(InvalidPortType, match="both NULL"):
            parse_port_line("SOUTH,NULL,0,4,NULL,4")


class TestJumpPorts:
    """JUMP lines stay within a tile: no tile ports, one jump wire."""

    def test_jump_wire_with_switch_matrix_ports(self) -> None:
        ports, jump = parse_port_line("JUMP,J_SR_BEG,0,0,J_SR_END,2")

        assert ports == []
        assert jump is not None
        assert jump.source is not None
        assert jump.destination is not None
        assert jump.source.io_direction is IO.OUTPUT
        assert jump.destination.io_direction is IO.INPUT
        assert [p.name() for p in jump.source.pins] == ["J_SR_BEG0", "J_SR_BEG1"]
        assert [p.name() for p in jump.destination.pins] == ["J_SR_END0", "J_SR_END1"]

    def test_null_source_declares_a_constant(self) -> None:
        _, jump = parse_port_line("JUMP,NULL,0,0,GND,1")

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
        ports, jump = parse_port_line(line)
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


def _fresh_dsp_tiles(project: Path) -> dict[str, Tile]:
    """Parse the DSP subtiles anew, free to join another supertile."""
    tiles = {}
    for name in ("DSP_top", "DSP_bot"):
        (tile,) = parseTilesCSV(project / "Tile" / "DSP" / name / f"{name}.csv")
        tiles[name] = tile
    return tiles


def _write_bel_supertile(project: Path, bel_line: str) -> Path:
    """Write a supertile `ST` over `DSP_bot` with the BEL line, and its matrix."""
    dsp = project / "Tile" / "DSP"
    (dsp / "ST_switch_matrix.v").write_text(
        "// NumberOfConfigBits: 0\nmodule ST_switch_matrix(); endmodule\n"
    )
    csv = dsp / "ST.csv"
    csv.write_text(
        f"SuperTILE,ST\nDSP_bot\n{bel_line}\nMATRIX,./ST_switch_matrix.v\n"
        "EndSuperTILE\n"
    )
    return csv


@pytest.mark.parametrize("prefix", ["", "MUL_"])
def test_supertile_bel_add_as_custom_prim(project: Path, prefix: str) -> None:
    """A supertile BEL flagged `ADD_AS_CUSTOM_PRIM` lands in `custom_prims.v`."""
    init_context(project)
    prims = project / "user_design" / "custom_prims.v"
    prims.unlink(missing_ok=True)
    csv = _write_bel_supertile(
        project, f"BEL,./DSP_bot/MULADD.v,{prefix},ADD_AS_CUSTOM_PRIM"
    )

    (supertile,) = parseSupertilesCSV(csv, _fresh_dsp_tiles(project))

    assert supertile.bels[0].prefix == prefix
    assert "module MULADD" in prims.read_text()


def test_tile_prefix_add_as_custom_prim_warns(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`ADD_AS_CUSTOM_PRIM` in a tile BEL's prefix field warns and still applies."""
    init_context(project)
    prims = project / "user_design" / "custom_prims.v"
    prims.unlink(missing_ok=True)
    csv = project / "Tile" / "DSP" / "DSP_bot" / "DSP_bot.csv"
    csv.write_text(
        csv.read_text().replace(
            "BEL,./MULADD.v,", "BEL,./MULADD.v,ADD_AS_CUSTOM_PRIM", 1
        )
    )

    (tile,) = parseTilesCSV(csv)

    assert "ADD_AS_CUSTOM_PRIM in the prefix field" in caplog.text
    assert tile.bels[0].prefix == ""
    assert "module MULADD" in prims.read_text()


def test_supertile_prefix_add_as_custom_prim_warns(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`ADD_AS_CUSTOM_PRIM` in a supertile BEL's prefix field warns and applies."""
    init_context(project)
    prims = project / "user_design" / "custom_prims.v"
    prims.unlink(missing_ok=True)
    csv = _write_bel_supertile(project, "BEL,./DSP_bot/MULADD.v,ADD_AS_CUSTOM_PRIM")

    (supertile,) = parseSupertilesCSV(csv, _fresh_dsp_tiles(project))

    assert "ADD_AS_CUSTOM_PRIM in the prefix field" in caplog.text
    assert supertile.bels[0].prefix == ""
    assert "module MULADD" in prims.read_text()


class TestConfigMemLocation:
    """Where a parsed tile expects its `ConfigMem.csv` to be."""

    @pytest.mark.parametrize(
        ("row", "expected"),
        [
            pytest.param(None, "LUT4AB_ConfigMem.csv", id="derived"),
            pytest.param(
                "CONFIGMEM,./elsewhere/Mapping.csv",
                "elsewhere/Mapping.csv",
                id="declared",
            ),
        ],
    )
    def test_the_tile_names_its_mapping_file(
        self, project: Path, row: str | None, expected: str
    ) -> None:
        """A CONFIGMEM line wins; without one the mapping sits beside the tile."""
        tile_csv = project / "Tile/LUT4AB/LUT4AB.csv"
        text = tile_csv.read_text()
        if row is None:
            text = "\n".join(
                line for line in text.splitlines() if not line.startswith("CONFIGMEM,")
            )
        else:
            text = "\n".join(
                row if line.startswith("CONFIGMEM,") else line
                for line in text.splitlines()
            )
        tile_csv.write_text(text + "\n")

        init_context(project)
        fabric = parseFabricCSV(str(project / "fabric.csv"))

        source = fabric.tileDic["LUT4AB"].config_mem.source
        assert source is not None
        assert source == (project / "Tile/LUT4AB" / expected).absolute()


@pytest.mark.parametrize(
    ("mode", "maps_bits"),
    [("frame_based", True), ("FlipFlopChain", False)],
    ids=["frame_based", "flip_flop_chain"],
)
def test_only_a_frame_based_fabric_maps_its_configuration_bits(
    project: Path, mode: str, maps_bits: bool
) -> None:
    """A chain fabric's tiles keep an empty memory."""
    fabric_csv = project / "fabric.csv"
    fabric_csv.write_text(
        fabric_csv.read_text().replace(
            "ConfigBitMode,frame_based,", f"ConfigBitMode,{mode},", 1
        )
    )

    init_context(project)
    fabric = parseFabricCSV(str(fabric_csv))

    assert (fabric.tileDic["LUT4AB"].config_mem.config_bits > 0) is maps_bits


def test_a_supertile_declared_in_the_fabric_csv_maps_into_its_own_directory(
    project: Path,
) -> None:
    """A supertile declared in `fabric.csv` expects its mapping beside its children."""
    init_context(project)
    fabric_csv = project / "fabric.csv"
    fabric_csv.write_text(
        fabric_csv.read_text()
        + "\nSuperTILE,DSP_INLINE,\nDSP_top,\nDSP_bot,\nEndSuperTILE,\n"
    )

    # A tile type joins one supertile only: take subtiles not yet in `DSP`.
    (super_tile,) = parseSupertilesCSV(fabric_csv, _fresh_dsp_tiles(project))

    assert super_tile.config_mem.source.parent == project / "Tile" / "DSP"


def test_a_chain_tile_parsed_from_its_directory_ignores_a_stale_mapping(
    project: Path,
) -> None:
    """A chain tile flow ignores a mapping left by a frame-based run."""
    tile_dir = project / "Tile" / "LUT4AB"
    (tile_dir / "LUT4AB_ConfigMem.csv").write_text("this is not a mapping\n")

    init_context(project)
    tile = parse_tile_from_dir(
        tile_dir,
        "LUT4AB",
        False,
        config_bit_mode=ConfigBitMode.FLIPFLOP_CHAIN,
        frame_bits_per_row=32,
        max_frames_per_col=20,
    )

    assert tile.config_mem.config_bits == 0


class TestParseSupertiles:
    """Supertile definitions are checked when they are parsed."""

    @pytest.mark.parametrize("with_sjump", [False, True])
    def test_sjump_wires_need_a_supertile_matrix(
        self, tmp_path: Path, with_sjump: bool
    ) -> None:
        """SJUMP wires lead to the supertile matrix, so one must be declared."""
        ports = [sjump_port("A", IO.OUTPUT)] if with_sjump else []
        bot = make_empty_tile("DSP_bot", sjump_ports=ports, pinOrderConfig={})
        csv = tmp_path / "DSP.csv"
        csv.write_text("SuperTILE,DSP\nDSP_bot\nEndSuperTILE\n")

        if with_sjump:
            with pytest.raises(InvalidSupertileDefinition, match="no MATRIX line"):
                parseSupertilesCSV(csv, {"DSP_bot": bot})
        else:
            (super_tile,) = parseSupertilesCSV(csv, {"DSP_bot": bot})
            assert super_tile.switch_matrix is None

    def test_bels_need_a_supertile_matrix(self, tmp_path: Path) -> None:
        """Supertile BELs connect to the supertile matrix, so one must be declared."""
        (tmp_path / "ADD.v").write_text(
            "module ADD (input A, output Q);\n    assign Q = A;\nendmodule\n"
        )
        bot = make_empty_tile("DSP_bot", pinOrderConfig={})
        csv = tmp_path / "DSP.csv"
        csv.write_text("SuperTILE,DSP\nBEL,./ADD.v\nDSP_bot\nEndSuperTILE\n")

        with pytest.raises(InvalidSupertileDefinition, match="has BELs, but no MATRIX"):
            parseSupertilesCSV(csv, {"DSP_bot": bot})
