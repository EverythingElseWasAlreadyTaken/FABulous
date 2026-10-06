"""Class for generating and managing the geometry of BELs."""

from fabulous.fabric_definition.bel import Bel
from fabulous.fabric_definition.define import IO, BelPortKind
from fabulous.fabric_definition.port import BelPort
from fabulous.geometry_generator.port_geometry import PortGeometry, PortType


class BelGeometry:
    """A data structure representing the geometry of a bel.

    Sets all attributes to default values: None for names/sources,
    zero for dimensions and coordinates, and empty lists for
    port names and geometries.

    Attributes
    ----------
    name : str | None
        Name of the bel
    src : str | None
        File path of the bel HDL source file
    width : int
        Width of the bel
    height : int
        Height of the bel
    relX : int
        X coordinate of the bel, relative within the tile
    relY : int
        Y coordinate of the bel, relative within the tile
    internalPortGeoms : list[PortGeometry]
        List of geometries of the internal ports of the bel
    externalPortGeoms : list[PortGeometry]
        List of geometries of the external ports of the bel
    """

    name: str | None
    src: str | None
    width: int
    height: int
    relX: int
    relY: int
    internalPortGeoms: list[PortGeometry]
    externalPortGeoms: list[PortGeometry]

    def __init__(self) -> None:
        self.name = None
        self.src = None
        self.width = 0
        self.height = 0
        self.relX = 0
        self.relY = 0
        self.internalPortGeoms = []
        self.externalPortGeoms = []

    def generateGeometry(self, bel: Bel, padding: int) -> None:
        """Generate the geometry for a BEL (Basic Element).

        Creates the geometric representation of a BEL including its dimensions
        and port layout. The height is determined by the maximum number of
        ports on either side plus padding, while width is currently fixed.

        Parameters
        ----------
        bel : Bel
            The BEL object to generate the geometry for
        padding : int
            The padding space to add around the BEL
        """
        self.name = bel.name
        self.src = bel.src
        internal = bel.get_ports(BelPortKind.INTERNAL, IO.INPUT) + bel.get_ports(
            BelPortKind.INTERNAL, IO.OUTPUT
        )
        external = bel.get_ports(BelPortKind.EXTERNAL, IO.INPUT) + bel.get_ports(
            BelPortKind.EXTERNAL, IO.OUTPUT
        )
        pins = max(sum(p.width for p in internal), sum(p.width for p in external))
        self.height = pins + padding
        self.width = 32  # TODO: Deduce width in a meaningful way?

        # Internal ports face the switch matrix (left edge) under their tile
        # name; external ones face the fabric top (right edge) without prefix.
        self.internalPortGeoms = self._portGeoms(internal, 0, padding, prefixed=True)
        self.externalPortGeoms = self._portGeoms(
            external, self.width, padding, prefixed=False
        )

    @staticmethod
    def _portGeoms(
        ports: list[BelPort], x: int, padding: int, prefixed: bool
    ) -> list[PortGeometry]:
        """Return one port geometry per pin, stacked down from the BEL's top."""
        geoms = []
        y = padding // 2
        for port in ports:
            for pin in port.pins:
                name = pin.name() if prefixed else pin.name().removeprefix(port.prefix)
                portGeom = PortGeometry()
                portGeom.generateGeometry(
                    name, name, name, PortType.BEL, port.io_direction, x, y
                )
                geoms.append(portGeom)
                y += 1
        return geoms

    def adjustPos(self, relX: int, relY: int) -> None:
        """Adjust the position of the BEL within its containing tile.

        Updates the relative X and Y coordinates of the BEL to position
        it correctly within the tile layout.

        Parameters
        ----------
        relX : int
            New relative X coordinate within the tile
        relY : int
            New relative Y coordinate within the tile
        """
        self.relX = relX
        self.relY = relY

    def saveToCSV(self, writer: object) -> None:
        """Save BEL geometry data to CSV format.

        Writes the BEL geometry information including name, source file,
        position, dimensions, and all port geometries to a CSV file
        using the provided writer.

        Parameters
        ----------
        writer : object
            The CSV writer object to use for output
        """
        writer.writerows(
            [
                ["BEL"],
                ["Name"] + [self.name],
                ["Src"] + [self.src],
                ["RelX"] + [str(self.relX)],
                ["RelY"] + [str(self.relY)],
                ["Width"] + [str(self.width)],
                ["Height"] + [str(self.height)],
                [],
            ]
        )

        for portGeom in self.internalPortGeoms:
            portGeom.saveToCSV(writer)

        for portGeom in self.externalPortGeoms:
            portGeom.saveToCSV(writer)
