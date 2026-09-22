"""Unit tests for the SJUMP / supertile-BEL data model.

Covers the model pieces added to route a BEL that lives in a supertile's master
tile from its child tiles: SJUMP port expansion, `Tile.get_sjump_ports`,
`SuperTile` helpers, and the bidirectional SJUMP wire pass run by
`Fabric.__post_init__`.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from fabulous.fabric_cad.gen_bitstream_spec import generateBitstreamSpec
from fabulous.fabric_cad.gen_npnr_model import genNextpnrModel
from fabulous.fabric_definition.define import IO, Direction, Side
from fabulous.fabric_definition.fabric import Fabric
from fabulous.fabric_definition.port import TilePort
from fabulous.fabric_definition.supertile import SuperTile
from fabulous.fabric_definition.switch_matrix import (
    SwitchMatrix,
    switch_matrix_ports,
)
from fabulous.fabric_definition.tile import Tile
from tests.conftest import make_empty_tile, make_muladd_bel, sjump_port


def _tile(name: str, ports: list[TilePort]) -> Tile:
    """Build a minimal real Tile (`pinOrderConfig={}` skips the GDS import).

    These tests never read the tile's files, so the dir/matrix paths are left at
    their defaults; tests that do touch disk use the `tmp_path` fixture.
    """
    return make_empty_tile(name, ports, pinOrderConfig={})


def _pips(fabric: Fabric) -> set[str]:
    """Return the fabric's nextpnr PIP lines, with the delay column dropped."""
    pip_str, *_ = genNextpnrModel(fabric)
    return {
        ",".join(line.split(",")[:4])
        for line in pip_str.splitlines()
        if not line.startswith("#")
    }


class TestPortSJumpExpansion:
    """SJUMP ports keep their declared width instead of collapsing to zero.

    SJUMP ports have `(x_offset, y_offset) == (0, 0)`, so the Manhattan-distance
    width formula used for NULL-terminated wires would otherwise zero them out.
    """

    def test_expand_by_name_uses_wire_count(self) -> None:
        port = sjump_port("A", IO.OUTPUT, wire_count=4)
        assert port.expand_port_info_by_name() == ["A0", "A1", "A2", "A3"]

    def test_expand_by_name_indexed(self) -> None:
        port = sjump_port("A", IO.OUTPUT, wire_count=3)
        assert port.expand_port_info_by_name(indexed=True) == ["A[0]", "A[1]", "A[2]"]

    def test_expand_input_port_uses_wire_count(self) -> None:
        port = sjump_port("Q", IO.INPUT, wire_count=10)
        assert port.expand_port_info_by_name() == [f"Q{i}" for i in range(10)]

    def test_expand_by_name_top_uses_wire_count(self) -> None:
        port = sjump_port("A", IO.OUTPUT, wire_count=4)
        assert port.expand_port_info_by_name_top() == ["A0", "A1", "A2", "A3"]


class TestTileGetSJumpPorts:
    """`Tile.get_sjump_ports` returns only the SJUMP-direction, non-NULL ports."""

    def test_returns_only_sjump_ports(self) -> None:
        sjump_out = sjump_port("A", IO.OUTPUT)
        sjump_in = sjump_port("Q", IO.INPUT)
        jump = TilePort(
            name="J",
            io_direction=IO.OUTPUT,
            side_of_tile=Side.NORTH,
            wire_direction=Direction.JUMP,
            source_name="J",
            x_offset=0,
            y_offset=0,
            destination_name="J",
            wire_count=1,
        )
        normal = TilePort(
            name="N1BEG",
            io_direction=IO.OUTPUT,
            side_of_tile=Side.NORTH,
            wire_direction=Direction.NORTH,
            source_name="N1BEG",
            x_offset=0,
            y_offset=-1,
            destination_name="N1END",
            wire_count=4,
        )
        null_sjump = sjump_port("NULL", IO.OUTPUT)

        tile = _tile("DSP_bot", [sjump_out, jump, normal, sjump_in, null_sjump])

        assert tile.get_sjump_ports() == [sjump_out, sjump_in]

    def test_empty_when_no_sjump_ports(self) -> None:
        tile = _tile("LUT", [])
        assert tile.get_sjump_ports() == []


class TestSuperTileHelpers:
    """`SuperTile` master-coordinate and SJUMP-port helpers."""

    def _supertile(self, **overrides: object) -> SuperTile:
        top = _tile("DSP_top", [sjump_port("top2bot", IO.OUTPUT)])
        bot = _tile("DSP_bot", [sjump_port("A", IO.OUTPUT)])
        defaults: dict = {
            "name": "DSP",
            "tileDir": Path(),
            "tiles": [top, bot],
            "tileMap": [[top], [bot]],
        }
        defaults.update(overrides)
        return SuperTile(**defaults)

    def test_master_defaults_to_last_non_none_tile(self) -> None:
        # tileMap [[DSP_top], [DSP_bot]] -> master is DSP_bot at local (0, 1).
        assert self._supertile().get_master_tile_coords() == (0, 1)

    def test_master_explicit_override(self) -> None:
        st = self._supertile(master_tile_coords=(0, 0))
        assert st.get_master_tile_coords() == (0, 0)

    def test_master_raises_on_empty(self) -> None:
        st = self._supertile(tiles=[], tileMap=[[None]])
        with pytest.raises(ValueError, match="has no tiles"):
            st.get_master_tile_coords()

    def test_sjump_wires_pair_child_ports_with_matrix_ports(self) -> None:
        top = _tile("DSP_top", [sjump_port("top2bot", IO.OUTPUT)])
        bot = _tile(
            "DSP_bot",
            [sjump_port("A", IO.OUTPUT), sjump_port("Q", IO.INPUT)],
        )
        st = SuperTile(
            name="DSP",
            tileDir=Path(),
            tiles=[top, bot],
            tileMap=[[top], [bot]],
        )

        # One wire per child SJUMP port, in tileMap order, each carrying the
        # child's (local_x, local_y) and the {tile}_ prefixed matrix pin names.
        assert [(w.x, w.y, w.signal_name) for w in st.sjump_wires] == [
            (0, 0, "DSP_top_top2bot"),
            (0, 1, "DSP_bot_A"),
            (0, 1, "DSP_bot_Q"),
        ]
        # The matrix reads what the child drives, and vice versa.
        forward = st.forward_sjump_wires()
        assert [w.signal_name for w in forward] == ["DSP_top_top2bot", "DSP_bot_A"]
        assert all(w.matrix_port.is_input for w in forward)
        (reverse,) = st.reverse_sjump_wires()
        assert reverse.matrix_port.is_output
        assert [p.name() for p in reverse.matrix_port.pins] == [
            f"DSP_bot_Q{i}" for i in range(reverse.wire_count)
        ]

    def test_offsets_come_from_the_tile_map(self) -> None:
        """The wire spans from the child's cell to the master's, both ways."""
        top = _tile(
            "DSP_top",
            [
                sjump_port("A", IO.OUTPUT, wire_count=1),
                sjump_port("Q", IO.INPUT, wire_count=1),
            ],
        )
        bot = _tile("DSP_bot", [])
        # No MASTER token, so the master defaults to the last non-None tile.
        st = SuperTile(
            name="DSP",
            tileDir=Path(),
            tiles=[top, bot],
            tileMap=[[top], [bot]],
        )

        (forward,) = st.forward_sjump_wires()
        (reverse,) = st.reverse_sjump_wires()
        # top sits at y=0, the master bot at y=1: the matrix is one row down,
        # so the forward wire goes down and the reverse one back up.
        assert (forward.x_offset, forward.y_offset) == (0, 1)
        assert (reverse.x_offset, reverse.y_offset) == (0, -1)
        assert forward.pin_names == [("A0", "DSP_top_A0")]
        assert reverse.pin_names == [("DSP_top_Q0", "Q0")]


class TestSJumpPips:
    """SJUMP wires reach the nextpnr model as pips, in both directions.

    Layout: DSP_top (row 0) over DSP_bot (row 1), single column. DSP_bot is the
    master tile, so the supertile switch matrix lives at X0Y1. Forward pips
    carry child OUTPUT ports to it; reverse pips carry its outputs back down to
    child INPUT ports.
    """

    @pytest.fixture
    def fabric(self, make_fabric: Callable[..., Fabric]) -> Fabric:
        top = _tile(
            "DSP_top",
            [sjump_port("top2bot", IO.OUTPUT), sjump_port("bot2top", IO.INPUT)],
        )
        bot = _tile(
            "DSP_bot",
            [sjump_port("A", IO.OUTPUT), sjump_port("Q", IO.INPUT)],
        )
        supertile = SuperTile(
            name="DSP",
            tileDir=Path(),
            tiles=[top, bot],
            tileMap=[[top], [bot]],
        )
        for t in supertile.tiles:
            t.partOfSuperTile = True
        return make_fabric(
            tile=[[top], [bot]],
            superTileDic={"DSP": supertile},
        )

    def test_forward_pips_child_output_to_master(self, fabric: Fabric) -> None:
        pips = _pips(fabric)
        # DSP_top OUTPUT port jumps down to the master one row below.
        assert "X0Y0,top2bot0,X0Y1,DSP_top_top2bot0" in pips
        assert "X0Y0,top2bot1,X0Y1,DSP_top_top2bot1" in pips
        # The master's own OUTPUT port is a zero-offset self-jump.
        assert "X0Y1,A0,X0Y1,DSP_bot_A0" in pips

    def test_reverse_pips_master_to_child_input(self, fabric: Fabric) -> None:
        pips = _pips(fabric)
        # The matrix drives the master's own INPUT port back (zero offset)...
        assert "X0Y1,DSP_bot_Q0,X0Y1,Q0" in pips
        # ...and the child tile's INPUT port one row up.
        assert "X0Y1,DSP_top_bot2top0,X0Y0,bot2top0" in pips
        assert "X0Y1,DSP_top_bot2top1,X0Y0,bot2top1" in pips

    def test_no_duplicate_pips(self, fabric: Fabric) -> None:
        pip_str, *_ = genNextpnrModel(fabric)
        lines = [ln for ln in pip_str.splitlines() if not ln.startswith("#")]
        assert len(lines) == len(set(lines))


class TestSJumpRequiresSupertile:
    """SJUMP wires are only valid inside a tile that belongs to a supertile."""

    def test_sjump_tile_outside_supertile_rejected(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        lone = _tile("DSP_bot", [sjump_port("A", IO.OUTPUT)])
        with pytest.raises(ValueError, match="not part of any supertile"):
            make_fabric(tile=[[lone]])

    def test_sjump_tile_inside_supertile_accepted(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        bot = _tile("DSP_bot", [sjump_port("A", IO.OUTPUT)])
        bot.partOfSuperTile = True  # set by the parser for supertile members
        supertile = SuperTile(
            name="DSP",
            tileDir=Path(),
            tiles=[bot],
            tileMap=[[bot]],
        )
        # Must not raise.
        make_fabric(tile=[[bot]], superTileDic={"DSP": supertile})


class TestGenNpnrModelSupertile:
    """Supertile switch-matrix PIPs come out with the right source/sink.

    The `.list` convention is `<destination>,[<sources>]`; the supertile
    callers parse it with the `"source"` collect mode so the dict keys by
    destination (matching `parseMatrix`). A regression here previously emitted
    every supertile-SM PIP reversed.
    """

    @pytest.fixture
    def fabric(self, make_fabric: Callable[..., Fabric], tmp_path: Path) -> Fabric:
        # Child tiles need real (empty) switch-matrix list files.
        top_mat = tmp_path / "DSP_top_switch_matrix.list"
        bot_mat = tmp_path / "DSP_bot_switch_matrix.list"
        top_mat.write_text("# DSP_top\n")
        bot_mat.write_text("# DSP_bot\n")

        # Supertile matrix: forward "BEL_input,[source]" and reverse
        # "[reverse_wire],BEL_output".
        st_mat = tmp_path / "supertile_matrix.list"
        st_mat.write_text("SUPER_A0,[DSP_bot_A0]\n[DSP_bot_Q0],SUPER_Q0\n")

        top = make_empty_tile(
            "DSP_top",
            [sjump_port("top2bot", IO.OUTPUT)],
            tileDir=tmp_path,
            matrixDir=top_mat,
            pinOrderConfig={},
        )
        bot = make_empty_tile(
            "DSP_bot",
            [
                sjump_port("A", IO.OUTPUT, wire_count=1),
                sjump_port("Q", IO.INPUT, wire_count=1),
            ],
            tileDir=tmp_path,
            matrixDir=bot_mat,
            pinOrderConfig={},
        )
        bel = make_muladd_bel([("SUPER_A0", IO.INPUT), ("SUPER_Q0", IO.OUTPUT)])
        supertile = SuperTile(
            name="DSP",
            tileDir=tmp_path,
            tiles=[top, bot],
            tileMap=[[top], [bot]],
            bels=[bel],
        )
        supertile.switch_matrix = SwitchMatrix.from_file(
            st_mat, "DSP", supertile.switch_matrix_ports(), canonical=False
        )
        for t in supertile.tiles:
            t.partOfSuperTile = True
        return make_fabric(tile=[[top], [bot]], superTileDic={"DSP": supertile})

    def test_forward_pip_has_bel_input_as_destination(self, fabric: Fabric) -> None:
        pip_str, *_ = genNextpnrModel(fabric)
        pips = set(pip_str.splitlines())
        # source DSP_bot_A0 -> destination SUPER_A0 (BEL input is the sink).
        assert "X0Y1,DSP_bot_A0,X0Y1,SUPER_A0,8,DSP_bot_A0.SUPER_A0" in pips
        # The reversed form must NOT be present.
        assert "X0Y1,SUPER_A0,X0Y1,DSP_bot_A0,8,SUPER_A0.DSP_bot_A0" not in pips

    def test_reverse_pip_has_bel_output_as_source(self, fabric: Fabric) -> None:
        pip_str, *_ = genNextpnrModel(fabric)
        pips = set(pip_str.splitlines())
        # BEL output SUPER_Q0 -> reverse wire DSP_bot_Q0.
        assert "X0Y1,SUPER_Q0,X0Y1,DSP_bot_Q0,8,SUPER_Q0.DSP_bot_Q0" in pips

    def test_supertile_bel_emitted_in_belv2_and_belv3(
        self, make_fabric: Callable[..., Fabric], tmp_path: Path
    ) -> None:
        """Supertile BELs get bel.v2 blocks and, for timed types, bel.v3 arcs.

        `genNextpnrModel`'s supertile loop only ever emitted `belStr`/`belv2Str`
        blocks; `belv3Str` silently skipped every supertile BEL. Covers a
        supertile BEL of a type nextpnr times (FABULOUS_LC) at the master
        tile's fabric coordinates (X0Y1, per the module fixture above).
        """
        bel = make_muladd_bel(
            [("I0", IO.INPUT), ("I1", IO.INPUT), ("O", IO.OUTPUT)], prefix="LA_"
        )
        bel.name = "LUT4c_frame_config"

        top_mat = tmp_path / "DSP_top_switch_matrix.list"
        bot_mat = tmp_path / "DSP_bot_switch_matrix.list"
        top_mat.write_text("# DSP_top\n")
        bot_mat.write_text("# DSP_bot\n")
        top = make_empty_tile("DSP_top", tileDir=tmp_path, matrixDir=top_mat)
        bot = make_empty_tile("DSP_bot", tileDir=tmp_path, matrixDir=bot_mat)
        supertile = SuperTile(
            name="DSP",
            tileDir=tmp_path,
            tiles=[top, bot],
            tileMap=[[top], [bot]],
            bels=[bel],
        )
        for t in supertile.tiles:
            t.partOfSuperTile = True
        fabric = make_fabric(tile=[[top], [bot]], superTileDic={"DSP": supertile})

        _, belv1, belv2, belv3, _ = genNextpnrModel(fabric)

        assert "X0Y1,X0,Y1,A,FABULOUS_LC" in belv1
        assert "BelBegin,X0Y1,A,FABULOUS_LC,LA_" in belv2
        assert "BelBegin,X0Y1,A,FABULOUS_LC,LA_" in belv3
        assert "Delay,I0,O,3.0,FF=0" in belv3
        assert "Delay," not in belv2


class TestGenBitstreamSpecSupertileMux:
    """A multiplexed supertile switch matrix maps its mux-select config bits.

    The supertile's config bits physically live in the master tile's frame
    column (the master tile's own ConfigMem leaves them free). For a 4-input
    mux the two select bits must land on the master tile's `TileSpecs` PIP
    entries, and the supertile's used-bit mask must be merged into the master
    tile's `FrameMap` even though the master tile has no config bits of its
    own.
    """

    @pytest.fixture
    def spec(self, make_fabric: Callable[..., Fabric], tmp_path: Path) -> dict:
        # Child tiles each need a (header-only) switch-matrix CSV so the main
        # bitstream-spec loop's parseMatrix() succeeds; neither carries config
        # bits of its own.
        top_mat = tmp_path / "DSP_top_switch_matrix.csv"
        bot_mat = tmp_path / "DSP_bot_switch_matrix.csv"
        top_mat.write_text("DSP_top\n")
        bot_mat.write_text("DSP_bot\n")

        # Supertile matrix: one 4-input multiplexed connection feeding the BEL
        # input SUPER_A0. "<destination>,[<sources>]" -> 2 mux-select bits.
        st_mat = tmp_path / "supertile_matrix.list"
        st_mat.write_text("SUPER_A0{4},[s0|s1|s2|s3]\n")

        # Supertile ConfigMem: the 2 mux-select bits occupy frame 0's two MSBs.
        # used_bits_mask "11000..." -> config bit 0 lands at frame-bit 30,
        # config bit 1 at frame-bit 31 (matching parseConfigMem's encoding).
        configmem = tmp_path / "DSP_ConfigMem.csv"
        rows = [
            "frame_name,frame_index,bits_used_in_frame,used_bits_mask,ConfigBits_ranges"
        ]
        rows.append("frame0,0,2," + "1" * 2 + "0" * 30 + ",1:0")
        for i in range(1, 20):
            rows.append(f"frame{i},{i},0," + "0" * 32 + ",NULL")
        configmem.write_text("\n".join(rows) + "\n")

        top = _tile("DSP_top", [sjump_port("top2bot", IO.OUTPUT)])
        bot = _tile("DSP_bot", [sjump_port("A", IO.OUTPUT, wire_count=1)])
        for t, mat in ((top, top_mat), (bot, bot_mat)):
            t.switch_matrix = SwitchMatrix.from_file(
                mat, t.name, switch_matrix_ports(t.portsInfo, t.bels)
            )
        bel = make_muladd_bel(
            [("SUPER_A0", IO.INPUT)] + [(f"s{i}", IO.OUTPUT) for i in range(4)]
        )
        supertile = SuperTile(
            name="DSP",
            # tileDir is the supertile CSV file; consumers read sibling files via
            # tileDir.parent (here tmp_path, where the ConfigMem CSV is written).
            tileDir=tmp_path / "DSP.csv",
            tiles=[top, bot],
            tileMap=[[top], [bot]],
            bels=[bel],
        )
        supertile.switch_matrix = SwitchMatrix.from_file(
            st_mat, "DSP", supertile.switch_matrix_ports(), canonical=False
        )
        for t in supertile.tiles:
            t.partOfSuperTile = True
        fabric = make_fabric(tile=[[top], [bot]], superTileDic={"DSP": supertile})
        return generateBitstreamSpec(fabric)

    def test_mux_select_bits_mapped_at_master_tile(self, spec: dict) -> None:
        # The master tile is DSP_bot at X0Y1.
        master_specs = spec["TileSpecs"]["X0Y1"]
        # All four mux inputs become PIPs into the BEL input.
        for src in ("s0", "s1", "s2", "s3"):
            assert f"{src}.SUPER_A0" in master_specs
        # The select code is MSB-first over the .list order: the last source
        # (s3) is all-ones, the first (s0) all-zeros, on frame bits 30/31.
        assert master_specs["s3.SUPER_A0"] == {30: "1", 31: "1"}
        assert master_specs["s0.SUPER_A0"] == {30: "0", 31: "0"}

    def test_sjump_wires_are_bitless_pips_in_their_source_tile(
        self, spec: dict
    ) -> None:
        # An SJUMP wire is an immutable connection: nextpnr sees it as a pip, so
        # it needs an empty bit mapping in the tile the wire starts in - the
        # child tile forwards, the master tile in reverse.
        assert spec["TileSpecs"]["X0Y0"]["top2bot0.DSP_top_top2bot0"] == {}
        assert spec["TileSpecs"]["X0Y1"]["A0.DSP_bot_A0"] == {}
        assert spec["TileSpecs_No_Mask"]["X0Y0"]["top2bot0.DSP_top_top2bot0"] == {}

    def test_supertile_mask_merged_into_master_framemap(self, spec: dict) -> None:
        # The master tile has no config bits of its own, yet the supertile's
        # used-bit mask for frame 0 is merged into its FrameMap.
        master_frame_map = spec["FrameMap"]["DSP_bot"]
        assert master_frame_map[0] == "1" * 2 + "0" * 30
