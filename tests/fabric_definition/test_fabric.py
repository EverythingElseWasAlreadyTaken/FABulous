"""Tests for hardcoded validation checks in Fabric.__post_init__."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fabulous.fabric_definition.fabric import Fabric
from fabulous.fabric_definition.supertile import SuperTile
from fabulous.fabric_definition.tile import Tile
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

    @pytest.mark.parametrize(
        ("num_bels", "should_raise"),
        [
            pytest.param(26, False, id="bels_at_boundary"),
            pytest.param(27, True, id="bels_exceed_26"),
            pytest.param(30, True, id="bels_far_exceed_26"),
        ],
    )
    def test_tile_bel_count(
        self,
        make_fabric: Callable[..., Fabric],
        num_bels: int,
        should_raise: bool,
    ) -> None:
        tile = MagicMock(spec=Tile)
        tile.name = "test_tile"
        tile.bels = [MagicMock() for _ in range(num_bels)]
        if should_raise:
            with pytest.raises(ValueError, match="cannot have more than 26 BELs"):
                make_fabric(tileDic={"test_tile": tile})
        else:
            fabric = make_fabric(tileDic={"test_tile": tile})
            assert len(fabric.tileDic["test_tile"].bels) == num_bels


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


class TestSuperTilePlacement:
    """A subtile type only exists as part of its supertile's arrangement."""

    @staticmethod
    def _column_super_tile(top: Tile, bot: Tile) -> SuperTile:
        return SuperTile(
            name="COL", tileDir=Path(), tiles=[top, bot], tileMap=[[top], [bot]]
        )

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
        super_tile = self._column_super_tile(tiles["TOP"], tiles["BOT"])
        grid = [[tiles[n] for n in row] for row in grid_names]

        with pytest.raises(ValueError, match="not placed in its supertile"):
            make_fabric(tile=grid, superTileDic={"COL": super_tile})

    def test_overlapping_placements_leave_a_subtile_uncovered(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """`[[A], [A]]` in a column of three A: the third A is not placed."""
        a = make_empty_tile("A")
        super_tile = self._column_super_tile(a, a)

        with pytest.raises(ValueError, match="X0Y2"):
            make_fabric(tile=[[a], [a], [a]], superTileDic={"COL": super_tile})

    def test_instances_share_the_type_and_map_the_placement(
        self, make_fabric: Callable[..., Fabric]
    ) -> None:
        """Every cell is an instance of the shared type; a placement maps its cells."""
        top, bot, plain = (make_empty_tile(n) for n in ("TOP", "BOT", "PLAIN"))
        super_tile = self._column_super_tile(top, bot)
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
