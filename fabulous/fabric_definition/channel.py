"""Routing channel declarations and the fabric-wide channels they resolve to.

A wire line of a tile CSV (`NORTH,N4BEG,0,-4,N4END,4`) declares how the tile
takes part in a routing channel: which ports it has, how far the wire goes and
how many wires run in parallel. The line is a `ChannelDeclaration`, shared by
the ports it declares. A line may leave one end `NULL`: the tile then only
starts or only ends the channel, and the missing name is found in the other
tile types' declarations. The resolved channel, with both ends named, is a
`RoutingChannel`; it is unique in the fabric.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fabulous.fabric_definition.define import Direction

if TYPE_CHECKING:
    from collections.abc import Iterable

ROUTING_DIRECTIONS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)
"""The directions of lines that declare a routing channel between tiles."""


@dataclass(frozen=True)
class ChannelDeclaration:
    """One wire line of a tile CSV.

    Attributes
    ----------
    direction : Direction
        The direction the wire runs in.
    x_offset : int
        Columns from the driving tile to the receiving tile.
    y_offset : int
        Rows from the driving tile to the receiving tile.
    wire_count : int
        The number of wires running in parallel.
    begin : str | None
        The name of the driving end, or None where the line says `NULL` (the
        tile only ends the channel).
    end : str | None
        The name of the receiving end, or None where the line says `NULL` (the
        tile only starts the channel).
    """

    direction: Direction
    x_offset: int
    y_offset: int
    wire_count: int
    begin: str | None
    end: str | None

    @property
    def distance(self) -> int:
        """The number of tiles the wire spans."""
        return abs(self.x_offset) + abs(self.y_offset)

    @property
    def is_null_terminated(self) -> bool:
        """Whether one end of the line is `NULL`."""
        return self.begin is None or self.end is None


@dataclass(frozen=True)
class RoutingChannel:
    """A routing channel of the fabric, with both ends named.

    Attributes
    ----------
    direction : Direction
        The direction the wire runs in.
    x_offset : int
        Columns from the driving tile to the receiving tile.
    y_offset : int
        Rows from the driving tile to the receiving tile.
    wire_count : int
        The number of wires running in parallel.
    begin : str
        The name of the driving end.
    end : str
        The name of the receiving end.
    """

    direction: Direction
    x_offset: int
    y_offset: int
    wire_count: int
    begin: str
    end: str

    @property
    def step(self) -> tuple[int, int]:
        """The `(dx, dy)` from a tile to the next one along the channel."""
        return (
            (self.x_offset > 0) - (self.x_offset < 0),
            (self.y_offset > 0) - (self.y_offset < 0),
        )


def resolve_channels(
    declarations: Iterable[ChannelDeclaration],
) -> dict[ChannelDeclaration, RoutingChannel]:
    """Resolve each declaration to the routing channel it takes part in.

    A line naming both ends is its channel. A line with a `NULL` end resolves
    against the lines that name both ends, by direction, offsets, width and its
    known name. Without such a line, a start and an end with the same name form
    a channel of that name at both ends.

    Parameters
    ----------
    declarations : Iterable[ChannelDeclaration]
        The routing declarations of the tile types.

    Returns
    -------
    dict[ChannelDeclaration, RoutingChannel]
        The channel of each declaration.

    Raises
    ------
    ValueError
        If two lines name the same end of a channel with different far ends.
    """
    declarations = set(declarations)
    ends: dict[tuple, str] = {}
    begins: dict[tuple, str] = {}
    for d in declarations:
        if d.begin is None or d.end is None:
            continue
        shape = (d.direction, d.x_offset, d.y_offset, d.wire_count)
        for table, known, other in ((ends, d.begin, d.end), (begins, d.end, d.begin)):
            if table.setdefault((*shape, known), other) != other:
                raise ValueError(
                    f"Routing channel {d.direction.value} {known} "
                    f"({d.x_offset},{d.y_offset}) x{d.wire_count} is declared with "
                    f"two different far ends: {table[(*shape, known)]} and {other}."
                )
    channels = {}
    for d in declarations:
        shape = (d.direction, d.x_offset, d.y_offset, d.wire_count)
        begin = d.begin if d.begin is not None else begins.get((*shape, d.end), d.end)
        end = d.end if d.end is not None else ends.get((*shape, d.begin), d.begin)
        channels[d] = RoutingChannel(*shape, begin, end)
    return channels
