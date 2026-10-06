"""Connections between pins, the edges of the fabric's routing graph.

A connection joins a source pin to a sink pin, one bit, directed. Inside a tile
or switch matrix type the ends are plain `Pin`s; between placed tiles they are
`(TileInstance, Pin)`. Connections are not stored: the declarations they come
from (a routing channel line, a switch matrix port, a jump wire) are, and
expand into them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fabulous.fabric_definition.channel import ChannelDeclaration
    from fabulous.fabric_definition.instance import TileInstance
    from fabulous.fabric_definition.port import Pin, SwitchMatrixPort
    from fabulous.fabric_definition.switch_matrix import Mux, SwitchMatrix
    from fabulous.fabric_definition.wire import JumpWire

type InstancePin = tuple[TileInstance, Pin]
"""A pin of a placed tile."""

type LocalPin = tuple[tuple[int, int], Pin]
"""A pin at an `(x, y)` position of a supertile's `tileMap`."""


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
    declaration : ChannelDeclaration | SwitchMatrixPort | SwitchMatrix | JumpWire
        The declaration this connection is expanded from; for a switch
        matrix boundary, the matrix port; inside a matrix (an output with a
        single input), the matrix.
    """

    declaration: ChannelDeclaration | SwitchMatrixPort | SwitchMatrix | JumpWire


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
