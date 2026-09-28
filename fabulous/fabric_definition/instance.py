"""Placements of tile and supertile types in the fabric grid.

A `Tile` or `SuperTile` is a type: parsed once and shared by every cell that
places it. An instance is one placement of a type. Fabric-level pins are
addressed as `(instance, pin)`, the pin belonging to the instance's type.
Instances compare by identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fabulous.fabric_definition.supertile import SuperTile
    from fabulous.fabric_definition.tile import Tile


@dataclass(frozen=True, eq=False)
class TileInstance:
    """A tile type placed at one cell of the fabric grid.

    Attributes
    ----------
    x : int
        The grid column.
    y : int
        The grid row.
    tile_type : Tile
        The tile type placed here.
    """

    x: int
    y: int
    tile_type: Tile


@dataclass(frozen=True, eq=False)
class SuperTileInstance:
    """A supertile type placed in the fabric grid.

    Attributes
    ----------
    x : int
        The grid column of the supertile's `tileMap` top-left corner.
    y : int
        The grid row of the supertile's `tileMap` top-left corner.
    super_tile : SuperTile
        The supertile type placed here.
    tiles : dict[tuple[int, int], TileInstance]
        The tile instance at each non-empty `(x, y)` of the supertile's
        `tileMap`.
    """

    x: int
    y: int
    super_tile: SuperTile
    tiles: dict[tuple[int, int], TileInstance]

    @property
    def master(self) -> TileInstance:
        """The tile instance at the supertile's master tile.

        The supertile's config bits and BELs are anchored here.
        """
        return self.tiles[self.super_tile.get_master_tile_coords()]

    @property
    def anchor(self) -> TileInstance:
        """The first non-empty `tileMap` cell in row-major order.

        The fabric instantiates the supertile wrapper at this cell.
        """
        return next(iter(self.tiles.values()))
