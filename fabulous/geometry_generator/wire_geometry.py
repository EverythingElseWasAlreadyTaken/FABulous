"""Wire geometry classes for FABulous FPGA routing structures.

This module provides classes for representing wire geometries within FPGA tiles,
including simple wires and complex stair-like wire structures for multi-tile routing. It
supports CSV serialization for integration with geometry files.
"""

from csv import writer as csvWriter

from fabulous.custom_exception import InvalidPortType
from fabulous.fabric_definition.define import Direction
from fabulous.geometry_generator.geometry_obj import Location


class WireGeometry:
    """A data structure representing the geometry of a wire within a tile.

    Attributes
    ----------
    name : str
        Name of the wire
    path : list[Location]
        Path of the wire

    Parameters
    ----------
    name : str
        Name of the wire
    """

    name: str
    path: list[Location]

    def __init__(self, name: str) -> None:
        self.name = name
        self.path = []

    def addPathLoc(self, pathLoc: Location) -> None:
        """Add a location point to the wire path.

        Parameters
        ----------
        pathLoc : Location
            The location point to add to the wire path
        """
        self.path.append(pathLoc)

    def saveToCSV(self, writer: csvWriter) -> None:
        """Save wire geometry data to CSV format.

        Writes the wire name and all path points with their coordinates
        to a CSV file using the provided writer.

        Parameters
        ----------
        writer : csvWriter
            The CSV writer object to use for output
        """
        writer.writerows([["WIRE"], ["Name"] + [self.name]])
        for pathPoint in self.path:
            writer.writerows(
                [["RelX"] + [str(pathPoint.x)], ["RelY"] + [str(pathPoint.y)]]
            )
        writer.writerow([])


class StairWires:
    """A data structure representing a stair-like collection of wires.

    Parameters
    ----------
    name : str
        The name of the stair wire structure

    Attributes
    ----------
    name : str
        Name of the structure
    refX : int
        Reference point x coord of the stair structure
    refY : int
        Reference point y coord of the stair structure
    offset : int
        Offset of the wires
    direction : Direction
        Direction of the wires
    groupWires : int
        Amount of wires of a single "strand"
    tileWidth : int
        Width of the tile containing the wires
    tileHeight : int
        Height of the tile containing the wires
    wireGeoms : list[WireGeometry]
        List of the wires geometries

    Notes
    -----
    The (refX, refY) point refers to the following location(s) of the
    stair-like structure:

        |        @   @   @                  @@  @@  @@
        |        @   @   @                  @@  @@  @@
        |        @   @   @                  @@  @@  @@
        |        @   @   @                  @@  @@  @@
        |        @   @   @                  @@  @@  @@
        |        @   @   @@@@@@@@     @@@@@@@@  @@  @@
        |        @   @         @@     @         @@  @@
        |        @   @@@@@@@.  @@     @   @@@@@@@@  @@
        |        @         @.  @@     @   @@        @@
        |    --> @@@@@@.@  @.  @@     @   @. .@@@@@@@@  <-- (refX, refY)
        |              .@  @.  @@     @   @. .@
        |              .@  @.  @@     @   @. .@
        |              .@  @.  @@     @   @. .@
        |              .@  @.  @@     @   @. .@
        |              .@  @@  @@     @   @. .@

    Depending on the orientation of the structure, rotate right by 90° to get
    the image for the corresponding left-right stair-like wire structure.
    The right stair-like structure represents a north stair; the left one
    represents a south stair (these being the directions of the wires).
    """

    name: str
    refX: int
    refY: int
    offset: int
    direction: Direction
    groupWires: int
    tileWidth: int
    tileHeight: int
    wireGeoms: list[WireGeometry]

    def __init__(self, name: str) -> None:
        self.name = name
        self.refX = 0
        self.refY = 0
        self.offset = 0
        self.direction = None
        self.groupWires = 0
        self.tileWidth = 0
        self.tileHeight = 0
        self.wireGeoms = []

    def generateGeometry(
        self,
        refX: int,
        refY: int,
        offset: int,
        direction: Direction,
        groupWires: int,
        tileWidth: int,
        tileHeight: int,
    ) -> None:
        """Generate the stair wire geometry based on parameters and direction.

        Creates the stair-like wire structure: `groupWires * (|offset| - 1)`
        wires, vertical ones for a north / south stair, horizontal ones for an
        east / west stair.

        Parameters
        ----------
        refX : int
            Reference X coordinate for the stair structure
        refY : int
            Reference Y coordinate for the stair structure
        offset : int
            Wire offset distance
        direction : Direction
            Direction of the wire routing (NORTH, SOUTH, EAST, or WEST)
        groupWires : int
            Number of wires in each group or strand
        tileWidth : int
            Width of the containing tile
        tileHeight : int
            Height of the containing tile

        Raises
        ------
        InvalidPortType
            If the direction is invalid for stair wires.
        """
        self.refX = refX
        self.refY = refY
        self.offset = offset
        self.direction = direction
        self.groupWires = groupWires
        self.tileWidth = tileWidth
        self.tileHeight = tileHeight

        if direction not in (
            Direction.NORTH,
            Direction.SOUTH,
            Direction.EAST,
            Direction.WEST,
        ):
            raise InvalidPortType(f"Invalid direction {direction} for stair wires!")

        # Each wire enters at one tile edge, steps aside by a strand
        # (`groupWires`) and leaves at the opposite edge; the next wire starts
        # one track further along both axes.
        refX, refY, strand = self.refX, self.refY, self.groupWires
        for i in range(strand * (abs(offset) - 1)):
            if direction in (Direction.NORTH, Direction.SOUTH):
                side = -1 if direction == Direction.NORTH else 1
                path = [
                    (refX, 0),
                    (refX, refY),
                    (refX + side * strand, refY),
                    (refX + side * strand, tileHeight),
                ]
                refX, refY = refX + side, refY - 1
            else:
                side = 1 if direction == Direction.EAST else -1
                path = [
                    (tileWidth, refY),
                    (refX, refY),
                    (refX, refY + side * strand),
                    (0, refY + side * strand),
                ]
                refX, refY = refX + 1, refY + side
            wireGeom = WireGeometry(f"{self.name} #{i}")
            for x, y in path:
                wireGeom.addPathLoc(Location(x, y))
            self.wireGeoms.append(wireGeom)

    def saveToCSV(self, writer: csvWriter) -> None:
        """Save all stair wire geometries to CSV format.

        Writes all individual wire geometries in the stair structure
        to a CSV file using the provided writer.

        Parameters
        ----------
        writer : csvWriter
            The CSV writer object to use for output
        """
        for wireGeom in self.wireGeoms:
            wireGeom.saveToCSV(writer)


class WireConstraints:
    """Store information on where wires arrive at the border of a tile.

    Attributes
    ----------
    northPositions : list[int]
        Positions where wires arrive at the north border
    southPositions : list[int]
        Positions where wires arrive at the south border
    eastPositions : list[int]
        Positions where wires arrive at the east border
    westPositions : list[int]
        Positions where wires arrive at the west border
    """

    northPositions: list[int]
    southPositions: list[int]
    eastPositions: list[int]
    westPositions: list[int]

    def __init__(self) -> None:
        self.northPositions = []
        self.southPositions = []
        self.eastPositions = []
        self.westPositions = []

    def addConstraintsOf(self, stairWires: StairWires) -> None:
        """Add constraints from a stair wires structure.

        Parameters
        ----------
        stairWires : StairWires
            The stair wires structure to extract constraints from
        """
        # A stair wire starts at one tile edge and ends at the opposite one.
        for wireGeom in stairWires.wireGeoms:
            start, end = wireGeom.path[0], wireGeom.path[-1]
            if stairWires.direction in (Direction.NORTH, Direction.SOUTH):
                self.northPositions.append(start.x)
                self.southPositions.append(end.x)
            else:
                self.eastPositions.append(start.y)
                self.westPositions.append(end.y)
