"""Tests for hardcoded validation checks in Fabric.__post_init__."""

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fabulous.fabric_definition.configmem import ConfigMem
from fabulous.fabric_definition.connection import (
    FrameChainConnection,
    TilePortConnection,
)
from fabulous.fabric_definition.fabric import Fabric
from fabulous.fabric_definition.supertile import SuperTile
from fabulous.fabric_definition.tile import Tile
from fabulous.fabric_generator.parser.parse_csv import parse_port_line
from tests.fabric_definition.conftest import make_empty_tile


class TestFabricValidation:
    """Validate hardcoded bitstream and naming constraints."""

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({}, id="defaults"),
            pytest.param({"numberOfRows": 32}, id="rows_at_boundary"),
            pytest.param({"numberOfColumns": 32}, id="columns_at_boundary"),
            pytest.param(
                {"numberOfRows": 32, "numberOfColumns": 32},
                id="both_at_boundary",
            ),
        ],
    )
    def test_valid_configurations(
        self,
        make_fabric: Callable[..., Fabric],
        overrides: dict,
    ) -> None:
        fabric = make_fabric(**overrides)
        for key, value in overrides.items():
            assert getattr(fabric, key) == value

    @pytest.mark.parametrize(
        ("overrides", "error_match"),
        [
            pytest.param(
                {"numberOfRows": 33},
                "numberOfRows must be less than or equal to 32",
                id="rows_exceed_32",
            ),
            pytest.param(
                {"numberOfRows": 64},
                "numberOfRows must be less than or equal to 32",
                id="rows_far_exceed_32",
            ),
            pytest.param(
                {"numberOfColumns": 33},
                "numberOfColumns must be less than or equal to 32",
                id="columns_exceed_32",
            ),
            pytest.param(
                {"numberOfColumns": 64},
                "numberOfColumns must be less than or equal to 32",
                id="columns_far_exceed_32",
            ),
            pytest.param(
                {"frameBitsPerRow": 16},
                "frameBitsPerRow must be 32",
                id="frame_bits_per_row_wrong",
            ),
            pytest.param(
                {"maxFramesPerCol": 19},
                "maxFramesPerCol must be 20",
                id="max_frames_below_20",
            ),
            pytest.param(
                {"maxFramesPerCol": 21},
                "maxFramesPerCol must be 20",
                id="max_frames_above_20",
            ),
            pytest.param(
                {"frameSelectWidth": 4},
                "frameSelectWidth must be 5",
                id="frame_select_width_wrong",
            ),
            pytest.param(
                {"rowSelectWidth": 3},
                "rowSelectWidth must be 5",
                id="row_select_width_wrong",
            ),
            pytest.param(
                {"desync_flag": 10},
                "desync_flag must be 20",
                id="desync_flag_wrong",
            ),
        ],
    )
    def test_invalid_configurations(
        self,
        make_fabric: Callable[..., Fabric],
        overrides: dict,
        error_match: str,
    ) -> None:
        with pytest.raises(ValueError, match=error_match):
            make_fabric(**overrides)


class TestSubtileBackReference:
    """A subtile type knows its supertile, and has only one."""

    def test_supertile_attaches_its_subtile_types(self) -> None:
        a, b, other = (make_empty_tile(n) for n in ("SUB_A", "SUB_B", "OTHER"))
        super_tile = SuperTile(
            name="SUPER_X", tileDir=Path(), tiles=[a, b], tileMap=[[a, b, a]]
        )

        assert a.super_tile is super_tile
        assert b.super_tile is super_tile
        assert other.super_tile is None

    def test_subtile_of_two_supertiles_raises(self) -> None:
        a = make_empty_tile("SUB_A")
        SuperTile(name="SUPER_X", tileDir=Path(), tiles=[a], tileMap=[[a]])

        with pytest.raises(ValueError, match="already a subtile of supertile"):
            SuperTile(name="SUPER_Y", tileDir=Path(), tiles=[a], tileMap=[[a]])


def _column_super_tile(top: Tile, bot: Tile) -> SuperTile:
    """A supertile of `top` over `bot`."""
    return SuperTile(
        name="COL", tileDir=Path(), tiles=[top, bot], tileMap=[[top], [bot]]
    )


class TestSuperTilePlacement:
    """A subtile type only exists as part of its supertile's arrangement."""

    @pytest.mark.parametrize(
        "grid_names",
        [
            pytest.param([["TOP"]], id="subtile_without_its_partner"),
            pytest.param([["BOT"], ["TOP"]], id="wrong_arrangement"),
        ],
    )
    def test_subtile_outside_its_arrangement_raises(
        self, make_fabric: Callable[..., Fabric], grid_names: list[list[str]]
    ) -> None:
        tiles = {n: make_empty_tile(n) for n in ("TOP", "BOT")}
        super_tile = _column_super_tile(tiles["TOP"], tiles["BOT"])
        grid = [[tiles[n] for n in row] for row in grid_names]

        with pytest.raises(ValueError, match="not placed in its supertile"):
            make_fabric(tile=grid, superTileDic={"COL": super_tile})

    def test_overlapping_placements_leave_a_subtile_uncovered(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """`[[A], [A]]` in a column of three A: the third A is not placed."""
        a = make_empty_tile("A")
        super_tile = _column_super_tile(a, a)

        with pytest.raises(ValueError, match="X0Y2"):
            make_fabric(tile=[[a], [a], [a]], superTileDic={"COL": super_tile})

    def test_instances_share_the_type_and_map_the_placement(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """Every cell is an instance of the shared type; a placement maps its cells."""
        top, bot, plain = (make_empty_tile(n) for n in ("TOP", "BOT", "PLAIN"))
        super_tile = _column_super_tile(top, bot)
        fabric = make_fabric(
            tile=[[top, plain], [bot, plain]], superTileDic={"COL": super_tile}
        )

        assert fabric.instances[0][1] is not fabric.instances[1][1]
        assert fabric.instances[0][1].tile_type is fabric.instances[1][1].tile_type
        (placement,) = fabric.super_tile_instances
        assert (placement.x, placement.y) == (0, 0)
        assert placement.tiles == {
            (0, 0): fabric.instances[0][0],
            (0, 1): fabric.instances[1][0],
        }


class TestSuperTileLines:
    """A supertile wires its subtiles by itself; a fabric checks it agrees."""

    @staticmethod
    def _tile(name: str, line: str) -> Tile:
        return make_empty_tile(name, parse_port_line(line)[0])

    def test_supertile_wires_its_subtiles_without_a_fabric(self) -> None:
        line = "NORTH,A,0,-1,B,1"
        top, bot = self._tile("TOP", line), self._tile("BOT", line)
        super_tile = _column_super_tile(top, bot)

        assert [
            (hop.source[0], hop.source[1].name, hop.sink[0], hop.sink[1].name)
            for hop in super_tile.port_connections
            if isinstance(hop, TilePortConnection)
        ] == [((0, 1), "A", (0, 0), "B")]
        # The routing channel enters at the bottom and leaves at the top.
        assert {(xy, port.name) for xy, port in super_tile.outer_ports} == {
            ((0, 0), "A"),
            ((0, 1), "B"),
        }

    def test_fabric_holds_only_its_own_connections(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """The tile port connections inside a placement are the supertile's, placed."""
        line = "NORTH,A,0,-1,B,1"
        top, bot = self._tile("TOP", line), self._tile("BOT", line)
        super_tile = _column_super_tile(top, bot)
        fabric = make_fabric(tile=[[top], [bot]], superTileDic={"COL": super_tile})
        (inner,) = [
            pc
            for pc in super_tile.port_connections
            if isinstance(pc, TilePortConnection)
        ]

        assert not any(
            isinstance(pc, TilePortConnection) for pc in fabric.port_connections
        )
        placed = [
            pc
            for pc in fabric.placed_port_connections()
            if isinstance(pc, TilePortConnection)
        ]
        assert [
            (pc.source[0].y, pc.source[1], pc.sink[0].y, pc.sink[1]) for pc in placed
        ] == [(1, inner.source[1], 0, inner.sink[1])]

    def test_fabric_reports_a_supertile_wired_differently(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """Two NULL-ended declarations pair only through a full one in another tile."""
        top = self._tile("TOP", "NORTH,NULL,0,-1,N1END,1")
        bot = self._tile("BOT", "NORTH,N1BEG,0,-1,NULL,1")
        full = self._tile("FULL", "NORTH,N1BEG,0,-1,N1END,1")
        super_tile = _column_super_tile(top, bot)
        fabric = make_fabric(
            tile=[[top, full], [bot, full]], superTileDic={"COL": super_tile}
        )

        assert not any(
            isinstance(pc, TilePortConnection) for pc in super_tile.port_connections
        )
        assert any(
            "Supertile 'COL' at X0Y0 wires its subtiles differently" in problem
            for problem in fabric.routing_channel_problems()
        )


class TestSuperTileBelLimit:
    """A supertile's BELs share the master tile's 26 BEL letters."""

    @pytest.mark.parametrize(
        ("master_bels", "super_bels", "should_raise"),
        [
            pytest.param(20, 6, False, id="at_boundary"),
            pytest.param(20, 7, True, id="exceeds_26"),
        ],
    )
    def test_supertile_and_master_bel_count(
        self, master_bels: int, super_bels: int, should_raise: bool
    ) -> None:
        top, bot = make_empty_tile("TOP"), make_empty_tile("BOT")
        bot.bels = [MagicMock() for _ in range(master_bels)]
        expectation = (
            pytest.raises(ValueError, match="together cannot have more than")
            if should_raise
            else nullcontext()
        )
        with expectation:
            SuperTile(
                name="COL",
                tileDir=Path(),
                tiles=[top, bot],
                tileMap=[[top], [bot]],
                bels=[MagicMock() for _ in range(super_bels)],
            )


def _framed(name: str) -> Tile:
    """An empty tile on a 32 x 20 frame grid, so it has frame ports."""
    tile = make_empty_tile(name)
    tile.config_mem.set_grid(32, 20)
    return tile


def _frame_chains(fabric: Fabric) -> dict[tuple[str, str], str]:
    """Each frame input `(tile, port)` and the signal it reads."""
    return {
        (pc.sink[0].name, pc.sink[1].name): pc.vector_signal
        for pc in fabric.port_connections
        if isinstance(pc, FrameChainConnection)
    }


class TestFrameChains:
    """`FrameData` runs west to east, `FrameStrobe` south to north."""

    def test_a_chain_skips_empty_cells_and_starts_at_the_fabric_port(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        a, b = _framed("A"), _framed("B")
        fabric = make_fabric(tile=[[a, None, b]], numberOfRows=1, numberOfColumns=3)

        assert _frame_chains(fabric) == {
            ("Tile_X0Y0", "FrameData"): "Row_Y0_FrameData",
            ("Tile_X0Y0", "FrameStrobe"): "Column_X0_FrameStrobe",
            ("Tile_X2Y0", "FrameData"): "Tile_X0Y0_FrameData_O",
            ("Tile_X2Y0", "FrameStrobe"): "Column_X2_FrameStrobe",
        }
        (column_2,) = (
            pc
            for pc in fabric.port_connections
            if isinstance(pc, FrameChainConnection)
            and pc.vector_signal == "Column_X2_FrameStrobe"
        )
        assert column_2.source is fabric.config_logic_ports[1]
        assert column_2.source_offset == 2 * 20

    @pytest.mark.parametrize(
        "grid", [pytest.param(None, id="no_grid"), pytest.param((8, 20), id="other")]
    )
    def test_a_tile_off_the_fabric_frame_grid_is_rejected(
        self, make_fabric: Callable[..., Fabric], grid: tuple[int, int] | None
    ) -> None:
        tile = make_empty_tile("T")
        tile.config_mem = ConfigMem(source=Path("T_ConfigMem.csv"))
        if grid is not None:
            tile.config_mem.set_grid(*grid)

        with pytest.raises(ValueError, match="'T' holds its configuration on a"):
            make_fabric(tile=[[tile]], numberOfRows=1, numberOfColumns=1)

    def test_an_empty_fabric_has_no_frame_ports(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        fabric = make_fabric(tile=[], numberOfRows=0, numberOfColumns=0)

        assert fabric.config_logic_ports == ()
        assert list(fabric.fixed_connections()) == []

    def test_a_grid_of_null_cells_keeps_its_frame_ports(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        fabric = make_fabric(tile=[[None, None]], numberOfRows=1, numberOfColumns=2)

        assert [(p.name, p.width) for p in fabric.config_logic_ports] == [
            ("FrameData", 32),
            ("FrameStrobe", 2 * 20),
        ]

    def test_a_short_row_below_counts_as_empty_cells(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        a, b, c = _framed("A"), _framed("B"), _framed("C")
        fabric = make_fabric(tile=[[a, b], [c]], numberOfRows=2, numberOfColumns=2)

        assert _frame_chains(fabric)[("Tile_X1Y0", "FrameStrobe")] == (
            "Column_X1_FrameStrobe"
        )
        assert _frame_chains(fabric)[("Tile_X0Y0", "FrameStrobe")] == (
            "Tile_X0Y1_FrameStrobe_O"
        )

    def test_a_supertile_hole_is_bridged_by_the_fabric(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """A U-shaped supertile: a subtile east of a hole reads across it.

        The supertile chains only subtiles next to each other, so the subtile
        at (2, 0) faces out with its `FrameData`; the fabric feeds it from the
        nearest placed tile to the west, the same placement's (0, 0).
        """
        names = [["L0", None, "R0"], ["L1", None, "R1"], ["B0", "B1", "B2"]]
        tiles = {n: _framed(n) for row in names for n in row if n}
        tile_map = [[tiles.get(n) for n in row] for row in names]
        super_tile = SuperTile(
            name="U", tileDir=Path(), tiles=list(tiles.values()), tileMap=tile_map
        )
        fabric = make_fabric(
            tile=tile_map,
            superTileDic={"U": super_tile},
            numberOfRows=3,
            numberOfColumns=3,
        )

        chains = _frame_chains(fabric)
        assert chains[("Tile_X2Y0", "FrameData")] == "Tile_X0Y0_FrameData_O"
        assert chains[("Tile_X2Y1", "FrameData")] == "Tile_X0Y1_FrameData_O"
        # Subtiles next to each other are chained by the supertile, not here.
        assert ("Tile_X1Y2", "FrameData") not in chains
        assert ("Tile_X0Y0", "FrameStrobe") not in chains
