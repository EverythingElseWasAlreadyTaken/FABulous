"""Switch matrix geometry definitions."""

from pathlib import Path

from fabulous.fabric_definition.define import IO, Side
from fabulous.fabric_definition.port import TilePort
from fabulous.fabric_definition.tile import Tile
from fabulous.fabric_definition.wire import JumpWire
from fabulous.geometry_generator.bel_geometry import BelGeometry
from fabulous.geometry_generator.geometry_obj import Border, oppositeIO
from fabulous.geometry_generator.port_geometry import PortGeometry, PortType


class SmGeometry:
    """A data structure representing the geometry of a Switch Matrix.

    Sets all attributes to default values: None for names and paths,
    zero for dimensions and coordinates, and empty lists for ports
    and port geometries.

    Attributes
    ----------
    name : str
        Name of the switch matrix
    src : Path
        File path of the switch matrix HDL source file
    csv : Path
        File path of the switch matrix CSV file
    width : int
        Width of the switch matrix
    height : int
        Height of the switch matrix
    relX : int
        X coordinate of the switch matrix, relative within the tile
    relY : int
        Y coordinate of the switch matrix, relative within the tile
    northPorts : list[TilePort]
        List of the ports of the switch matrix in north direction
    southPorts : list[TilePort]
        List of the ports of the switch matrix in south direction
    eastPorts : list[TilePort]
        List of the ports of the switch matrix in east direction
    westPorts : list[TilePort]
        List of the ports of the switch matrix in west direction
    jump_wires : list[JumpWire]
        The tile's jump wires, each drawn as one port per bit
    portGeoms : list[PortGeometry]
        List of geometries of the ports of the switch matrix
    northWiresReservedWidth : int
        Reserved width for wires going north
    southWiresReservedWidth : int
        Reserved width for wires going south
    eastWiresReservedHeight : int
        Reserved height for wires going east
    westWiresReservedHeight : int
        Reserved height for wires going west
    southPortsTopY : int
        Top most y coord of any south port, reference for stair-wires
    westPortsRightX : int
        Right most x coord of any west port, reference for stair-wires
    """

    name: str
    src: Path
    csv: Path
    width: int
    height: int
    relX: int
    relY: int
    northPorts: list[TilePort]
    southPorts: list[TilePort]
    eastPorts: list[TilePort]
    westPorts: list[TilePort]
    jump_wires: list[JumpWire]
    portGeoms: list[PortGeometry]
    northWiresReservedWidth: int
    southWiresReservedWidth: int
    eastWiresReservedHeight: int
    westWiresReservedHeight: int
    southPortsTopY: int
    westPortsRightX: int

    def __init__(self) -> None:
        self.name = None
        self.src = None
        self.csv = None
        self.width = 0
        self.height = 0
        self.relX = 0
        self.relY = 0
        self.northPorts = []
        self.southPorts = []
        self.eastPorts = []
        self.westPorts = []
        self.jump_wires = []
        self.portGeoms = []
        self.northWiresReservedWidth = 0
        self.southWiresReservedWidth = 0
        self.eastWiresReservedHeight = 0
        self.westWiresReservedHeight = 0
        self.southPortsTopY = 0
        self.westPortsRightX = 0

    def preprocessPorts(self, tileBorder: Border) -> None:
        """Order the ports for downstream drawing.

        Ensure that ports are ordered correctly, merge connected jump ports and augment
        ports for term tiles.
        This step augments ports in border tiles.
        This is needed, as these are not contained in the (north...west)SidePorts
        in FABulous.
        """
        # This step ensures correct ordering, this is important
        # for the wire generation step.
        self.northPorts = sorted(self.northPorts, key=lambda port: abs(port.y_offset))
        self.southPorts = sorted(self.southPorts, key=lambda port: abs(port.y_offset))
        self.eastPorts = sorted(self.eastPorts, key=lambda port: abs(port.x_offset))
        self.westPorts = sorted(self.westPorts, key=lambda port: abs(port.x_offset))

        # This step augments ports in border tiles.
        # This is needed, as these are not contained
        # in the (north...west)SidePorts in FABulous.
        if tileBorder == Border.NORTHSOUTH or tileBorder == Border.CORNER:
            augmentedSouthPorts = []
            for southPort in self.southPorts:
                if abs(southPort.y_offset) > 1:
                    augmentedPort = TilePort(
                        name=southPort.name,
                        io_direction=southPort.io_direction,
                        side_of_tile=southPort.side_of_tile,
                        wire_direction=southPort.wire_direction,
                        source_name=southPort.source_name,
                        x_offset=0,
                        y_offset=1,
                        destination_name=southPort.destination_name,
                        wire_count=southPort.wire_count * abs(southPort.y_offset),
                    )
                    augmentedSouthPorts.append(augmentedPort)
                else:
                    augmentedSouthPorts.append(southPort)
            self.southPorts = augmentedSouthPorts

            augmentedNorthPorts = []
            for northPort in self.northPorts:
                if abs(northPort.y_offset) > 1:
                    augmentedPort = TilePort(
                        name=northPort.name,
                        io_direction=northPort.io_direction,
                        side_of_tile=northPort.side_of_tile,
                        wire_direction=northPort.wire_direction,
                        source_name=northPort.source_name,
                        x_offset=0,
                        y_offset=1,
                        destination_name=northPort.destination_name,
                        wire_count=northPort.wire_count * abs(northPort.y_offset),
                    )
                    augmentedNorthPorts.append(augmentedPort)
                else:
                    augmentedNorthPorts.append(northPort)
            self.northPorts = augmentedNorthPorts

        if tileBorder == Border.EASTWEST or tileBorder == Border.CORNER:
            augmentedEastPorts = []
            for eastPort in self.eastPorts:
                if abs(eastPort.x_offset) > 1:
                    augmentedPort = TilePort(
                        name=eastPort.name,
                        io_direction=eastPort.io_direction,
                        side_of_tile=eastPort.side_of_tile,
                        wire_direction=eastPort.wire_direction,
                        source_name=eastPort.source_name,
                        x_offset=1,
                        y_offset=0,
                        destination_name=eastPort.destination_name,
                        wire_count=eastPort.wire_count * abs(eastPort.x_offset),
                    )
                    augmentedEastPorts.append(augmentedPort)
                else:
                    augmentedEastPorts.append(eastPort)
            self.eastPorts = augmentedEastPorts

            augmentedWestPorts = []
            for westPort in self.westPorts:
                if abs(westPort.x_offset) > 1:
                    augmentedPort = TilePort(
                        name=westPort.name,
                        io_direction=westPort.io_direction,
                        side_of_tile=westPort.side_of_tile,
                        wire_direction=westPort.wire_direction,
                        source_name=westPort.source_name,
                        x_offset=1,
                        y_offset=0,
                        destination_name=westPort.destination_name,
                        wire_count=westPort.wire_count * abs(westPort.x_offset),
                    )
                    augmentedWestPorts.append(augmentedPort)
                else:
                    augmentedWestPorts.append(westPort)
            self.westPorts = augmentedWestPorts

    def generateGeometry(
        self, tile: Tile, tileBorder: Border, belGeoms: list[BelGeometry], padding: int
    ) -> None:
        """Generate the geometry for a switch matrix.

        Creates the geometric representation of a switch matrix including its
        dimensions, port arrangements, and spatial relationships. Calculates
        the required space for routing wires and positions the switch matrix
        within the tile.
        the required space for routing wires and positions for the switch matrix

        Parameters
        ----------
        tile : Tile
            The tile object containing the switch matrix definition
        tileBorder : Border
            The border type of the tile within the fabric
        belGeoms : list[BelGeometry]
            List of BEL geometries within the same tile
        padding : int
            The padding space to add around the switch matrix
        """
        self.name = f"{tile.name}_switch_matrix"
        self.src = tile.tileDir.parent.joinpath(f"{self.name}.v")
        # A switch-matrix .csv is no longer generated by default; record the real file.
        self.csv = tile.switch_matrix.matrix_file

        self.jump_wires = tile.jump_wires
        self.northPorts = tile.getNorthSidePorts()
        self.southPorts = tile.getSouthSidePorts()
        self.eastPorts = tile.getEastSidePorts()
        self.westPorts = tile.getWestSidePorts()
        self.preprocessPorts(tileBorder)

        # Counting the total number of wires for each direction
        northWires = sum([port.wire_count for port in self.northPorts])
        southWires = sum([port.wire_count for port in self.southPorts])
        eastWires = sum([port.wire_count for port in self.eastPorts])
        westWires = sum([port.wire_count for port in self.westPorts])
        jumpWires = sum([wire.wire_count for wire in self.jump_wires])

        self.northWiresReservedWidth = sum(
            [abs(port.y_offset) * port.wire_count for port in self.northPorts]
        )
        self.southWiresReservedWidth = sum(
            [abs(port.y_offset) * port.wire_count for port in self.southPorts]
        )
        self.eastWiresReservedHeight = sum(
            [abs(port.x_offset) * port.wire_count for port in self.eastPorts]
        )
        self.westWiresReservedHeight = sum(
            [abs(port.x_offset) * port.wire_count for port in self.westPorts]
        )

        self.relX = (
            max(self.northWiresReservedWidth, self.southWiresReservedWidth)
            + 2 * padding
        )
        self.relY = padding

        # These gaps are for the stair-like wires,
        # hence they're not needed for border tiles,
        # where no stair-like wires are generated.
        if tileBorder == Border.NORTHSOUTH or tileBorder == Border.CORNER:
            portsGapWest = 0
        else:
            portsGapWest = sum(
                [
                    port.wire_count
                    for port in (self.northPorts + self.southPorts)
                    if abs(port.y_offset) > 1
                ]
            )
            portsGapWest += padding

        if tileBorder == Border.EASTWEST or tileBorder == Border.CORNER:
            portsGapSouth = 0
        else:
            portsGapSouth = sum(
                [
                    port.wire_count
                    for port in (self.eastPorts + self.westPorts)
                    if abs(port.x_offset) > 1
                ]
            )
            portsGapSouth += padding

        belsHeightTotal = sum([belGeom.height for belGeom in belGeoms])
        belPadding = padding // 2
        belsPaddingTotal = (len(belGeoms) + 1) * belPadding
        belsReservedSpace = belsHeightTotal + belsPaddingTotal

        self.width = max(eastWires + westWires + portsGapSouth, jumpWires) + 2 * padding
        self.height = max(
            southWires + northWires + portsGapWest + 2 * padding, belsReservedSpace
        )
        self.generatePortsGeometry(padding)

        self.southPortsTopY = min(
            [geom.relY for geom in self.portGeoms if geom.side_of_tile == Side.SOUTH]
            + [self.height]
        )
        self.westPortsRightX = max(
            [geom.relX for geom in self.portGeoms if geom.side_of_tile == Side.WEST]
            + [0]
        )

    def generatePortsGeometry(self, padding: int) -> None:
        """Generate the geometry for all ports of the switch matrix.

        Creates `PortGeometry` objects for all jump, north, south, east, and west
        ports of the switch matrix. Positions each port according to its type
        and assigns appropriate coordinates and grouping information.

        Parameters
        ----------
        padding : int
            The padding space to add around ports
        """
        jumpPortX = padding
        jumpPortY = 0
        for wire in self.jump_wires:
            for i in range(wire.wire_count):
                source = f"NULL{i}" if wire.source is None else wire.source[i].name()
                dest = (
                    f"NULL{i}"
                    if wire.destination is None
                    else wire.destination[i].name()
                )
                if wire.source is not None and wire.destination is not None:
                    io = IO.INOUT
                elif wire.source is not None:
                    io = IO.OUTPUT
                else:
                    io = IO.INPUT
                portGeom = PortGeometry()
                portGeom.generateGeometry(
                    source if wire.source is not None else dest,
                    source,
                    dest,
                    PortType.JUMP,
                    io,
                    jumpPortX,
                    jumpPortY,
                )
                self.portGeoms.append(portGeom)
                jumpPortX += 1

        northPortX = 0
        northPortY = padding
        for port in self.northPorts:
            for i in range(port.wire_count):
                portGeom = PortGeometry()
                portGeom.generateGeometry(
                    f"{port.name}{i}",
                    f"{port.source_name}{i}",
                    f"{port.destination_name}{i}",
                    PortType.SWITCH_MATRIX,
                    port.io_direction,
                    northPortX,
                    northPortY,
                )
                portGeom.side_of_tile = port.side_of_tile
                portGeom.offset = port.y_offset
                portGeom.wire_direction = port.wire_direction
                portGeom.groupId = PortGeometry.nextId
                portGeom.groupWires = port.wire_count

                self.portGeoms.append(portGeom)
                northPortY += 1
            PortGeometry.nextId += 1

        southPortX = 0
        southPortY = self.height - padding
        for port in self.southPorts:
            for i in range(port.wire_count):
                portGeom = PortGeometry()
                portGeom.generateGeometry(
                    f"{port.name}{i}",
                    f"{port.source_name}{i}",
                    f"{port.destination_name}{i}",
                    PortType.SWITCH_MATRIX,
                    port.io_direction,
                    southPortX,
                    southPortY,
                )
                portGeom.side_of_tile = port.side_of_tile
                portGeom.offset = port.y_offset
                portGeom.wire_direction = port.wire_direction
                portGeom.groupId = PortGeometry.nextId
                portGeom.groupWires = port.wire_count

                self.portGeoms.append(portGeom)
                southPortY -= 1
            PortGeometry.nextId += 1

        eastPortX = self.width - padding
        eastPortY = self.height
        for port in self.eastPorts:
            for i in range(port.wire_count):
                portGeom = PortGeometry()
                portGeom.generateGeometry(
                    f"{port.name}{i}",
                    f"{port.source_name}{i}",
                    f"{port.destination_name}{i}",
                    PortType.SWITCH_MATRIX,
                    port.io_direction,
                    eastPortX,
                    eastPortY,
                )
                portGeom.side_of_tile = port.side_of_tile
                portGeom.offset = port.x_offset
                portGeom.wire_direction = port.wire_direction
                portGeom.groupId = PortGeometry.nextId
                portGeom.groupWires = port.wire_count

                self.portGeoms.append(portGeom)
                eastPortX -= 1
            PortGeometry.nextId += 1

        westPortX = padding
        westPortY = self.height
        for port in self.westPorts:
            for i in range(port.wire_count):
                portGeom = PortGeometry()
                portGeom.generateGeometry(
                    f"{port.name}{i}",
                    f"{port.source_name}{i}",
                    f"{port.destination_name}{i}",
                    PortType.SWITCH_MATRIX,
                    port.io_direction,
                    westPortX,
                    westPortY,
                )
                portGeom.side_of_tile = port.side_of_tile
                portGeom.offset = port.x_offset
                portGeom.wire_direction = port.wire_direction
                portGeom.groupId = PortGeometry.nextId
                portGeom.groupWires = port.wire_count

                self.portGeoms.append(portGeom)
                westPortX += 1
            PortGeometry.nextId += 1

    def generateBelPorts(self, belGeomList: list[BelGeometry]) -> None:
        """Generate port geometries for BEL connections to the switch matrix.

        Creates `PortGeometry` objects for connecting BEL internal ports to the
        switch matrix. These ports facilitate routing between BELs and the
        switch matrix interconnect network.

        Parameters
        ----------
        belGeomList : list[BelGeometry]
            List of BEL geometries to connect to the switch matrix
        """
        for belGeom in belGeomList:
            for belPortGeom in belGeom.internalPortGeoms:
                portX = self.width
                portY = belGeom.relY - self.relY + belPortGeom.relY

                portGeom = PortGeometry()
                portGeom.generateGeometry(
                    belPortGeom.name,
                    belPortGeom.source_name,
                    belPortGeom.destName,
                    PortType.SWITCH_MATRIX,
                    oppositeIO(belPortGeom.io_direction),
                    portX,
                    portY,
                )
                self.portGeoms.append(portGeom)

    def saveToCSV(self, writer: object) -> None:
        """Save switch matrix geometry data to CSV format.

        Writes the switch matrix geometry information including name, source
        and CSV file paths, position, dimensions, and all port geometries
        to a CSV file using the provided writer.

        Parameters
        ----------
        writer : object
            The CSV `writer` object to use for output
        """
        writer.writerows(
            [
                ["SWITCH_MATRIX"],
                ["Name"] + [self.name],
                ["Src"] + [self.src],
                ["Csv"] + [self.csv],
                ["RelX"] + [str(self.relX)],
                ["RelY"] + [str(self.relY)],
                ["Width"] + [str(self.width)],
                ["Height"] + [str(self.height)],
                [],
            ]
        )

        for portGeom in self.portGeoms:
            portGeom.saveToCSV(writer)
