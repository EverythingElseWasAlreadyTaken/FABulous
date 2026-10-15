"""Which fabric cell sits where: the cell decisions on top of the model.

The model says what is connected. A rule, a plain Python callable reading a
tile's model, decides which fabric cell function sits at which of its
elements; it runs once into a plain mapping, the data every consumer reads.
The `FabricCellLibrary` then says which concrete cell implements a function.

So far only buffers are decided, at tile port pins: a buffer splits its pin
into an outer side, the module port, and an inner side, `inner_signal`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING

from fabulous.fabric_definition.connection import StagingConnection
from fabulous.fabric_definition.fabric_cell_library import FabricCellFunction

if TYPE_CHECKING:
    from fabulous.fabric_definition.connection import FixedConnection
    from fabulous.fabric_definition.port import Pin, Port
    from fabulous.fabric_definition.tile import Tile

type BufferRule = Callable[[Tile], dict[Pin, FabricCellFunction]]
"""Decide the buffers of a tile: the buffer at each buffered pin."""


def staged_pin_buffers(tile: Tile) -> dict[Pin, FabricCellFunction]:
    """Put a plain buffer at both ends of every staging connection.

    The default buffer rule: a pin staged through the tile has no driver or
    load inside it, so it is buffered at the tile boundary.

    Parameters
    ----------
    tile : Tile
        The tile type.

    Returns
    -------
    dict[Pin, FabricCellFunction]
        `BUFFER` at each staged pin.
    """
    return {
        pin: FabricCellFunction.BUFFER
        for pc in tile.port_connections
        if isinstance(pc, StagingConnection)
        for c in pc.pin_connections
        for pin in (c.source, c.sink)
    }


def resolve_buffers(
    tiles: Iterable[Tile], rule: BufferRule | None = None
) -> dict[Pin, FabricCellFunction]:
    """Run the buffer rule over the tiles.

    Parameters
    ----------
    tiles : Iterable[Tile]
        The tile types.
    rule : BufferRule | None
        Decides the buffers of each tile; None for `staged_pin_buffers`.

    Returns
    -------
    dict[Pin, FabricCellFunction]
        The buffer at each buffered pin.
    """
    rule = rule or staged_pin_buffers
    buffers = {}
    for tile in tiles:
        buffers |= rule(tile)
    return buffers


def inner_vector(port: Port) -> str:
    """Return the vector of a port's inner side, `N4END_i`.

    Parameters
    ----------
    port : Port
        A port with buffered pins.

    Returns
    -------
    str
        The vector name.
    """
    return f"{port.name}_i"


def inner_signal(buffers: Mapping[Pin, FabricCellFunction], pin: Pin) -> str:
    """Return the signal inside the tile module at `pin`.

    The inner side of a buffered pin (`N4END_i[4]`), else the pin itself
    (`N4END[0]`).

    Parameters
    ----------
    buffers : Mapping[Pin, FabricCellFunction]
        The buffered pins.
    pin : Pin
        A tile port pin.

    Returns
    -------
    str
        The signal, indexed.
    """
    if pin in buffers:
        return f"{inner_vector(pin.port)}[{pin.index}]"
    return pin.name(indexed=True)


def buffer_name(pin: Pin) -> str:
    """Return the instance name of the buffer at `pin`.

    `N4END_inbuf_4` at an input pin, `N4BEG_outbuf_0` at an output pin.

    Parameters
    ----------
    pin : Pin
        A buffered pin.

    Returns
    -------
    str
        The instance name.
    """
    kind = "outbuf" if pin.port.is_output else "inbuf"
    return f"{pin.port.name}_{kind}_{pin.index}"


def buffered_signal(
    buffers: Mapping[Pin, FabricCellFunction], c: FixedConnection
) -> str:
    """Return the tile RTL signal a hard wire is, with the buffers.

    A wire ending at a buffered pin of the module is that pin's inner side,
    the source end first; otherwise the model's `c.signal`.

    Parameters
    ----------
    buffers : Mapping[Pin, FabricCellFunction]
        The buffered pins.
    c : FixedConnection
        A hard wire of a tile.

    Returns
    -------
    str
        The signal name.
    """
    for end in (c.source, c.sink):
        if not isinstance(end, tuple) and end in buffers:
            return inner_signal(buffers, end)
    return c.signal
