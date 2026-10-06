"""Classes for generating and managing the geometry of FPGA fabrics."""

from csv import writer as csvWriter
from pathlib import Path

from loguru import logger

from fabulous.fabric_definition.define import grid_at
from fabulous.fabric_definition.fabric import Fabric
from fabulous.fabric_definition.instance import TileInstance
from fabulous.geometry_generator.geometry_obj import Border, Location
from fabulous.geometry_generator.tile_geometry import TileGeometry

GENERATOR_VERSION = "1.0.0"


class FabricGeometry:
    """Fetch and hold geometric information about a fabric.

    Objects of this class can be constructed by passing a `Fabric` object
    and optionally, padding.

    Creates the fabric geometry by processing the given fabric definition
    and automatically generating the complete geometric layout.

    Parameters
    ----------
    fabric : Fabric
        The fabric object from CSV definition files
    padding : int, optional
        Padding used throughout the geometry, by default 8

    Attributes
    ----------
    fabric : Fabric
        The fabric object passed from the CSV definition files
    tileNames : set[str]
        Set of unique tileNames in the fabric
    tileGeomMap : dict[str, TileGeometry]
        Map of the geometry of each tile by name
    tileLocs : list[list[Location]]
        Locations of all tiles in the fabric
    padding : int
        Padding used throughout the geometry, in multiples of the width between wires
    width : int
        Width of the fabric
    height : int
        Height of the fabric
    """

    fabric: Fabric
    tileNames: set[str]
    tileGeomMap: dict[str, TileGeometry]
    tileLocs: list[list[Location]]
    padding: int
    width: int
    height: int

    def __init__(self, fabric: Fabric, padding: int = 8) -> None:
        fabric.check_routing_channels()
        self.fabric = fabric
        self.tileNames = set()
        self.tileGeomMap = {}
        self.tileLocs = []
        self.padding = padding
        self.width = 0
        self.height = 0

        self.generateGeometry()

    @property
    def _placed(self) -> list[TileInstance]:
        """The placed tiles, in row-major order."""
        return [i for row in self.fabric.instances for i in row if i is not None]

    def genNeighbourConstraints(self, queried: TileGeometry) -> None:
        """Take the wire constraints of a neighbour on the queried tile's border.

        A tile on a north/south border lines its direct wires up with the tile
        below it (or above, if that cell is empty), one on an east/west border
        with the tile to its right (or left); T-shaped fabrics can have an
        empty cell on one side inside the grid. The last placement of the tile
        type with such a neighbour decides.

        Parameters
        ----------
        queried : TileGeometry
            The tile geometry to generate constraints for
        """
        steps = {
            Border.NORTHSOUTH: ((0, 1), (0, -1)),
            Border.EASTWEST: ((1, 0), (-1, 0)),
        }.get(queried.border, ())
        for instance in self._placed:
            if self.tileGeomMap[instance.tile_type.name] is not queried:
                continue
            neighbours = (
                grid_at(self.fabric.tile, instance.x + dx, instance.y + dy)
                for dx, dy in steps
            )
            neighbour = next((n for n in neighbours if n is not None), None)
            if neighbour is not None:
                queried.neighbourConstraints = self.tileGeomMap[
                    neighbour.name
                ].wireConstraints

    def generateGeometry(self) -> None:
        """Generate the geometric information from the given fabric object.

        The border attribute is set for tiles that are located at a border of the
        fabric: such a tile reserves no space for stair-like wires and lines its
        direct wires up with its neighbour. The distinction left/right and
        top/bottom is made per axis.
        """
        grid = self.fabric.tile
        for instance in self._placed:
            tile, x, y = instance.tile_type, instance.x, instance.y
            self.tileNames.add(tile.name)
            tileGeom = self.tileGeomMap.setdefault(tile.name, TileGeometry())
            # A tile sits on a north/south border if the cell above or below is
            # off the grid or NULL; the latter lets termination tiles on the
            # inner edge of T-shaped fabrics be classified correctly.
            northSouth = (
                grid_at(grid, x, y - 1) is None or grid_at(grid, x, y + 1) is None
            )
            eastWest = (
                grid_at(grid, x - 1, y) is None or grid_at(grid, x + 1, y) is None
            )
            if northSouth and eastWest:
                tileGeom.border = Border.CORNER
            elif northSouth:
                tileGeom.border = Border.NORTHSOUTH
            elif eastWest:
                tileGeom.border = Border.EASTWEST

        # generate geometry for central tiles first
        # to avoid conflicts at outer tiles:
        # with the geometry of inner tiles already
        # generated, outer tiles can simply generate
        # their wires such that everything lines up.
        innerTileNames = []
        outerTileNames = []

        for tileName in self.tileNames:
            tileGeom = self.tileGeomMap[tileName]
            self.genNeighbourConstraints(tileGeom)

            if tileGeom.border == Border.NONE:
                innerTileNames.append(tileName)
            else:
                outerTileNames.append(tileName)

        # A supertile-level BEL is drawn in the supertile's master tile, so map
        # each master tile name to the supertile BELs it hosts.
        master_bels: dict[str, list] = {}
        for superTile in self.fabric.superTileDic.values():
            mx, my = superTile.get_master_tile_coords()
            master_tile = superTile.tileMap[my][mx]
            if master_tile is not None:
                master_bels.setdefault(master_tile.name, []).extend(superTile.bels)

        for tileName in self.tileNames:
            tile = self.fabric.getTileByName(tileName)
            tileGeom = self.tileGeomMap[tileName]
            tileGeom.generateGeometry(
                tile, self.padding, extra_bels=master_bels.get(tileName)
            )

        # All tiles of a column take the widest tile's width, all tiles of a
        # row the tallest one's height, so they form a regular grid.
        geoms = [
            [TileGeometry() if t is None else self.tileGeomMap[t.name] for t in row]
            for row in grid
        ]
        columns = list(zip(*geoms, strict=True))
        maxWidths = [max(g.width for g in col) for col in columns]
        maxSmRelXValues = [max(g.smGeometry.relX for g in col) for col in columns]
        maxSmWidths = [max(g.smGeometry.width for g in col) for col in columns]
        maxHeights = [max(g.height for g in row) for row in geoms]
        lowestSmYCoords = [
            max(g.smGeometry.relY + g.smGeometry.height for g in row) for row in geoms
        ]

        for instance in self._placed:
            x, y = instance.x, instance.y
            self.tileGeomMap[instance.tile_type.name].adjustDimensions(
                maxWidths[x], maxHeights[y], maxSmWidths[x], maxSmRelXValues[x]
            )

        self.tileLocs = [
            [
                None if t is None else Location(sum(maxWidths[:x]), sum(maxHeights[:y]))
                for x, t in enumerate(row)
            ]
            for y, row in enumerate(grid)
        ]

        # The top left point of the fabric is the origin (0, 0), so the
        # fabric's dimensions are its rightmost and bottommost points.
        self.width = max(
            (
                self.tileLocs[y][-1].x + self.tileGeomMap[row[-1].name].width
                for y, row in enumerate(grid)
                if row[-1] is not None
            ),
            default=0,
        )
        self.height = max(
            (
                self.tileLocs[-1][x].y + self.tileGeomMap[t.name].height
                for x, t in enumerate(grid[-1])
                if t is not None
            ),
            default=0,
        )

        # Line up the switch matrices of a row at their southern edge, so all
        # inter-tile wires line up; a tile type is adjusted at its first
        # placement.
        adjusted = set()
        for instance in self._placed:
            name = instance.tile_type.name
            if name not in adjusted:
                self.tileGeomMap[name].adjustSmPos(
                    lowestSmYCoords[instance.y], self.padding
                )
                adjusted.add(name)

        # By now, the geometry of the whole fabric is fixed,
        # hence we can start generating the inter-tile wires.
        for tileName in innerTileNames + outerTileNames:
            self.tileGeomMap[tileName].generateWires(self.padding)

    def totalWireLines(self) -> int:
        """Return the total amount of lines (segments) of wires of the fabrics routing.

        Can, for instance, be used to initialize the size of datastructures in the
        frontend.
        """
        return sum(
            self.tileGeomMap[i.tile_type.name].totalWireLines() for i in self._placed
        )

    def saveToCSV(self, fileName: str) -> None:
        """Save geometric information of the given fabric for the graphical frontend.

        Parameters
        ----------
        fileName : str
            The name of the csv file
        """
        logger.info(
            f"Generating geometry csv file for {self.fabric.name} # file name: "
            f"{fileName}"
        )

        with Path(f"{fileName}").open("w", newline="", encoding="utf-8") as file:
            writer = csvWriter(file)

            writer.writerows(
                [
                    ["PARAMS"],
                    ["GeneratorVersion"] + [GENERATOR_VERSION],
                    ["Name"] + [self.fabric.name],
                    ["Rows"] + [str(self.fabric.numberOfRows)],
                    ["Columns"] + [str(self.fabric.numberOfColumns)],
                    ["Width"] + [str(self.width)],
                    ["Height"] + [str(self.height)],
                    ["Lines"] + [str(self.totalWireLines())],
                    [],
                ]
            )

            writer.writerow(["FABRIC_DEF"])
            for i in range(self.fabric.numberOfRows):
                writer.writerow(
                    [
                        tile.name if tile is not None else "Null"
                        for tile in self.fabric.tile[i]
                    ]
                )
            writer.writerow([])

            writer.writerow(["FABRIC_LOCS"])
            for i in range(self.fabric.numberOfRows):
                writer.writerow(
                    [loc if loc is not None else "Null" for loc in self.tileLocs[i]]
                )
            writer.writerows([[], []])

            for tileName in sorted(self.tileNames):
                tileGeometry = self.tileGeomMap[tileName]
                tileGeometry.saveToCSV(writer)

    def __repr__(self) -> str:
        """Return the string representation of the fabric geometry.

        Provides a formatted view of the tile dimensions and locations in a
        grid layout showing the fabric structure.

        Returns
        -------
        str
            Multi-line string showing tile dimensions and locations
        """
        geometry = "Respective dimensions of tiles: \n"
        for i in range(self.fabric.numberOfRows):
            for j in range(self.fabric.numberOfColumns):
                tile = self.fabric.tile[i][j]

                if tile is None:
                    geometry += "Null".ljust(8) + "\t"
                else:
                    geometry += f"{str(self.tileGeomMap[tile.name]).ljust(8)}\t "
            geometry += "\n"
        geometry += "Respective locations of tiles: \n"

        for i in range(self.fabric.numberOfRows):
            for j in range(self.fabric.numberOfColumns):
                loc = self.tileLocs[i][j]
                if loc is None:
                    geometry += "Null".ljust(8) + "\t"
                else:
                    geometry += f"{str(loc).ljust(8)}\t "
            geometry += "\n"
        return geometry
