"""Tests for SuperTile methods.

The supertile aggregates Tile objects into a 2D layout. The methods under test are
pure functions of the ``tileMap`` shape and the constituent tiles' port counts:

- `get_ports_around_tile`: emits side-of-tile port lists for *outer* edges only.
- `get_internal_connections`: emits side-of-tile port lists for *inner* edges only.
- ``__iter__``: yields ``((x, y), tile)`` for every non-None cell.
- ``max_width`` / ``max_height``: dimensions of the layout grid.
- ``get_min_die_area``: pin-density-driven physical minimum.

These were not previously covered and are entry points to the global tile size
optimisation pipeline, so any drift in their semantics propagates silently.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from fabulous.fabric_definition.bel import Bel
from fabulous.fabric_definition.define import IO, Side
from fabulous.fabric_definition.port import BelConfigPort
from fabulous.fabric_definition.supertile import SuperTile
from fabulous.fabric_definition.switch_matrix import SwitchMatrix
from fabulous.fabric_definition.tile import Tile
from tests.conftest import make_muladd_bel
from tests.fabric_definition.conftest import make_empty_tile, make_side_port


class TestSuperTileLayout:
    """Geometric properties — independent of the constituent tiles' ports."""

    def test_iter_yields_only_non_none_tiles_with_xy(self) -> None:
        # Layout:
        #   row0: T00, None
        #   row1: None, T11
        # __iter__ uses (row_index, col_index) as (x, y) per the implementation.
        t00 = make_empty_tile("T00")
        t11 = make_empty_tile("T11")
        st = SuperTile(
            name="ST",
            tileDir=Path(),
            tiles=[t00, t11],
            tileMap=[[t00, None], [None, t11]],
        )
        assert list(st) == [((0, 0), t00), ((1, 1), t11)]

    def test_max_width_uses_widest_row(self) -> None:
        t = make_empty_tile("T")
        # Ragged layout: top row is wider.
        st = SuperTile(
            name="ST",
            tileDir=Path(),
            tiles=[t],
            tileMap=[[t, t, t], [t, None]],
        )
        assert st.max_width == 3

    def test_max_height_is_row_count(self) -> None:
        t = make_empty_tile("T")
        st = SuperTile(
            name="ST",
            tileDir=Path(),
            tiles=[t],
            tileMap=[[t], [t], [t]],
        )
        assert st.max_height == 3


class TestSuperTilePortQueries:
    """`get_ports_around_tile` / `get_internal_connections` partition the four
    edges of every cell into "outer" (boundary or facing a hole) and "inner"
    (facing another tile).

    The implementation calls the side-getter for outer-edges only
    in `get_ports_around_tile` and inner-edges only in `get_internal_connections`.
    """

    def test_single_tile_supertile_has_all_outer_edges(
        self, mocker: MockerFixture
    ) -> None:
        # A 1x1 supertile: every edge is outer, none are internal.
        tile = mocker.MagicMock(spec=Tile)
        tile.getNorthSidePorts.return_value = ["N"]
        tile.getEastSidePorts.return_value = ["E"]
        tile.getSouthSidePorts.return_value = ["S"]
        tile.getWestSidePorts.return_value = ["W"]

        st = SuperTile(
            name="ST",
            tileDir=Path(),
            tiles=[tile],
            tileMap=[[tile]],
        )

        ports = st.get_ports_around_tile()
        # The cell coordinate is "x,y" for col x, row y.
        assert list(ports.keys()) == ["0,0"]
        # Every direction should appear exactly once on the only cell.
        assert ports["0,0"] == [["N"], ["E"], ["S"], ["W"]]

        # No internal connections.
        assert st.get_internal_connections() == []

    def test_two_tile_horizontal_splits_outer_and_inner(
        self, mocker: MockerFixture
    ) -> None:
        # Two side-by-side tiles. The left tile's outer edges are N, S, W and
        # its east edge is internal (faces the right tile); mirror for the
        # right tile.
        left = mocker.MagicMock(spec=Tile)
        left.getNorthSidePorts.return_value = ["LN"]
        left.getEastSidePorts.return_value = ["LE"]
        left.getSouthSidePorts.return_value = ["LS"]
        left.getWestSidePorts.return_value = ["LW"]

        right = mocker.MagicMock(spec=Tile)
        right.getNorthSidePorts.return_value = ["RN"]
        right.getEastSidePorts.return_value = ["RE"]
        right.getSouthSidePorts.return_value = ["RS"]
        right.getWestSidePorts.return_value = ["RW"]

        st = SuperTile(
            name="ST",
            tileDir=Path(),
            tiles=[left, right],
            tileMap=[[left, right]],
        )

        ports = st.get_ports_around_tile()
        # Outer edges: left has N, S, W (no E, since right is east neighbor).
        assert ports["0,0"] == [["LN"], ["LS"], ["LW"]]
        # Outer edges: right has N, E, S (no W).
        assert ports["1,0"] == [["RN"], ["RE"], ["RS"]]

        # Inner connections: left's E side and right's W side.
        internal = st.get_internal_connections()
        # Each entry is (ports, x, y); order follows the loop.
        assert (["LE"], 0, 0) in internal
        assert (["RW"], 1, 0) in internal
        assert len(internal) == 2


class TestSuperTileMinDieArea:
    """``get_min_die_area`` aggregates the maximum per-side port count across
    constituent tiles, then derives a physical floor from the pitch.

    Formula per side: ``min_dim = (max_count * thickness_mult + edge_offset) * pitch``.
    """

    def test_picks_max_side_count_across_tiles(self) -> None:
        # Tile A: 3 north, 1 east. Tile B: 1 north, 2 east.
        # Aggregate max: 3 north (=> width axis), 2 east (=> height axis).
        a = make_empty_tile(
            "A",
            ports=[make_side_port(Side.NORTH, f"AN{i}") for i in range(3)]
            + [make_side_port(Side.EAST, "AE")],
        )
        b = make_empty_tile(
            "B",
            ports=[make_side_port(Side.NORTH, "BN")]
            + [make_side_port(Side.EAST, f"BE{i}") for i in range(2)],
        )

        st = SuperTile(
            name="ST",
            tileDir=Path(),
            tiles=[a, b],
            tileMap=[[a, b]],
        )

        # pitch=1, thickness_mult=1, edge_offset=2.
        # width = (3*1 + 2)*1 = 5 ; height = (2*1 + 2)*1 = 4.
        w, h = st.get_min_die_area(
            x_pitch=Decimal(1),
            y_pitch=Decimal(1),
            x_pin_thickness_mult=Decimal(1),
            y_pin_thickness_mult=Decimal(1),
            edge_offset=2,
        )
        assert w == Decimal(5)
        assert h == Decimal(4)

    def test_thickness_mult_and_pitch_scale_dimensions(self) -> None:
        # 2 ports on south (covers x_io_count via max(north, south)).
        a = make_empty_tile(
            "A",
            ports=[make_side_port(Side.SOUTH, f"AS{i}") for i in range(2)]
            + [make_side_port(Side.WEST, "AW")],
        )
        st = SuperTile(
            name="ST",
            tileDir=Path(),
            tiles=[a],
            tileMap=[[a]],
        )
        # width = (2 * 3 + 2) * 0.5 = 4.0 ; height = (1 * 2 + 2) * 0.25 = 1.0
        w, h = st.get_min_die_area(
            x_pitch=Decimal("0.5"),
            y_pitch=Decimal("0.25"),
            x_pin_thickness_mult=Decimal(3),
            y_pin_thickness_mult=Decimal(2),
            edge_offset=2,
        )
        assert w == Decimal("4.0")
        assert h == Decimal("1.0")


def _configurable_bels(*bits: int) -> list[Bel]:
    """BELs with the given config bit counts (0: no config port)."""
    return [
        make_muladd_bel(
            [],
            prefix=f"B{i}_",
            config_port=BelConfigPort("ConfigBits", IO.INPUT, n, {}) if n else None,
        )
        for i, n in enumerate(bits)
    ]


class TestConfigBitRanges:
    """Where each BEL and switch matrix sits in the (supertile) ConfigBits."""

    def test_tile_puts_the_bels_first(self) -> None:
        tile = make_empty_tile("T", config_bits=5)
        bels = _configurable_bels(3, 0, 2)
        tile.add_bels(bels)

        assert tile.config_bit_ranges == {
            bels[0]: range(3),
            bels[1]: range(3, 3),
            bels[2]: range(3, 5),
            tile.switch_matrix: range(5, 10),
        }

    @pytest.mark.parametrize("matrix_bits", [None, 4], ids=["no matrix", "hdl"])
    def test_supertile_puts_the_matrix_first(self, matrix_bits: int | None) -> None:
        """A hand-written matrix's declared bits come before the BELs too."""
        t = make_empty_tile("T")
        bels = _configurable_bels(3, 2)
        matrix = None
        if matrix_bits is not None:
            matrix = SwitchMatrix(Path("ST.v"), (), {}, "ST", hdl_config_bits=4)
        st = SuperTile("ST", Path(), [t], [[t]], bels, matrix)

        start = matrix_bits or 0
        expected = {} if matrix is None else {matrix: range(start)}
        expected |= {
            bels[0]: range(start, start + 3),
            bels[1]: range(start + 3, start + 5),
        }
        assert st.config_bit_ranges == expected
