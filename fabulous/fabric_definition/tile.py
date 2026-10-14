"""Tile class definition for FPGA fabric representation."""

from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from fabulous.fabric_definition.bel import Bel
from fabulous.fabric_definition.configmem import ConfigMem
from fabulous.fabric_definition.connection import (
    BelExternalConnection,
    FixedConnection,
    JumpConnection,
    PortConnection,
    StagingConnection,
    SwitchMatrixConnection,
)
from fabulous.fabric_definition.define import (
    IO,
    BelPortKind,
    Direction,
    PinSortMode,
    Side,
)
from fabulous.fabric_definition.fabric_cell_library import FabricCellFunction
from fabulous.fabric_definition.gen_io import Gen_IO
from fabulous.fabric_definition.port import (
    BelExternalPort,
    Pin,
    Port,
    SJumpPort,
    TilePort,
)
from fabulous.fabric_definition.switch_matrix import SwitchMatrix
from fabulous.fabric_definition.wire import JumpWire

if TYPE_CHECKING:
    from fabulous.fabric_definition.supertile import SuperTile
    from fabulous.fabric_generator.gds_generator.gen_io_pin_config_yaml import (
        PinOrderConfig,
    )


def tile_directory(name: str, tile_dir: Path, matrix_file: Path) -> Path:
    """Return the directory a tile's own files are written to and read from.

    A tile declared inside `fabric.csv` has no CSV of its own, so its switch
    matrix's directory is used. A free function because the parser needs it
    before the `Tile` exists.

    Parameters
    ----------
    name : str
        The tile's name.
    tile_dir : Path
        The CSV the tile is defined in, which `Tile.tileDir` holds.
    matrix_file : Path
        The tile's switch matrix file.

    Returns
    -------
    Path
        The tile's directory, which a `CONFIGMEM` or `MATRIX` entry does not
        move.
    """
    if tile_dir.stem == name:
        return tile_dir.parent
    return matrix_file.parent


@dataclass
class Tile:
    """Store information about a tile.

    Parameters
    ----------
    name : str
        The name of the tile
    ports : list[TilePort]
        The routing channel ports of the tile. Each port is attached to this
        tile.
    bels : list[Bel]
        List of Basic Elements of Logic (BELs) in the tile
    tileDir : Path
        Directory path for the tile
    switch_matrix : SwitchMatrix
        Switch matrix of the tile, holding its source file, connectivity, and
        config-bit count.
    config_mem : ConfigMem
        The tile's configuration memory. It holds no bits until a mapping is
        read or generated, and never does on a FLIPFLOP_CHAIN fabric.
    gen_ios : list[Gen_IO]
        List of general I/O components
    pinOrderConfig : dict[Side, PinOrderConfig] | None, optional
        Configuration for pin ordering on each side of the tile. If None, defaults to
        BUS_MAJOR sorting on all sides.
    jump_wires : list[JumpWire] | None, optional
        The tile-internal wires looping a switch-matrix output back into a
        switch-matrix input. Defaults to none.
    sjump_ports : list[SJumpPort] | None, optional
        The ports facing the switch matrix of the surrounding supertile.
        Defaults to none.

    Attributes
    ----------
    name : str
        The name of the tile
    portsInfo : list[TilePort]
        The routing channel ports of the tile
    jump_wires : list[JumpWire]
        The tile-internal jump wires
    sjump_ports : list[SJumpPort]
        The ports facing the supertile's switch matrix
    bels: list[Bel]
        The list of BELs of the tile
    switch_matrix : SwitchMatrix
        The switch matrix of the tile
    config_mem : ConfigMem
        The tile's configuration memory
    gen_ios : list[Gen_IO]
        The list of GEN_IOs of the tile
    tileDir : Path
        The path to the tile folder
    pinOrderConfig : dict, optional
        Configuration for pin ordering on each side of the tile.

    Raises
    ------
    ValueError
        If the tile has more than 26 BELs (BELs are named by the letters A-Z).
    """

    name: str
    portsInfo: list[TilePort]
    jump_wires: list[JumpWire]
    sjump_ports: list[SJumpPort]
    bels: list[Bel]
    switch_matrix: SwitchMatrix
    config_mem: ConfigMem
    gen_ios: list[Gen_IO]
    tileDir: Path = Path()
    pinOrderConfig: dict = field(default_factory=dict)

    def __init__(
        self,
        name: str,
        ports: list[TilePort],
        bels: list[Bel],
        tileDir: Path,
        switch_matrix: SwitchMatrix,
        config_mem: ConfigMem,
        gen_ios: list[Gen_IO],
        pinOrderConfig: dict[Side, "PinOrderConfig"] | None = None,
        jump_wires: list[JumpWire] | None = None,
        sjump_ports: list[SJumpPort] | None = None,
    ) -> None:
        self.name = name
        self._super_tile: SuperTile | None = None
        self.portsInfo = ports
        self.jump_wires = jump_wires or []
        self.sjump_ports = sjump_ports or []
        for port in ports:
            port.tile = self
        self.bels = []
        self.add_bels(bels)
        if len(bels) > 26:
            raise ValueError(
                f"Due to naming limitations, tile {name} cannot have more than 26 BELs."
            )
        self.gen_ios = gen_ios
        self.switch_matrix = switch_matrix
        self.tileDir = tileDir
        self.config_mem = config_mem

        if pinOrderConfig is None:
            from fabulous.fabric_generator.gds_generator.gen_io_pin_config_yaml import (
                PinOrderConfig,
            )

            self.pinOrderConfig = {
                Side.NORTH: PinOrderConfig(sort_mode=PinSortMode.BUS_MAJOR),
                Side.EAST: PinOrderConfig(sort_mode=PinSortMode.BUS_MAJOR),
                Side.SOUTH: PinOrderConfig(sort_mode=PinSortMode.BUS_MAJOR),
                Side.WEST: PinOrderConfig(sort_mode=PinSortMode.BUS_MAJOR),
            }
        else:
            self.pinOrderConfig = pinOrderConfig

    def add_bels(self, bels: list[Bel]) -> None:
        """Add BELs to the tile, which becomes their owner.

        Parameters
        ----------
        bels : list[Bel]
            The BELs, appended in order.

        Raises
        ------
        RuntimeError
            If the tile's connections are already derived from its BELs.
        """
        # The hard wires are derived from the BELs once; a BEL added later
        # would silently be missing from them.
        derived = {"port_connections", "fixed_connections", "bel_external_ports"}
        if derived & self.__dict__.keys():
            raise RuntimeError(
                f"Cannot add BELs to tile {self.name}: its connections are "
                "already derived from its BELs."
            )
        for bel in bels:
            bel.owner = self
        self.bels += bels

    def __eq__(self, __o: object, /) -> bool:
        """Check equality between tiles based on their name.

        Parameters
        ----------
        __o : object
            The object to compare with.

        Returns
        -------
        bool
            True if both tiles have the same name, False otherwise.
        """
        if __o is None or not isinstance(__o, Tile):
            return False
        return self.name == __o.name

    @property
    def super_tile(self) -> "SuperTile | None":
        """The supertile this tile type is a subtile of, or None.

        A subtile type only exists as part of its supertile, and belongs to one
        supertile type only (it may fill several of its positions).
        """
        return self._super_tile

    @super_tile.setter
    def super_tile(self, super_tile: "SuperTile") -> None:
        if self._super_tile is not None and self._super_tile is not super_tile:
            raise ValueError(
                f"Tile '{self.name}' is already a subtile of supertile "
                f"'{self._super_tile.name}' and cannot also be one of "
                f"'{super_tile.name}'. Copy and rename the tile to use it in "
                "another supertile."
            )
        self._super_tile = super_tile

    @property
    def withUserCLK(self) -> bool:
        """Whether any BEL of the tile uses the user clock."""
        return any(bel.withUserCLK for bel in self.bels)

    @cached_property
    def bel_external_ports(self) -> tuple[BelExternalPort, ...]:
        """The tile-module ports exporting the BELs' `EXTERNAL` ports.

        Returns
        -------
        tuple[BelExternalPort, ...]
            BEL by BEL, inputs then outputs, as the tile RTL declares them.
        """
        return tuple(
            BelExternalPort(port)
            for bel in self.bels
            for io in (IO.INPUT, IO.OUTPUT)
            for port in bel.get_ports(BelPortKind.EXTERNAL, io)
        )

    @cached_property
    def port_connections(self) -> list[PortConnection[Port]]:
        """The port-to-port wiring inside the tile.

        A channel declaration naming both ends stages its pins through the
        tile as the RTL does: `N4BEG[i]` is driven by `N4END[i + wire_count]`
        through a buffer at the tile entry and one at the exit. Every switch
        matrix port with an `origin` is joined to it at the module boundary:
        to the tile port's matrix-facing slice (the matrix reads the first
        `wire_count` pins of `N4END` and drives the last ones of `N4BEG`), to
        the BEL port or to the SJUMP port. A jump wire loops a matrix output
        back into a matrix input, and a BEL's `EXTERNAL` port is wired to the
        tile-module port exporting it.

        Returns
        -------
        list[PortConnection[Port]]
            Staging, matrix boundary, jump wires, BEL external ports.
        """
        ports = {(p.declaration, p.io_direction): p for p in self.portsInfo}
        connections: list[PortConnection[Port]] = []
        for (declaration, io), out in ports.items():
            if io != IO.OUTPUT or declaration.end is None or declaration.distance < 2:
                continue
            count = declaration.wire_count
            connections.append(
                StagingConnection(
                    ports[(declaration, IO.INPUT)],
                    out,
                    declaration,
                    out.width - count,
                    source_offset=count,
                    buffers=(FabricCellFunction.BUFFER, FabricCellFunction.BUFFER),
                )
            )
        for sm_port in self.switch_matrix.ports:
            origin = sm_port.origin
            if origin is None:
                continue
            offset = 0
            if isinstance(origin, TilePort):
                outer = origin.top_pins if origin.is_output else origin.sm_pins
                offset = outer[0].index
            if sm_port.is_output:
                connection = SwitchMatrixConnection(
                    sm_port, origin, sm_port, sm_port.width, sink_offset=offset
                )
            else:
                connection = SwitchMatrixConnection(
                    origin, sm_port, sm_port, sm_port.width, source_offset=offset
                )
            connections.append(connection)
        for jump in self.jump_wires:
            if jump.source is not None and jump.destination is not None:
                connections.append(
                    JumpConnection(jump.source, jump.destination, jump, jump.wire_count)
                )
        for external in self.bel_external_ports:
            ends = (external.bel_port, external)
            source, sink = ends[::-1] if external.is_input else ends
            connections.append(
                BelExternalConnection(source, sink, external, external.width)
            )
        return connections

    @cached_property
    def fixed_connections(self) -> list[FixedConnection[Pin]]:
        """The hard wires inside the tile.

        The pin connections of the `port_connections`, then the matrix outputs
        with a single input, hard wires inside the matrix.

        Returns
        -------
        list[FixedConnection[Pin]]
            The connections.
        """
        connections = [c for pc in self.port_connections for c in pc.pin_connections]
        return connections + list(self.switch_matrix.fixed_connections)

    def getWestSidePorts(self) -> list[TilePort]:
        """Get all ports physically located on the west side of the tile.

        Returns
        -------
        list[TilePort]
            List of ports on the west side.
        """
        return [p for p in self.portsInfo if p.side_of_tile == Side.WEST]

    def getEastSidePorts(self) -> list[TilePort]:
        """Get all ports physically located on the east side of the tile.

        Returns
        -------
        list[TilePort]
            List of ports on the east side.
        """
        return [p for p in self.portsInfo if p.side_of_tile == Side.EAST]

    def getNorthSidePorts(self) -> list[TilePort]:
        """Get all ports physically located on the north side of the tile.

        Returns
        -------
        list[TilePort]
            List of ports on the north side.
        """
        return [p for p in self.portsInfo if p.side_of_tile == Side.NORTH]

    def getSouthSidePorts(self) -> list[TilePort]:
        """Get all ports physically located on the south side of the tile.

        Returns
        -------
        list[TilePort]
            List of ports on the south side.
        """
        return [p for p in self.portsInfo if p.side_of_tile == Side.SOUTH]

    def getNorthPorts(self, io: IO) -> list[TilePort]:
        """Get all ports with north wire direction filtered by I/O type.

        Parameters
        ----------
        io : IO
            The I/O direction to filter by (INPUT or OUTPUT).

        Returns
        -------
        list[TilePort]
            List of north-direction ports with specified I/O type.
        """
        return [
            p
            for p in self.portsInfo
            if p.wire_direction == Direction.NORTH and p.io_direction == io
        ]

    def getSouthPorts(self, io: IO) -> list[TilePort]:
        """Get all ports with south wire direction filtered by I/O type.

        Parameters
        ----------
        io : IO
            The I/O direction to filter by (INPUT or OUTPUT).

        Returns
        -------
        list[TilePort]
            List of south-direction ports with specified I/O type.
        """
        return [
            p
            for p in self.portsInfo
            if p.wire_direction == Direction.SOUTH and p.io_direction == io
        ]

    def getEastPorts(self, io: IO) -> list[TilePort]:
        """Get all ports with east wire direction filtered by I/O type.

        Parameters
        ----------
        io : IO
            The I/O direction to filter by (INPUT or OUTPUT).

        Returns
        -------
        list[TilePort]
            List of east-direction ports with specified I/O type.
        """
        return [
            p
            for p in self.portsInfo
            if p.wire_direction == Direction.EAST and p.io_direction == io
        ]

    def getWestPorts(self, io: IO) -> list[TilePort]:
        """Get all ports with west wire direction filtered by I/O type.

        Parameters
        ----------
        io : IO
            The I/O direction to filter by (INPUT or OUTPUT).

        Returns
        -------
        list[TilePort]
            List of west-direction ports with specified I/O type.
        """
        return [
            p
            for p in self.portsInfo
            if p.wire_direction == Direction.WEST and p.io_direction == io
        ]

    def getTileOutputNames(self) -> list[str]:
        """Get all output port source names for the tile.

        Returns
        -------
        list[str]
            List of source names for output ports.
        """
        return [p.source_name for p in self.portsInfo if p.is_output]

    @property
    def directory(self) -> Path:
        """The directory the tile's own files are written to and read from.

        Returns
        -------
        Path
            What `tile_directory` returns for this tile.
        """
        return tile_directory(self.name, self.tileDir, self.switch_matrix.matrix_file)

    @property
    def config_bit_ranges(self) -> dict[Bel | SwitchMatrix, range]:
        """Where each configurable element's bits sit in the tile's `ConfigBits`.

        The BELs in order, then the switch matrix. The tile RTL slices
        `ConfigBits` and the bitstream spec places the features by these ranges.

        Returns
        -------
        dict[Bel | SwitchMatrix, range]
            The range of each BEL and of the switch matrix.
        """
        ranges: dict[Bel | SwitchMatrix, range] = {}
        start = 0
        for bel in self.bels:
            ranges[bel] = range(start, start + bel.configBit)
            start += bel.configBit
        sm_bits = self.switch_matrix.no_config_bits
        ranges[self.switch_matrix] = range(start, start + sm_bits)
        return ranges

    @property
    def globalConfigBits(self) -> int:
        """Get the total number of global configuration bits.

        Calculates the sum of switch matrix configuration bits
        and all BEL configuration bits.

        Returns
        -------
        int
            Total number of global configuration bits for the tile.
        """
        ret = self.switch_matrix.no_config_bits

        for b in self.bels:
            ret += b.configBit

        return ret

    def get_port_count(self, side: Side) -> int:
        """Count total number of expanded physical pins on a given side of the tile.

        Parameters
        ----------
        side : Side
            The side of the tile to count ports for.

        Returns
        -------
        int
            Total number of expanded ports on the given side.
        """
        return sum(p.width for p in self.portsInfo if p.side_of_tile == side)

    def get_min_die_area(
        self,
        x_pitch: Decimal,
        y_pitch: Decimal,
        x_pin_thickness_mult: Decimal = Decimal(1),
        y_pin_thickness_mult: Decimal = Decimal(1),
        frame_data_width: int = 32,
        frame_strobe_width: int = 20,
        edge_offset: int = 2,
    ) -> tuple[Decimal, Decimal]:
        """Calculate minimum tile dimensions based on IO pin track requirements.

        The IO pin placer distributes pins across available tracks on each
        tile edge. Each pin occupies `thickness_mult` consecutive tracks,
        and `edge_offset` tracks are reserved at the start of the tile
        (see `tile_io_place.allocate_tracks`).

        The minimum number of tracks on a side is therefore::

            required_tracks = pin_count * thickness_mult + edge_offset

        And the minimum physical dimension is::

            min_dim = required_tracks * pitch

        Parameters
        ----------
        x_pitch : Decimal
            Vertical-layer track pitch (for north/south pins).
        y_pitch : Decimal
            Horizontal-layer track pitch (for east/west pins).
        x_pin_thickness_mult : Decimal
            Number of tracks each north/south pin spans, by default 1.
        y_pin_thickness_mult : Decimal
            Number of tracks each east/west pin spans, by default 1.
        frame_data_width : int, optional
            Frame data width, by default 32.
        frame_strobe_width : int, optional
            Frame strobe width, by default 20.
        edge_offset : int, optional
            Reserved tracks at tile edge, by default 2.

        Returns
        -------
        tuple[Decimal, Decimal]
            (min_width, min_height)
        """
        north_ports = self.get_port_count(Side.NORTH)
        south_ports = self.get_port_count(Side.SOUTH)
        west_ports = self.get_port_count(Side.WEST)
        east_ports = self.get_port_count(Side.EAST)

        x_io_count = Decimal(max(north_ports, south_ports) + frame_strobe_width)
        min_width_io = (x_io_count * x_pin_thickness_mult + edge_offset) * x_pitch

        y_io_count = Decimal(max(west_ports, east_ports) + frame_data_width)
        min_height_io = (y_io_count * y_pin_thickness_mult + edge_offset) * y_pitch

        return min_width_io, min_height_io
