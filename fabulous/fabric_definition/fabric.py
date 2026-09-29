"""FPGA fabric definition module.

This module contains the Fabric class which represents the complete FPGA fabric
including tile layout, configuration parameters, and connectivity information. The
fabric is the top-level container for all tiles, BELs, and routing resources.
"""

from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from fabulous.custom_exception import InvalidFabricDefinition
from fabulous.fabric_definition.bel import Bel
from fabulous.fabric_definition.channel import (
    ChannelDeclaration,
    RoutingChannel,
    resolve_channels,
)
from fabulous.fabric_definition.connection import FixedConnection, InstancePin
from fabulous.fabric_definition.define import (
    IO,
    ConfigBitMode,
    MultiplexerStyle,
    Side,
)
from fabulous.fabric_definition.instance import SuperTileInstance, TileInstance
from fabulous.fabric_definition.supertile import SuperTile
from fabulous.fabric_definition.tile import Tile


@dataclass
class Fabric:
    """Store the configuration of a fabric.

    All the information is parsed from the CSV file.

    Attributes
    ----------
    fabric_dir : Path
        The path to the fabric config file
    tile : list[list[Tile]]
        The tile map of the fabric
    name : str
        The name of the fabric
    numberOfRows : int
        The number of rows of the fabric
    numberOfColumns : int
        The number of columns of the fabric
    configBitMode : ConfigBitMode
        The configuration bit mode of the fabric.
        Currently supports frame based or ff chain
    frameBitsPerRow : int
        The number of frame bits per row of the fabric
    maxFramesPerCol : int
        The maximum number of frames per column of the fabric
    package : str
        The extra package used by the fabric. Only useful for VHDL output.
    generateDelayInSwitchMatrix : int
        The amount of delay in a switch matrix.
    multiplexerStyle : MultiplexerStyle
        The style of the multiplexer used in the fabric.
        Currently supports custom or generic
    frameSelectWidth : int
        The width of the frame select signal.
    rowSelectWidth : int
        The width of the row select signal.
    desync_flag : int
        The flag indicating desynchronization status,
        used to manage timing issues within the fabric.
    numberOfBRAMs : int
        The number of BRAMs in the fabric.
    superTileEnable : bool
        Whether the fabric has super tile.
    disableUserCLK : bool
        Whether to disable UserCLK generation in the fabric.
    userCLKSide : Side
        Side on which UserCLK enters each tile; UserCLKo leaves on the opposite
        side and feeds the next tile in that direction. Default SOUTH (S->N ladder).
    multiClkDomains : bool
        Whether the fabric uses multiple clock domains. When True, CLK features
        are kept in the bitstream instead of being filtered out.
    syncHeaderHex : str
        Hex string of the 20-byte sync header written at the start of every
        binary bitstream.
    tileDic : dict[str, Tile]
        A dictionary of tiles used in the fabric. The key is the name of the tile and
        the value is the tile.
    superTileDic : dict[str, SuperTile]
        A dictionary of super tiles used in the fabric. The key is the name of the
        supertile and the value is the supertile.
    unusedTileDic: dict[str, Tile]
        A dictionary of tiles that are not used in the fabric,
        but defined in the fabric.csv.
        The key is the name of the tile and the value is the tile.
    unusedSuperTileDic : dict[str, SuperTile]
        A dictionary of super tiles that are not used in the fabric,
        but defined in the fabric.csv.
        The key is the name of the tile and the value is the tile.
    instances : list[list[TileInstance | None]]
        The placement of a tile type at each grid cell (None for an empty
        cell), mirroring `tile`. Derived in `__post_init__`.
    super_tile_instances : list[SuperTileInstance]
        The supertile placements, each mapping its `tileMap` positions to the
        tile instances covering them. Derived in `__post_init__`.
    channels : dict[ChannelDeclaration, RoutingChannel]
        The routing channel each routing declaration of a placed tile type takes
        part in. Derived in `__post_init__`.
    """

    fabric_dir: Path
    tile: list[list[Tile]] = field(default_factory=list)

    name: str = "eFPGA"
    numberOfRows: int = 15
    numberOfColumns: int = 15
    configBitMode: ConfigBitMode = ConfigBitMode.FRAME_BASED
    frameBitsPerRow: int = 32
    maxFramesPerCol: int = 20
    package: str = "use work.my_package.all"
    generateDelayInSwitchMatrix: int = 80
    multiplexerStyle: MultiplexerStyle = MultiplexerStyle.CUSTOM
    frameSelectWidth: int = 5
    rowSelectWidth: int = 5
    desync_flag: int = 20
    numberOfBRAMs: int = 10
    superTileEnable: bool = True
    disableUserCLK: bool = False
    userCLKSide: Side = Side.SOUTH
    multiClkDomains: bool = False
    syncHeaderHex: str = "00AAFF01000000010000000000000000FAB0FAB1"

    tileDic: dict[str, Tile] = field(default_factory=dict)
    superTileDic: dict[str, SuperTile] = field(default_factory=dict)
    unusedTileDic: dict[str, Tile] = field(default_factory=dict)
    unusedSuperTileDic: dict[str, SuperTile] = field(default_factory=dict)
    instances: list[list[TileInstance | None]] = field(default_factory=list, init=False)
    super_tile_instances: list[SuperTileInstance] = field(
        default_factory=list, init=False
    )
    channels: dict[ChannelDeclaration, RoutingChannel] = field(
        default_factory=dict, init=False
    )

    def __post_init__(self) -> None:
        """Check the fabric parameters, place the tiles and resolve the channels."""
        if self.numberOfRows > 32:
            raise ValueError(
                "Due to bitstream limitations, "
                "numberOfRows must be less than or equal to 32."
            )

        if self.numberOfColumns > 32:
            raise ValueError(
                "Due to bitstream limitations, "
                "numberOfColumns must be less than or equal to 32."
            )

        if self.frameBitsPerRow != 32:
            raise ValueError(
                "Due to bitstream limitations, frameBitsPerRow must be 32."
            )

        if self.maxFramesPerCol != 20:
            raise ValueError(
                "Due to bitstream limitations, maxFramesPerCol must be 20."
            )

        if self.frameSelectWidth != 5:
            raise ValueError(
                "Due to bitstream limitations, frameSelectWidth must be 5."
            )

        if self.rowSelectWidth != 5:
            raise ValueError("Due to bitstream limitations, rowSelectWidth must be 5.")

        if self.desync_flag != 20:
            raise ValueError("Due to bitstream limitations, desync_flag must be 20.")

        # A subtile type only exists as part of its supertile: every cell of one
        # must be covered by a placement of the supertile's arrangement. SJUMP
        # wires route a subtile to its supertile's switch matrix, so a tile type
        # declaring them must be a subtile type.
        self.instances = [
            [
                None if tile is None else TileInstance(x, y, tile)
                for x, tile in enumerate(row)
            ]
            for y, row in enumerate(self.tile)
        ]
        self.super_tile_instances = self._place_super_tiles()
        covered = {
            instance
            for placement in self.super_tile_instances
            for instance in placement.tiles.values()
        }
        for row in self.instances:
            for instance in row:
                if instance is None:
                    continue
                tile, x, y = instance.tile_type, instance.x, instance.y
                if tile.super_tile is not None:
                    if instance not in covered:
                        raise ValueError(
                            f"Tile '{tile.name}' at X{x}Y{y} is a subtile but is "
                            "not placed in its supertile's arrangement. A subtile "
                            "can only be placed as part of its supertile; copy and "
                            "rename the tile for a standalone version."
                        )
                elif tile.sjump_ports:
                    raise ValueError(
                        f"Tile '{tile.name}' declares SJUMP wires but is not part "
                        "of any supertile. SJUMP wires route to a supertile-hosted "
                        "BEL and are only valid inside a supertile's tiles."
                    )

        self.channels = resolve_channels(
            port.declaration
            for row in self.tile
            for tile in row
            if tile is not None
            for port in tile.portsInfo
        )
        # Reported, not fatal: work on single tiles goes on with a fabric in
        # progress; everything using the whole fabric refuses it.
        for problem in self.routing_channel_problems():
            logger.warning(problem)

    def _place_super_tiles(self) -> list[SuperTileInstance]:
        """Find every placement of a supertile's arrangement in the grid.

        Each supertile type's `tileMap` is matched against the grid at every
        base cell. A match claims its tile instances; a later match overlapping
        claimed instances is skipped, so every cell belongs to at most one
        placement.

        Returns
        -------
        list[SuperTileInstance]
            The placements, per supertile type in row-major order.
        """
        placements: list[SuperTileInstance] = []
        claimed: set[TileInstance] = set()
        for st in self.superTileDic.values():
            for base_fy in range(len(self.tile) - st.max_height + 1):
                for base_fx in range(len(self.tile[base_fy]) - st.max_width + 1):
                    if not self._matches_super_tile(st, base_fx, base_fy):
                        continue
                    tiles = {
                        (lx, ly): self.instances[base_fy + ly][base_fx + lx]
                        for ly, row in enumerate(st.tileMap)
                        for lx, sub in enumerate(row)
                        if sub is not None
                    }
                    if claimed.intersection(tiles.values()):
                        continue
                    claimed.update(tiles.values())
                    placements.append(SuperTileInstance(base_fx, base_fy, st, tiles))
        return placements

    def fixed_connections(
        self, instance: TileInstance
    ) -> list[FixedConnection[InstancePin]]:
        """Return the hard wires starting at a placed tile.

        These are the tile type's own `fixed_connections`, placed at the
        instance, and the routing channel hops from each of its begin ports to
        the same channel's end port one tile along. A hop keeps the bit index;
        the staging is inside the tiles. Needs consistent routing channels
        (`check_routing_channels`).

        Parameters
        ----------
        instance : TileInstance
            The placed tile.

        Returns
        -------
        list[FixedConnection[InstancePin]]
            The connections, the tile's own first, then the hops.
        """
        connections = [
            FixedConnection((instance, c.source), (instance, c.sink), c.declaration)
            for c in instance.tile_type.fixed_connections
        ]
        for out in instance.tile_type.portsInfo:
            if not out.is_output:
                continue
            declaration = out.declaration
            channel = self.channels[declaration]
            dx, dy = channel.step
            target = self.instances[instance.y + dy][instance.x + dx]
            end = next(
                p
                for p in target.tile_type.portsInfo
                if p.is_input and self.channels[p.declaration] is channel
            )
            # A start tap drives every slice it spans; with no span, none.
            if declaration.end is None:
                bits = range(declaration.wire_count * declaration.distance)
            else:
                bits = range(out.width)
            connections += [
                FixedConnection((instance, out[i]), (target, end[i]), declaration)
                for i in bits
            ]
        return connections

    def check_routing_channels(self) -> None:
        """Refuse a fabric whose routing channels are inconsistent.

        Called by everything that generates or uses the fabric as a whole (fabric
        RTL, top wrapper, geometry, nextpnr model, bitstream spec, stitching);
        work on single tiles does not need it.

        Raises
        ------
        InvalidFabricDefinition
            If a routing channel end dangles or is undriven.
        """
        if problems := self.routing_channel_problems():
            raise InvalidFabricDefinition("\n".join(problems))

    def routing_channel_problems(self) -> list[str]:
        """Report routing channel ends without their partner one hop away.

        A begin port drives the same channel's end port in the next tile along
        the channel; an end port is driven by the begin port one tile back. The
        fabric is built even when this does not hold (it logs the problems as
        warnings), so work on single tiles keeps going; everything using the
        fabric as a whole refuses it (`check_routing_channels`).

        Returns
        -------
        list[str]
            One message per dangling or undriven channel end, empty if none.
        """
        ends = {
            (instance.x, instance.y, self.channels[p.declaration], p.io_direction): (
                instance
            )
            for row in self.instances
            for instance in row
            if instance is not None
            for p in instance.tile_type.portsInfo
        }
        problems = []
        for (x, y, channel, io), instance in ends.items():
            dx, dy = channel.step
            sign = 1 if io == IO.OUTPUT else -1
            partner_io = IO.INPUT if io == IO.OUTPUT else IO.OUTPUT
            px, py = x + sign * dx, y + sign * dy
            if (px, py, channel, partner_io) not in ends:
                problem = (
                    "has nothing receiving it" if io == IO.OUTPUT else "is not driven"
                )
                problems.append(
                    f"Routing channel {channel.begin} -> {channel.end} of tile "
                    f"'{instance.tile_type.name}' at X{x}Y{y} {problem} at "
                    f"X{px}Y{py}."
                )
        return problems

    def _matches_super_tile(
        self, superTile: SuperTile, base_fx: int, base_fy: int
    ) -> bool:
        """Return whether `superTile`'s tileMap matches the grid at the base.

        Grid cells and the `tileMap` share the tile type objects, so a match is
        the same object (or None) in every cell.
        """
        return all(
            self.tile[base_fy + ly][base_fx + lx] is sub
            for ly, row in enumerate(superTile.tileMap)
            for lx, sub in enumerate(row)
        )

    def __repr__(self) -> str:
        """Return the string representation of the fabric.

        Returns
        -------
        str
            A formatted string showing the fabric layout and key parameters.
        """
        fabric = ""
        for i in range(self.numberOfRows):
            for j in range(self.numberOfColumns):
                if self.tile[i][j] is None:
                    fabric += "Null".ljust(15) + "\t"
                else:
                    fabric += f"{str(self.tile[i][j].name).ljust(15)}\t"
            fabric += "\n"

        fabric += "\n"
        fabric += f"numberOfColumns: {self.numberOfColumns}\n"
        fabric += f"numberOfRows: {self.numberOfRows}\n"
        fabric += f"configBitMode: {self.configBitMode}\n"
        fabric += f"frameBitsPerRow: {self.frameBitsPerRow}\n"
        fabric += f"maxFramesPerCol: {self.maxFramesPerCol}\n"
        fabric += f"package: {self.package}\n"
        fabric += f"generateDelayInSwitchMatrix: {self.generateDelayInSwitchMatrix}\n"
        fabric += f"multiplexerStyle: {self.multiplexerStyle}\n"
        fabric += f"superTileEnable: {self.superTileEnable}\n"
        fabric += f"disableUserCLK: {self.disableUserCLK}\n"
        fabric += f"userCLKSide: {self.userCLKSide}\n"
        fabric += f"multiClkDomains: {self.multiClkDomains}\n"
        fabric += f"tileDic: {list(self.tileDic.keys())}\n"
        return fabric

    def __iter__(self) -> Generator[tuple[tuple[int, int], Tile | None]]:
        """Iterate over all tiles in the fabric in row-major order.

        Yields
        ------
        tuple[tuple[int, int], Tile | None]
            A tuple where the first element is the (x, y) coordinates and the
            second is the Tile at that position or None if the position is
            empty.
        """
        for y, row in enumerate(self.tile):
            for x, tile in enumerate(row):
                yield (x, y), tile

    def getTileByName(self, name: str) -> Tile | SuperTile:
        """Get a tile by its name from the fabric.

        Search for the tile first in the used tiles dictionary, then in the unused tiles
        dictionary then in the supertiles if not found.

        Parameters
        ----------
        name : str
            The name of the tile to retrieve.

        Returns
        -------
        Tile | SuperTile
            The tile or supertile object if found.

        Raises
        ------
        KeyError
            If the tile name is not found in either used or unused tiles.
        """
        ret = self.tileDic.get(name)
        if ret is None:
            ret = self.unusedTileDic.get(name)
        if ret is None:
            ret = self.getSuperTileByName(name)  # Check if it's a supertile
        if ret is None:
            raise KeyError(f"Tile {name} not found in fabric.")
        return ret

    def getSuperTileByName(self, name: str) -> SuperTile:
        """Get a supertile by its name from the fabric.

        Searches for the supertile first in the used supertiles dictionary, then in the
        unused supertiles dictionary if not found.

        Parameters
        ----------
        name : str
            The name of the supertile to retrieve.

        Returns
        -------
        SuperTile
            The super tile object if found.

        Raises
        ------
        KeyError
            If the super tile name is not found in either used or unused super tiles.
        """
        ret = self.superTileDic.get(name)
        if ret is None:
            ret = self.unusedSuperTileDic.get(name)
        if ret is None:
            raise KeyError(f"SuperTile {name} not found in fabric.")

        return ret

    def getAllUniqueBels(self) -> list[Bel]:
        """Get all unique BELs from all tiles and supertiles in the fabric.

        Returns
        -------
        list[Bel]
            A list of all unique BELs across all tiles and supertiles.
        """
        bels = list()
        for tile in self.tileDic.values():
            bels.extend(tile.bels)
        for superTile in self.superTileDic.values():
            bels.extend(superTile.bels)
        return bels

    def getBelsByTileXY(self, x: int, y: int) -> list[Bel]:
        """Get all the Bels of a tile.

        Parameters
        ----------
        x : int
            The x coordinate of / column the tile.
        y : int
            The y coordinate / row of the tile.

        Returns
        -------
        list[Bel]
            A list of Bels in the tile.

        Raises
        ------
        ValueError
            Tile coordinates are out of range.
        """
        if x < 0 or x >= self.numberOfColumns or y < 0 or y >= self.numberOfRows:
            raise ValueError(
                f"Invalid tile coordinates: ({x},{y}) max (0,0) - ({self.numberOfRows},"
                f"{self.numberOfColumns})"
            )
        if self.tile[y][x] is None:
            return []

        return self.tile[y][x].bels

    def find_tile_positions(
        self, tile: Tile | SuperTile
    ) -> list[tuple[int, int]] | None:
        """Find all positions where a tile or supertile appears in the fabric grid.

        Parameters
        ----------
        tile : Tile | SuperTile
            The tile or supertile to search for

        Returns
        -------
        list[tuple[int, int]] | None
            List of (x, y) positions where the tile/supertile appears,
            or None if not found
        """
        positions = []
        if isinstance(tile, SuperTile):
            # For SuperTiles, find where they appear
            for y, row in enumerate(self.tile):
                for x, fabric_tile in enumerate(row):
                    if fabric_tile is None:
                        continue
                    # Check if this fabric tile belongs to the supertile
                    for st in self.superTileDic.values():
                        if st == tile:
                            # Check if fabric_tile is part of this supertile
                            for st_row in st.tileMap:
                                for st_tile in st_row:
                                    if st_tile and st_tile.name == fabric_tile.name:
                                        positions.append((x, y))
        else:
            # For regular Tiles, find where they appear
            for y, row in enumerate(self.tile):
                for x, fabric_tile in enumerate(row):
                    if fabric_tile and fabric_tile.name == tile.name:
                        positions.append((x, y))

        return positions or None

    def determine_border_side(self, x: int, y: int) -> Side | None:
        """Determine which border side a tile position is on, if any.

        Parameters
        ----------
        x : int
            X coordinate in the fabric grid
        y : int
            Y coordinate in the fabric grid

        Returns
        -------
        Side | None
            The border side (NORTH, SOUTH, EAST, or WEST) if the position is on
            a border, None otherwise. If on a corner, returns the vertical side
            (NORTH or SOUTH) as priority.
        """
        is_north = y == 0
        is_south = y == self.numberOfRows - 1
        is_east = x == self.numberOfColumns - 1
        is_west = x == 0

        # Priority: corners get vertical sides (NORTH/SOUTH)
        if is_north:
            return Side.NORTH
        if is_south:
            return Side.SOUTH
        if is_east:
            return Side.EAST
        if is_west:
            return Side.WEST

        return None

    def get_all_unique_tiles(self) -> list[Tile | SuperTile]:
        """Get list of unique tile types used in the fabric.

        Returns
        -------
        list[Tile | SuperTile]
            List of unique tile types (one instance per type name)
        """
        result: list[Tile | SuperTile] = []

        # Add all regular tiles from tileDic
        result.extend(i for i in self.tileDic.values() if i.super_tile is None)

        # Add all SuperTiles from superTileDic
        result.extend(self.superTileDic.values())

        return result

    def get_tile_row_column_indices(self, tile_name: str) -> tuple[set[int], set[int]]:
        """Get all row and column indices where a tile type appears.

        Parameters
        ----------
        tile_name : str
            Name of the tile type to search for

        Returns
        -------
        tuple[set[int], set[int]]
            (row_indices, column_indices) where the tile type appears
        """
        rows: set[int] = set()
        cols: set[int] = set()

        for row_idx, row in enumerate(self.tile):
            for col_idx, tile in enumerate(row):
                if tile is not None and tile.name == tile_name:
                    rows.add(row_idx)
                    cols.add(col_idx)

        return rows, cols
