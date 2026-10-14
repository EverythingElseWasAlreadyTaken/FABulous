"""Connections between pins, the edges of the fabric's routing graph.

A connection joins a source pin to a sink pin, directed. Inside a tile
or switch matrix type the ends are plain `Pin`s; between placed tiles they are
`(TileInstance, Pin)`. Connections are not stored: the declarations they come
from (a routing channel line, a switch matrix port, a jump wire) are, and
expand into them. A `PortConnection` is the port-level view: one declaration
wiring a run of pins of one port to another, which expands into its pin
connections.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fabulous.fabric_definition.channel import ChannelDeclaration
from fabulous.fabric_definition.define import grid_at
from fabulous.fabric_definition.port import BelExternalPort, BelPort, SwitchMatrixPort
from fabulous.fabric_definition.wire import JumpWire, SJumpWire

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fabulous.fabric_definition.channel import RoutingChannel
    from fabulous.fabric_definition.fabric_cell_library import FabricCellFunction
    from fabulous.fabric_definition.instance import TileInstance
    from fabulous.fabric_definition.port import Pin, Port, TilePort
    from fabulous.fabric_definition.switch_matrix import Mux, SwitchMatrix
    from fabulous.fabric_definition.tile import Tile

type InstancePin = tuple[TileInstance, Pin]
"""A pin of a placed tile."""

type LocalPin = tuple[tuple[int, int], Pin]
"""A pin at an `(x, y)` position of a supertile's `tileMap`."""

type Declaration = (
    ChannelDeclaration
    | SwitchMatrixPort
    | SwitchMatrix
    | JumpWire
    | SJumpWire
    | BelExternalPort
)
"""What a fixed connection is expanded from."""


@dataclass(frozen=True)
class Connection[E]:
    """A directed pin-to-pin connection from `source` to `sink`.

    Attributes
    ----------
    source : E
        The driving end, a `Pin` or an `InstancePin`.
    sink : E
        The driven end, of the same kind as `source`.
    """

    source: E
    sink: E

    @property
    def feature(self) -> str:
        """The name of the connection: source and sink full names, `.`-joined.

        This is the FASM feature of the pip nextpnr sees for it.
        """
        ends = (self.source, self.sink)
        pins = [end[1] if isinstance(end, tuple) else end for end in ends]
        return ".".join(pin.full_name() for pin in pins)


@dataclass(frozen=True)
class FixedConnection[E](Connection[E]):
    """A hard wire: both ends are the same signal, nothing to configure.

    Attributes
    ----------
    declaration : Declaration
        The declaration this connection is expanded from; for a switch
        matrix boundary, the matrix port; inside a matrix (an output with a
        single input), the matrix; for an exported BEL port, the tile port
        exporting it.
    port_connection : PortConnection | None
        The port connection the wire belongs to, None for a single wire.
    """

    declaration: Declaration
    port_connection: PortConnection | None = None

    @property
    def buffers(self) -> tuple[FabricCellFunction, ...]:
        """The buffer cells the signal passes, those of its port connection."""
        return () if self.port_connection is None else self.port_connection.buffers

    @property
    def signal(self) -> str:
        """The tile RTL signal this hard wire is.

        Its port connection names it (`PortConnection.pin_signal`): at the
        switch matrix boundary the end outside the matrix, a tile or SJUMP
        port pin (`N4END[3]`) or a BEL pin (`LA_I`); a jump wire its source
        pin (`JN2BEG[0]`); an exported BEL pin its scalar tile-module port
        (`A_O_top`); a line between tiles the port pin driving it
        (`Tile_X1Y3_N4BEG[0]`).

        Returns
        -------
        str
            The signal name, indexed for a pin of a vector.

        Raises
        ------
        ValueError
            If the connection is inside the switch matrix module. Its port
            connection raises when it names no signal: `ValueError` through
            buffer cells, `TypeError` when placed but no line between tiles.
        """
        pc = self.port_connection
        if pc is None:
            raise ValueError(f"{self.feature} is not a signal of the tile module")
        return pc.pin_signal(self)


@dataclass(frozen=True)
class PortConnection[E]:
    """Wires `width` pins of a source port to a sink port, pin by pin.

    Source pin `source_offset + i` drives sink pin `sink_offset + i`. It is
    one declaration's whole wiring between two ports, which its
    `pin_connections` expand into.

    Attributes
    ----------
    source : E
        The driving port: a `Port`, or placed `(TileInstance, Port)` or
        `((x, y), Port)`.
    sink : E
        The driven port, of the same kind as `source`.
    declaration : Declaration
        The declaration the connection is expanded from.
    width : int
        How many pins are wired.
    source_offset : int
        The first source pin. Default 0.
    sink_offset : int
        The first sink pin. Default 0.
    buffers : tuple[FabricCellFunction, ...]
        The buffer cells each pin connection passes. Default none.
    """

    source: E
    sink: E
    declaration: Declaration
    width: int
    source_offset: int = 0
    sink_offset: int = 0
    buffers: tuple[FabricCellFunction, ...] = ()

    @property
    def pin_connections(self) -> tuple[FixedConnection, ...]:
        """The pin-to-pin connections, in source pin order."""

        def pin(end: E, index: int) -> object:
            if isinstance(end, tuple):
                at, port = end
                return (at, port[index])
            return end[index]

        return tuple(
            FixedConnection(
                pin(self.source, self.source_offset + i),
                pin(self.sink, self.sink_offset + i),
                self.declaration,
                self,
            )
            for i in range(self.width)
        )

    def pin_signal(self, c: FixedConnection) -> str:
        """Name the RTL signal one of the `pin_connections` is.

        Parameters
        ----------
        c : FixedConnection
            One of the connection's `pin_connections`.

        Returns
        -------
        str
            The signal name; each kind of port connection has its rule.

        Raises
        ------
        ValueError
            If the kind names no single signal.
        """
        raise ValueError(f"{c.feature} is no single signal")


def _module_pins(c: FixedConnection) -> tuple[Pin, Pin]:
    """Return the pins of a connection inside a tile or supertile module.

    Its ends are plain pins (a tile module) or at `tileMap` positions (a
    supertile wrapper).

    Parameters
    ----------
    c : FixedConnection
        The connection.

    Returns
    -------
    tuple[Pin, Pin]
        Its source and sink pin.

    Raises
    ------
    TypeError
        If the connection is placed in the fabric: inside a tile instance it
        is no signal of the fabric.
    """
    source, sink = c.source, c.sink
    if isinstance(source, tuple):
        (at, source), (_, sink) = source, sink
        if not isinstance(at, tuple):
            raise TypeError(f"{c.feature} is no line between placed tiles")
    return source, sink


@dataclass(frozen=True)
class StagingConnection[E](PortConnection[E]):
    """A channel line staged through a tile: `END[count:]` -> `BEG[:-count]`."""

    def pin_signal(self, c: FixedConnection) -> str:
        """Refuse: a staged pin passes buffer cells, so it is no single signal.

        Parameters
        ----------
        c : FixedConnection
            One of the connection's `pin_connections`.

        Returns
        -------
        str
            Never.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError(f"{c.feature} passes buffer cells: no single signal")


@dataclass(frozen=True)
class TilePortConnection[E](PortConnection[E]):
    """A channel line from a tile's begin port to the partner end port one tile on."""

    def pin_signal(self, c: FixedConnection) -> str:
        """Name the signal after the port pin driving it, `Tile_X1Y3_N4BEG[0]`.

        Parameters
        ----------
        c : FixedConnection
            One of the connection's `pin_connections`.

        Returns
        -------
        str
            The signal: the source tile, its port and the pin index.
        """
        at, pin = c.source
        tile = f"Tile_X{at[0]}Y{at[1]}" if isinstance(at, tuple) else at.name
        return f"{tile}_{pin.port.name}[{pin.index}]"


@dataclass(frozen=True)
class SwitchMatrixConnection[E](PortConnection[E]):
    """A switch matrix port wired to its origin.

    The origin is a tile port's matrix-facing slice (by the offsets), a BEL
    port or an SJUMP port. The declaration is the matrix port, or in a
    supertile for an SJUMP port the `SJumpWire` creating it.
    """

    @property
    def matrix_port(self) -> E:
        """The end at the switch matrix."""
        if isinstance(self._port(self.source), SwitchMatrixPort):
            return self.source
        return self.sink

    @property
    def outer(self) -> E:
        """The end outside the switch matrix."""
        return self.sink if self.matrix_port is self.source else self.source

    @staticmethod
    def _port(end: E) -> Port:
        return end[1] if isinstance(end, tuple) else end

    def pin_signal(self, c: FixedConnection) -> str:
        """Name the signal after its end outside the matrix.

        In a tile module, a tile or SJUMP port pin (`N4END[3]`) or a BEL pin
        (`LA_I`). In a supertile wrapper (ends at `tileMap` positions), a BEL
        pin or the SJUMP wire's vector (`DSP_top_top2bot[0]`).

        Parameters
        ----------
        c : FixedConnection
            One of the connection's `pin_connections`.

        Returns
        -------
        str
            The signal name; placed in the fabric, `_module_pins` raises a
            `TypeError`.
        """
        source, sink = _module_pins(c)
        outer = sink if isinstance(source.port, SwitchMatrixPort) else source
        if isinstance(outer.port, BelPort):
            return outer.name()
        if isinstance(self.declaration, SJumpWire):
            return f"{self.declaration.signal_name}[{outer.index}]"
        return outer.name(indexed=True)


@dataclass(frozen=True)
class JumpConnection[E](PortConnection[E]):
    """A jump wire: a matrix output looped back into a matrix input."""

    def pin_signal(self, c: FixedConnection) -> str:
        """Name the signal after its source pin, `JN2BEG[0]`.

        Parameters
        ----------
        c : FixedConnection
            One of the connection's `pin_connections`.

        Returns
        -------
        str
            The source pin, indexed.
        """
        source, _ = _module_pins(c)
        return source.name(indexed=True)


@dataclass(frozen=True)
class BelExternalConnection[E](PortConnection[E]):
    """A BEL's `EXTERNAL` port wired to the tile-module port exporting it."""

    def pin_signal(self, c: FixedConnection) -> str:
        """Name the signal after its scalar tile-module port, `A_O_top`.

        Parameters
        ----------
        c : FixedConnection
            One of the connection's `pin_connections`.

        Returns
        -------
        str
            The flat pin name.
        """
        source, _ = _module_pins(c)
        return source.name()


def tile_port_connections(
    tiles: list[list[Tile | None]],
    channels: Mapping[ChannelDeclaration, RoutingChannel],
) -> list[TilePortConnection[tuple[tuple[int, int], TilePort]]]:
    """Find where the channel lines hop from tile to tile across a grid.

    A line runs from a tile's begin port to the same channel's end port one
    tile along. A line leading out of the grid, into an empty cell or into a
    tile without its end port dangles and has no hop.

    Parameters
    ----------
    tiles : list[list[Tile | None]]
        The grid of tile types, `None` for an empty cell.
    channels : Mapping[ChannelDeclaration, RoutingChannel]
        The channel of each declaration of the tiles.

    Returns
    -------
    list[TilePortConnection[tuple[tuple[int, int], TilePort]]]
        The hops with their ends at grid positions, row by row.
    """
    hops = []
    for y, row in enumerate(tiles):
        for x, tile in enumerate(row):
            if tile is None:
                continue
            for out in tile.portsInfo:
                if not out.is_output:
                    continue
                channel = channels[out.declaration]
                dx, dy = channel.step
                target = grid_at(tiles, x + dx, y + dy)
                if target is None:
                    continue
                end = next(
                    (
                        p
                        for p in target.portsInfo
                        if p.is_input and channels[p.declaration] is channel
                    ),
                    None,
                )
                if end is None:
                    continue
                declaration = out.declaration
                # A start tap drives every slice it spans; with no span, none.
                if declaration.end is None:
                    width = declaration.wire_count * declaration.distance
                else:
                    width = out.width
                hops.append(
                    TilePortConnection(
                        ((x, y), out), ((x + dx, y + dy), end), declaration, width
                    )
                )
    return hops


@dataclass(frozen=True)
class Pip[E](Connection[E]):
    """A programmable connection: an input of a switch matrix mux.

    Attributes
    ----------
    mux : Mux
        The mux the pip is an input of.
    index : int
        Which of the mux's inputs the pip is.
    """

    mux: Mux
    index: int

    @property
    def bits(self) -> tuple[tuple[int, str], ...]:
        """The `(config bit within the matrix, value)` pairs selecting the pip."""
        return self.mux.select(self.index)
