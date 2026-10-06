"""Switch matrix geometry definitions."""

from pathlib import Path

from fabulous.fabric_definition.define import IO, Side
from fabulous.fabric_definition.port import (
    NULL_PORT_NAME,
    SwitchMatrixPort,
    TilePort,
)
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
    ports : dict[Side, list[SwitchMatrixPort]]
        The matrix ports wired to the tile's routing ports, per side of the
        tile, nearest neighbour first
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
    ports: dict[Side, list[SwitchMatrixPort]]
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
        self.ports = {}
        self.jump_wires = []
        self.portGeoms = []
        self.northWiresReservedWidth = 0
        self.southWiresReservedWidth = 0
        self.eastWiresReservedHeight = 0
        self.westWiresReservedHeight = 0
        self.southPortsTopY = 0
        self.westPortsRightX = 0

    @staticmethod
    def _offset(sm_port: SwitchMatrixPort) -> int:
        """Return the offset of the routing port a matrix port is wired to."""
        declaration = sm_port.origin.declaration
        if sm_port.origin.side_of_tile in (Side.NORTH, Side.SOUTH):
            return declaration.y_offset
        return declaration.x_offset

    @staticmethod
    def drawn_offset(sm_port: SwitchMatrixPort) -> int:
        """Return the offset the wires of a matrix port are drawn with.

        A line naming both ends and spanning several tiles is staged through
        the tile and drawn as a stair. Any other spanning line (a terminator's
        half) hands all its bits to the matrix, drawn as direct wires.
        """
        offset = SmGeometry._offset(sm_port)
        declaration = sm_port.origin.declaration
        staged = declaration.begin is not None and declaration.end is not None
        return offset if staged or abs(offset) <= 1 else 1

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
        routing = [
            p for p in tile.switch_matrix.ports if isinstance(p.origin, TilePort)
        ]
        # Nearest neighbour first: the wire generation relies on this order.
        self.ports = {
            side: sorted(
                (p for p in routing if p.origin.side_of_tile == side),
                key=lambda p: abs(self._offset(p)),
            )
            for side in (Side.NORTH, Side.SOUTH, Side.EAST, Side.WEST)
        }

        def wires(*sides: Side) -> int:
            return sum(p.width for side in sides for p in self.ports[side])

        def reserved(side: Side) -> int:
            return sum(
                abs(self._offset(p)) * p.origin.declaration.wire_count
                for p in self.ports[side]
            )

        def stair_gap(*sides: Side) -> int:
            return sum(
                p.origin.declaration.wire_count
                for side in sides
                for p in self.ports[side]
                if abs(self._offset(p)) > 1
            )

        jumpWires = sum([wire.wire_count for wire in self.jump_wires])
        self.northWiresReservedWidth = reserved(Side.NORTH)
        self.southWiresReservedWidth = reserved(Side.SOUTH)
        self.eastWiresReservedHeight = reserved(Side.EAST)
        self.westWiresReservedHeight = reserved(Side.WEST)

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
            portsGapWest = stair_gap(Side.NORTH, Side.SOUTH) + padding

        if tileBorder == Border.EASTWEST or tileBorder == Border.CORNER:
            portsGapSouth = 0
        else:
            portsGapSouth = stair_gap(Side.EAST, Side.WEST) + padding

        belsHeightTotal = sum([belGeom.height for belGeom in belGeoms])
        belPadding = padding // 2
        belsPaddingTotal = (len(belGeoms) + 1) * belPadding
        belsReservedSpace = belsHeightTotal + belsPaddingTotal

        self.width = (
            max(wires(Side.EAST, Side.WEST) + portsGapSouth, jumpWires) + 2 * padding
        )
        self.height = max(
            wires(Side.NORTH, Side.SOUTH) + portsGapWest + 2 * padding,
            belsReservedSpace,
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

        # Start position and step of each side's ports along the matrix edge.
        layout = {
            Side.NORTH: (0, padding, 0, 1),
            Side.SOUTH: (0, self.height - padding, 0, -1),
            Side.EAST: (self.width - padding, self.height, -1, 0),
            Side.WEST: (padding, self.height, 1, 0),
        }
        for side, (x, y, dx, dy) in layout.items():
            for sm_port in self.ports[side]:
                declaration = sm_port.origin.declaration
                begin = declaration.begin or NULL_PORT_NAME
                end = declaration.end or NULL_PORT_NAME
                for i, pin in enumerate(sm_port.pins):
                    portGeom = PortGeometry()
                    portGeom.generateGeometry(
                        pin.name(),
                        f"{begin}{i}",
                        f"{end}{i}",
                        PortType.SWITCH_MATRIX,
                        sm_port.io_direction,
                        x,
                        y,
                    )
                    portGeom.side_of_tile = side
                    portGeom.offset = self.drawn_offset(sm_port)
                    portGeom.wire_direction = declaration.direction
                    portGeom.groupId = PortGeometry.nextId
                    portGeom.groupWires = sm_port.width
                    self.portGeoms.append(portGeom)
                    x, y = x + dx, y + dy
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
