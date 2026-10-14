"""Connections between pins, the edges of the fabric's routing graph.

A connection joins a source pin to a sink pin, one bit, directed. Inside a tile
or switch matrix type the ends are plain `Pin`s; between placed tiles they are
`(TileInstance, Pin)`. Connections are not stored: the declarations they come
from (a routing channel line, a switch matrix port, a jump wire) are, and
expand into them. A `PortConnection` is the port-level view: one declaration
wiring a run of bits of one port to another, which expands into its pin
connections.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fabulous.fabric_definition.channel import ChannelDeclaration
from fabulous.fabric_definition.define import grid_at
from fabulous.fabric_definition.port import BelExternalPort, BelPort, SwitchMatrixPort
from fabulous.fabric_definition.wire import JumpWire

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fabulous.fabric_definition.channel import RoutingChannel
    from fabulous.fabric_definition.fabric_cell_library import FabricCellFunction
    from fabulous.fabric_definition.instance import TileInstance
    from fabulous.fabric_definition.port import Pin, TilePort
    from fabulous.fabric_definition.switch_matrix import Mux, SwitchMatrix
    from fabulous.fabric_definition.tile import Tile

type InstancePin = tuple[TileInstance, Pin]
"""A pin of a placed tile."""

type LocalPin = tuple[tuple[int, int], Pin]
"""A pin at an `(x, y)` position of a supertile's `tileMap`."""

type Declaration = (
    ChannelDeclaration | SwitchMatrixPort | SwitchMatrix | JumpWire | BelExternalPort
)
"""What a fixed connection is expanded from."""


@dataclass(frozen=True)
class Connection[E]:
    """A directed one-bit connection from `source` to `sink`.

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
        The port connection the wire is a bit of, None for a single wire.
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

        Named after its end outside the switch matrix: a tile or SJUMP port
        bit (`N4END[3]`) or a BEL pin (`LA_I`); a jump wire after its source
        bit (`JN2BEG[0]`); an exported BEL pin after its scalar tile-module
        port (`A_O_top`). A hop between placed tiles is a fabric signal, named
        after the port driving it (`Tile_X1Y3_N4BEG[0]`).

        Returns
        -------
        str
            The signal name, indexed for a bit of a vector.

        Raises
        ------
        TypeError
            If the connection is placed but no hop between two tiles.
        ValueError
            If the connection is not one tile signal: through buffer cells or
            inside the switch matrix module.
        """
        if isinstance(self.source, tuple):
            at, pin = self.source
            if not isinstance(self.port_connection, TilePortConnection):
                raise TypeError(f"{self.feature} is no hop between placed tiles")
            return f"{at.port_signal(pin.port)}[{pin.index}]"
        if self.buffers:
            raise ValueError(f"{self.feature} passes buffer cells: no single signal")
        if isinstance(self.declaration, JumpWire):
            return self.source.name(indexed=True)
        if isinstance(self.declaration, BelExternalPort):
            return self.source.name()
        if isinstance(self.declaration, SwitchMatrixPort):
            outer = self.sink if self.source.port is self.declaration else self.source
            if isinstance(outer.port, BelPort):
                return outer.name()
            return outer.name(indexed=True)
        raise ValueError(f"{self.feature} is not a signal of the tile module")


@dataclass(frozen=True)
class PortConnection[E]:
    """Wires `width` bits of a source port to a sink port, bit by bit.

    Source bit `source_offset + i` drives sink bit `sink_offset + i`. It is
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
        How many bits are wired.
    source_offset : int
        The first source bit. Default 0.
    sink_offset : int
        The first sink bit. Default 0.
    buffers : tuple[FabricCellFunction, ...]
        The buffer cells each bit passes from source to sink. Default none.
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
        """The pin-to-pin connections, in source bit order."""

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


@dataclass(frozen=True)
class StagingConnection[E](PortConnection[E]):
    """A channel line staged through a tile: `END[count:]` -> `BEG[:-count]`."""


@dataclass(frozen=True)
class TilePortConnection[E](PortConnection[E]):
    """A channel line from a tile's begin port to the partner end port one tile on."""


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
