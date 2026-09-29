"""Connections between pins, the edges of the fabric's routing graph.

A connection joins a source pin to a sink pin, one bit, directed. Inside a tile
or switch matrix type the ends are plain `Pin`s; between placed tiles they are
`(TileInstance, Pin)`. Connections are not stored: the declarations they come
from (a routing channel line, a jump wire) are, and expand into them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fabulous.fabric_definition.channel import ChannelDeclaration
    from fabulous.fabric_definition.instance import TileInstance
    from fabulous.fabric_definition.port import Pin
    from fabulous.fabric_definition.wire import JumpWire

type InstancePin = tuple[TileInstance, Pin]
"""A pin of a placed tile."""


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


@dataclass(frozen=True)
class FixedConnection[E](Connection[E]):
    """A hard wire: both ends are the same signal, nothing to configure.

    Attributes
    ----------
    declaration : ChannelDeclaration | JumpWire
        The declaration this connection is expanded from.
    """

    declaration: ChannelDeclaration | JumpWire
