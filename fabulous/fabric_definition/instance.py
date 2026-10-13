"""Placements of tile and supertile types in the fabric grid.

A `Tile` or `SuperTile` is a type: parsed once and shared by every cell that
places it. An instance is one placement of a type. Fabric-level pins are
addressed as `(instance, pin)`, the pin belonging to the instance's type.
Instances compare by identity.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fabulous.fabric_definition.connection import (
        FixedConnection,
        InstancePin,
        PortConnection,
    )
    from fabulous.fabric_definition.port import Port
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

    @property
    def name(self) -> str:
        """The instance's name in the fabric RTL, `Tile_X{x}Y{y}`."""
        return f"Tile_X{self.x}Y{self.y}"

    def port_signal(self, port: Port) -> str:
        """Return the fabric RTL signal a port of this instance is wired to.

        Parameters
        ----------
        port : Port
            A port of the instance's tile type.

        Returns
        -------
        str
            `Tile_X{x}Y{y}_{port}`, a vector as wide as the port.
        """
        return f"{self.name}_{port.name}"

    @property
    def port_connections(self) -> list[PortConnection[tuple[TileInstance, Port]]]:
        """The tile type's port connections, placed at this instance.

        Returns
        -------
        list[PortConnection[tuple[TileInstance, Port]]]
            The tile type's `port_connections`, both ends at this instance.
        """
        return [
            replace(pc, source=(self, pc.source), sink=(self, pc.sink))
            for pc in self.tile_type.port_connections
        ]

    @property
    def fixed_connections(self) -> list[FixedConnection[InstancePin]]:
        """The tile type's hard wires, placed at this instance.

        Returns
        -------
        list[FixedConnection[InstancePin]]
            The tile type's `fixed_connections`, both ends at this instance.
        """
        return [
            replace(c, source=(self, c.source), sink=(self, c.sink))
            for c in self.tile_type.fixed_connections
        ]


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

    @property
    def port_connections(self) -> list[PortConnection[tuple[TileInstance, Port]]]:
        """The supertile type's port connections, placed onto its tile instances.

        Returns
        -------
        list[PortConnection[tuple[TileInstance, Port]]]
            The supertile's `port_connections`, each `tileMap` position
            replaced by the tile instance covering it.
        """
        return [
            replace(
                pc,
                source=(self.tiles[pc.source[0]], pc.source[1]),
                sink=(self.tiles[pc.sink[0]], pc.sink[1]),
            )
            for pc in self.super_tile.port_connections
        ]

    @property
    def fixed_connections(self) -> list[FixedConnection[InstancePin]]:
        """The supertile type's hard wires, placed onto its tile instances.

        Returns
        -------
        list[FixedConnection[InstancePin]]
            The supertile's `fixed_connections`, each `tileMap` position
            replaced by the tile instance covering it.
        """
        connections = []
        for c in self.super_tile.fixed_connections:
            (src_at, src), (dst_at, dst) = c.source, c.sink
            source, sink = (self.tiles[src_at], src), (self.tiles[dst_at], dst)
            connections.append(replace(c, source=source, sink=sink))
        return connections
