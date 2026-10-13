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

from fabulous.fabric_definition.channel import ChannelDeclaration
from fabulous.fabric_definition.port import BelExternalPort, BelPort, SwitchMatrixPort
from fabulous.fabric_definition.wire import JumpWire

if TYPE_CHECKING:
    from fabulous.fabric_definition.fabric_cell_library import FabricCellFunction
    from fabulous.fabric_definition.instance import TileInstance
    from fabulous.fabric_definition.port import Pin
    from fabulous.fabric_definition.switch_matrix import Mux, SwitchMatrix

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
    buffers : tuple[FabricCellFunction, ...]
        The buffer cells the signal passes from source to sink, empty for a
        plain wire.
    """

    declaration: Declaration
    buffers: tuple[FabricCellFunction, ...] = ()

    @property
    def is_staging(self) -> bool:
        """Whether the connection stages a channel line through one tile.

        Between placed tiles a channel line also declares the hops; those
        join two tiles and are no staging.
        """
        if not isinstance(self.declaration, ChannelDeclaration):
            return False
        if isinstance(self.source, tuple):
            return self.source[0] == self.sink[0]
        return True

    @property
    def signal(self) -> str:
        """The tile RTL signal this hard wire is.

        Named after its end outside the switch matrix: a tile or SJUMP port
        bit (`N4END[3]`) or a BEL pin (`LA_I`); a jump wire after its source
        bit (`JN2BEG[0]`); an exported BEL pin after its scalar tile-module
        port (`A_O_top`).

        Returns
        -------
        str
            The signal name, indexed for a bit of a vector.

        Raises
        ------
        TypeError
            If the connection is between placed tiles.
        ValueError
            If the connection is not one tile signal: through buffer cells or
            inside the switch matrix module.
        """
        if isinstance(self.source, tuple):
            raise TypeError(f"{self.feature} is not inside a tile")
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
