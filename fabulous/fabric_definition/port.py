"""Port class hierarchy for FPGA fabric.

This module contains the port class hierarchy for representing different types of ports
in the FPGA fabric:
- Pin: A single bit of a port; the node of the routing graph
- Port: Base class for all port types
- TilePort: Port on a tile with side and termination information
- SJumpPort: Tile port facing the switch matrix of the surrounding supertile
- BelPort: Port on a BEL (Basic Element of Logic)
- ConfigPort: A port carrying configuration bits into a module
- BelConfigPort: The configuration port of a BEL, with its feature map
- SwitchMatrixPort: A port of a tile's switch-matrix module
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property, total_ordering
from typing import TYPE_CHECKING

from fabulous.fabric_definition.define import IO, BelPortKind, Direction, Side

if TYPE_CHECKING:
    from fabulous.fabric_definition.bel import Bel
    from fabulous.fabric_definition.tile import Tile

NULL_PORT_NAME = "NULL"


@dataclass(frozen=True)
class Pin:
    """A single bit of a `Port`.

    Pins are the nodes in the FABulous routing graph: a wire or a pip always joins
    exactly one pin to another. Two pins are equal when they index the same
    bit of the same port object, so they can be used as dictionary keys.

    Attributes
    ----------
    port : Port
        The port this pin is a bit of.
    index : int
        The bit index within the port, 0 is the least significant bit.
    """

    port: Port
    index: int

    def name(self, indexed: bool = False, prefix: str = "") -> str:
        """Return the HDL wire name of this pin.

        Parameters
        ----------
        indexed : bool, optional
            If True, use bracket notation (`port[3]`, a bit of a vector).
            If False, use flat concatenation (`port3`, a scalar switch-matrix
            port). Defaults to False.
        prefix : str, optional
            A prefix to prepend to the port name, by default "".

        Returns
        -------
        str
            The wire name.
        """
        return self.port.pin_name(self.index, indexed, prefix)


class Port:
    """Base class for all port types.

    Parameters
    ----------
    name : str
        The name of the port.
    io_direction : IO
        The I/O direction (INPUT, OUTPUT, INOUT).
    width : int
        The bit width of the port.
    is_clock : bool
        Whether the port carries a clock. Defaults to False.
    is_global : bool
        Whether the port is driven by a fabric-wide signal, such as the global
        user clock, rather than a locally generated one. Defaults to False.
    net : str
        The net the port belongs to. Defaults to "", the global net.

    Raises
    ------
    ValueError
        If the width is not greater than 0.
    TypeError
        If io_direction is not an instance of IO, if name is not a string, or if
        is_clock or is_global is not a bool.
    """

    _name: str
    _io_direction: IO
    _width: int
    _is_clock: bool
    _is_global: bool
    _net: str

    def __init__(
        self,
        name: str,
        io_direction: IO,
        width: int,
        is_clock: bool = False,
        is_global: bool = False,
        net: str = "",
    ) -> None:
        self._name = name
        self._io_direction = io_direction
        self._width = width
        self._is_clock = is_clock
        self._is_global = is_global
        self._net = net

        if self.width <= 0:
            raise ValueError(f"Width must be greater than 0, got {self.width}")
        if not isinstance(self.io_direction, IO):
            raise TypeError(
                f"io_direction must be an instance of IO, got {type(self.io_direction)}"
            )
        if not isinstance(self._name, str):
            raise TypeError(f"name must be a string, got {type(self._name)}")
        if not isinstance(self._is_clock, bool):
            raise TypeError(f"is_clock must be a bool, got {type(self._is_clock)}")
        if not isinstance(self._is_global, bool):
            raise TypeError(f"is_global must be a bool, got {type(self._is_global)}")

    def __repr__(self) -> str:
        """Return a string representation of the port."""
        return f"Port({self.io_direction.value} {self.name}[{self.width - 1}:0])"

    @property
    def name(self) -> str:
        """Return the port name."""
        return self._name

    @property
    def io_direction(self) -> IO:
        """Return the I/O direction."""
        return self._io_direction

    @property
    def width(self) -> int:
        """Return the bit width."""
        return self._width

    @property
    def is_input(self) -> bool:
        """Whether the port is an input. An INOUT port is not an input."""
        return self._io_direction == IO.INPUT

    @property
    def is_output(self) -> bool:
        """Whether the port is an output. An INOUT port is not an output."""
        return self._io_direction == IO.OUTPUT

    @property
    def is_inout(self) -> bool:
        """Whether the port is bidirectional."""
        return self._io_direction == IO.INOUT

    @property
    def is_clock(self) -> bool:
        """Whether the port carries a clock."""
        return self._is_clock

    @property
    def is_global(self) -> bool:
        """Whether the port is driven by a fabric-wide signal."""
        return self._is_global

    @property
    def net(self) -> str:
        """The net the port belongs to; "" is the global net."""
        return self._net

    def pin_name(self, index: int, indexed: bool = False, prefix: str = "") -> str:
        """Return the HDL wire name of bit `index` of this port.

        Parameters
        ----------
        index : int
            The bit index.
        indexed : bool, optional
            If True, use bracket notation (`port[3]`, a bit of a vector).
            If False, use flat concatenation (`port3`, a scalar switch-matrix
            port). Defaults to False.
        prefix : str, optional
            A prefix to prepend to the port name, by default "".

        Returns
        -------
        str
            The wire name.
        """
        if indexed:
            return f"{prefix}{self.name}[{index}]"
        return f"{prefix}{self.name}{index}"

    @cached_property
    def pins(self) -> tuple[Pin, ...]:
        """One `Pin` per bit, least significant first."""
        return tuple(Pin(self, i) for i in range(self.width))

    def __getitem__(self, index: int) -> Pin:
        """Return the pin for bit `index`."""
        return self.pins[index]

    def expand(self) -> list[str]:
        """Expand the port name into a list of strings based on the width.

        Returns
        -------
        list[str]
            A list of expanded port names.
        """
        if self.width == 1:
            return [f"{self.name}"]
        return [pin.name(indexed=True) for pin in self.pins]

    def __eq__(self, other: object, /) -> bool:
        """Check equality with another object."""
        if other is None or not isinstance(other, Port):
            return False
        return self is other

    def __hash__(self) -> int:
        """Return the hash value."""
        return id(self)

    def serialize(self) -> dict:
        """Serialize the port to a dictionary."""
        return {
            "name": self.name,
            "io_direction": self.io_direction.value,
            "width": self.width,
            "is_clock": self.is_clock,
            "is_global": self.is_global,
            "net": self.net,
        }


@total_ordering
class TilePort(Port):
    """TilePort represents a port on a tile with a side and termination status.

    It is an immutable and comparable class. When sorting a list of TilePort instances,
    the order is determined first by the side of the tile in order of
    [north, east, south, west] then by the IO type in the order of
    [output, input, inout].

    Parameters
    ----------
    name : str
        The name of the port.
    io_direction : IO
        The I/O direction (INPUT, OUTPUT, INOUT).
    side_of_tile : Side
        The side of the tile where the port is located.
    term : bool
        Indicates if the port is a termination port. Defaults to False.
    wire_direction : Direction | None
        The direction the wire runs in. Defaults to None, which resolves to
        Direction.JUMP.
    source_name : str
        The source name of the wire connection. Defaults to "".
    x_offset : int
        The X-offset for wire routing. Defaults to 0.
    y_offset : int
        The Y-offset for wire routing. Defaults to 0.
    destination_name : str
        The destination name of the wire connection. Defaults to "".
    wire_count : int
        The number of wires per hop. Defaults to 1. The port's `width` is
        `wire_count` times the Manhattan distance of the offset: a spanning wire
        occupies one slice per hop it crosses.
    """

    _side_of_tile: Side
    _term: bool
    _tile: Tile | None
    _wire_direction: Direction
    _source_name: str
    _x_offset: int
    _y_offset: int
    _destination_name: str
    _wire_count: int

    def __init__(
        self,
        name: str,
        io_direction: IO,
        side_of_tile: Side,
        term: bool = False,
        wire_direction: Direction | None = None,
        source_name: str = "",
        x_offset: int = 0,
        y_offset: int = 0,
        destination_name: str = "",
        wire_count: int = 1,
    ) -> None:
        distance = abs(x_offset) + abs(y_offset)
        super().__init__(name, io_direction, wire_count * max(1, distance))
        self._side_of_tile = side_of_tile
        self._term = term
        self._tile = None
        self._wire_direction = (
            wire_direction if wire_direction is not None else Direction.JUMP
        )
        self._source_name = source_name
        self._x_offset = x_offset
        self._y_offset = y_offset
        self._destination_name = destination_name
        self._wire_count = wire_count

    __order = {Side.NORTH: 0, Side.EAST: 1, Side.SOUTH: 2, Side.WEST: 3, Side.ANY: 4}
    __io = {IO.OUTPUT: 0, IO.INPUT: 1, IO.INOUT: 2}

    @property
    def side_of_tile(self) -> Side:
        """The side of the tile where the port is located."""
        return self._side_of_tile

    @property
    def term(self) -> bool:
        """Whether the port is a termination port."""
        return self._term

    @property
    def tile(self) -> Tile | None:
        """The tile this port belongs to, or None while the port is unattached."""
        return self._tile

    @tile.setter
    def tile(self, tile: Tile) -> None:
        if self._tile is not None:
            raise ValueError(f"{self} already belongs to tile {self._tile.name}")
        self._tile = tile

    @property
    def wire_direction(self) -> Direction:
        """The direction the wire runs in."""
        return self._wire_direction

    @property
    def source_name(self) -> str:
        """The name of the wire's driving end, or NULL."""
        return self._source_name

    @property
    def x_offset(self) -> int:
        """The column offset from the driving to the receiving tile."""
        return self._x_offset

    @property
    def y_offset(self) -> int:
        """The row offset from the driving to the receiving tile."""
        return self._y_offset

    @property
    def destination_name(self) -> str:
        """The name of the wire's receiving end, or NULL."""
        return self._destination_name

    @property
    def wire_count(self) -> int:
        """The number of wires per hop."""
        return self._wire_count

    def __repr__(self) -> str:
        """Return a string representation of the TilePort."""
        name = f"{self.side_of_tile}"
        return (
            f"TilePort({{{name}}} {self.io_direction.value} "
            f"{self.name}[{self.width - 1}:0])"
        )

    @property
    def _sort_key(self) -> tuple[int, int]:
        """The [north, east, south, west] then [output, input, inout] sort key."""
        return (self.__order[self.side_of_tile], self.__io[self.io_direction])

    def __lt__(self, other: object, /) -> bool:
        """Less than comparison.

        `total_ordering` derives the remaining comparisons from this and the
        identity-based `__eq__` inherited from `Port`, so two distinct ports of
        equal rank compare as neither less than nor equal to each other.
        """
        if not isinstance(other, TilePort):
            raise TypeError(f"Cannot compare {self} with {other}")
        return self._sort_key < other._sort_key

    def serialize(self) -> dict:
        """Serialize the tile port to a dictionary."""
        return super().serialize() | {
            "side_of_tile": self.side_of_tile.value,
            "term": self.term,
            "tile": self.tile.name if self.tile is not None else None,
            "wire_direction": self.wire_direction.value,
            "source_name": self.source_name,
            "x_offset": self.x_offset,
            "y_offset": self.y_offset,
            "destination_name": self.destination_name,
            "wire_count": self.wire_count,
        }

    @property
    def has_source(self) -> bool:
        """Whether the wire this port belongs to starts at a named port.

        A CSV line may write either end as NULL to declare a wire that is
        driven or read nowhere; this asks about the driving end.
        """
        return self.source_name != NULL_PORT_NAME

    @property
    def has_destination(self) -> bool:
        """Whether the wire this port belongs to ends at a named port.

        The mirror image of `has_source`, asking about the receiving end.
        """
        return self.destination_name != NULL_PORT_NAME

    @property
    def is_null_terminated(self) -> bool:
        """Whether either end of this port's wire is the NULL placeholder."""
        return not (self.has_source and self.has_destination)

    @property
    def _spanned_pins(self) -> tuple[Pin, ...]:
        # A NULL-terminated wire exposes one slice per hop. With no offset
        # (a JUMP whose other end is NULL) that is no pins at all: the CSV line
        # declares a dangling wire, not a port.
        distance = abs(self.x_offset) + abs(self.y_offset)
        return self.pins[: self.wire_count * distance]

    @property
    def sm_pins(self) -> tuple[Pin, ...]:
        """The pins that face the switch matrix.

        A NULL-terminated spanning wire has no partner tile to hand the wire
        on to, so every hop's slice is driven or read locally. A named wire
        only exposes its first `wire_count` bits; the remaining slices pass
        through the tile untouched.
        """
        if self.is_null_terminated and self.wire_direction != Direction.SJUMP:
            return self._spanned_pins
        return self.pins[: self.wire_count]

    @property
    def top_pins(self) -> tuple[Pin, ...]:
        """The pins that face the neighbouring tile at the fabric top level.

        The mirror image of `sm_pins`: a named spanning wire hands its last
        `wire_count` bits to the neighbour, everything else stays inside.
        """
        if self.wire_direction == Direction.SJUMP:
            return self.pins
        if self.is_null_terminated:
            return self._spanned_pins
        return self.pins[self.width - self.wire_count :]

    def _pin_names(
        self, pins: tuple[Pin, ...], indexed: bool, prefix: str, escape: bool
    ) -> list[str]:
        names = [pin.name(indexed, prefix) for pin in pins]
        if indexed and escape:
            return [n.replace("[", r"\[").replace("]", r"\]") for n in names]
        return names

    def get_port_regex(self, indexed: bool = False, prefix: str = "") -> str:
        """Expand port information to individual wire names.

        Generates a regex expression for this port, accounting for wire count and
        offset calculations.

        Parameters
        ----------
        indexed : bool, optional
            If True, wire names use bracket notation (e.g., `port[0]`).
            If False, wire names use simple concatenation (e.g., `port0`).
            Defaults to False.
        prefix : str, optional
            A prefix to prepend to the port name, by default "".

        Returns
        -------
        str
            A regex expression matching the port's wire names.
        """
        if self.width == 1:
            return f"{prefix}{self.name}"
        if indexed:
            return rf"{prefix}{self.name}\[\d+\]"
        return rf"{prefix}{self.name}\d+"

    def expand_port_info_by_name(
        self, indexed: bool = False, prefix: str = "", escape: bool = False
    ) -> list[str]:
        """Expand port information to individual wire names.

        Generates a list of individual wire names for this port, accounting for
        wire count and offset calculations. For termination ports (NULL), the
        wire count is multiplied by the Manhattan distance.

        Parameters
        ----------
        indexed : bool, optional
            If True, wire names use bracket notation (e.g., `port[0]`).
            If False, wire names use simple concatenation (e.g., `port0`).
            Defaults to False.
        prefix : str, optional
            A prefix to prepend to the port name, by default "".
        escape : bool, optional
            If True, escape special characters in the port names (e.g., for regex),
            by default False.

        Returns
        -------
        list[str]
            List of individual wire names for this port.
        """
        return self._pin_names(self.sm_pins, indexed, prefix, escape)

    def expand_port_info_by_name_top(
        self, indexed: bool = False, prefix: str = "", escape: bool = False
    ) -> list[str]:
        """Expand port information for top-level connections.

        Similar to expand_port_info_by_name but specifically for top-level tile
        connections. The start index is calculated differently to handle
        the top slice of wires for routing fabric connections.

        Parameters
        ----------
        indexed : bool, optional
            If True, wire names use bracket notation (e.g., `port[0]`).
            If False, wire names use simple concatenation (e.g., `port0`).
            Defaults to False.
        prefix : str, optional
            A prefix to prepend to the port name, by default "".
        escape : bool, optional
            If True, escape special characters in the port names (e.g., for regex),
            by default False.

        Returns
        -------
        list[str]
            List of individual wire names for top-level connections.
        """
        return self._pin_names(self.top_pins, indexed, prefix, escape)


class SJumpPort(TilePort):
    """A tile port that faces the switch matrix of the surrounding supertile.

    An SJUMP wire is one-way and never leaves the supertile, so unlike a
    spanning `TilePort` it has no offset, no partner port at the far side of
    the tile and no NULL-terminated expansion: every bit faces both the tile's
    own switch matrix and the supertile's. `SJumpWire` pairs it with the
    supertile matrix port it reaches.

    Parameters
    ----------
    name : str
        The name of the port.
    io_direction : IO
        `IO.OUTPUT` for a signal leaving the tile towards the supertile matrix,
        `IO.INPUT` for one arriving from it.
    wire_count : int
        The number of wires, which is also the port's width.
    """

    def __init__(self, name: str, io_direction: IO, wire_count: int) -> None:
        is_output = io_direction == IO.OUTPUT
        super().__init__(
            name=name,
            io_direction=io_direction,
            side_of_tile=Side.ANY,
            wire_direction=Direction.SJUMP,
            source_name=name if is_output else NULL_PORT_NAME,
            destination_name=NULL_PORT_NAME if is_output else name,
            wire_count=wire_count,
        )

    @property
    def sm_pins(self) -> tuple[Pin, ...]:
        """Every pin faces the tile's own switch matrix."""
        return self.pins

    @property
    def top_pins(self) -> tuple[Pin, ...]:
        """Every pin faces the supertile, which is this port's top level."""
        return self.pins

    def __repr__(self) -> str:
        """Return a string representation of the SJumpPort."""
        return f"SJumpPort({self.io_direction.value} {self.name}[{self.width - 1}:0])"


class BelPort(Port):
    """A port on a BEL (Basic Element of Logic).

    One `BelPort` mirrors one port of the BEL's HDL module; its `pins` are the
    individual bits. A multi-bit port's pins are named the flat way the tile HDL
    unrolls them (`A0`, `A1`, ...), a single-bit port's pin keeps the bare name.

    Parameters
    ----------
    name : str
        The name of the port in the BEL's HDL module, without prefix.
    io_direction : IO
        The I/O direction (INPUT, OUTPUT, INOUT).
    width : int
        The bit width of the port.
    kind : BelPortKind
        The role of the port. Defaults to `BelPortKind.INTERNAL`.
    prefix : str
        Prefix added to the port name, the BEL prefix. Defaults to "".
    carry : str | None
        The name of the carry chain the port belongs to. Defaults to None.
    local_shared : str | None
        The tile-local shared signal (`RESET` or `ENABLE`) the port is driven
        by. Defaults to None.
    is_clock : bool
        Whether the port carries a clock. Defaults to False.
    is_global : bool
        Whether the port is driven by a fabric-wide signal. Defaults to False.
    net : str
        The net the port belongs to. Defaults to "", the global net.
    """

    _kind: BelPortKind
    _prefix: str
    _carry: str | None
    _local_shared: str | None
    _bel: Bel | None

    def __init__(
        self,
        name: str,
        io_direction: IO,
        width: int,
        kind: BelPortKind = BelPortKind.INTERNAL,
        prefix: str = "",
        carry: str | None = None,
        local_shared: str | None = None,
        is_clock: bool = False,
        is_global: bool = False,
        net: str = "",
    ) -> None:
        super().__init__(name, io_direction, width, is_clock, is_global, net)
        self._kind = kind
        self._prefix = prefix
        self._carry = carry
        self._local_shared = local_shared
        self._bel = None

    @property
    def kind(self) -> BelPortKind:
        """The role of the port."""
        return self._kind

    @property
    def prefix(self) -> str:
        """The prefix added to the port name."""
        return self._prefix

    @property
    def base_name(self) -> str:
        """The port name in the BEL's HDL module, without prefix."""
        return self._name

    @property
    def carry(self) -> str | None:
        """The carry chain the port belongs to, or None."""
        return self._carry

    @property
    def local_shared(self) -> str | None:
        """The tile-local shared signal driving the port, or None."""
        return self._local_shared

    @property
    def bel(self) -> Bel | None:
        """The BEL this port belongs to, or None while the port is unattached."""
        return self._bel

    @bel.setter
    def bel(self, bel: Bel) -> None:
        if self._bel is not None:
            raise ValueError(f"{self} already belongs to BEL {self._bel.name}")
        self._bel = bel

    def __repr__(self) -> str:
        """Return a string representation of the BelPort."""
        return f"BelPort({self.io_direction.value} {self.name}[{self.width - 1}:0])"

    @property
    def name(self) -> str:
        """The port name including its prefix."""
        return f"{self.prefix}{self._name}"

    def pin_name(self, index: int, indexed: bool = False, prefix: str = "") -> str:
        """Return the HDL wire name of bit `index` of this port.

        Parameters
        ----------
        index : int
            The bit index.
        indexed : bool, optional
            Bracket notation when True, by default False.
        prefix : str, optional
            A prefix to prepend, by default "".

        Returns
        -------
        str
            The wire name; a single-bit port keeps its bare name.
        """
        if self.width == 1:
            return f"{prefix}{self.name}"
        return super().pin_name(index, indexed, prefix)

    def expand(self) -> list[str]:
        """Expand the port name into a list of strings based on the width.

        Returns
        -------
        list[str]
            A list of expanded port names.
        """
        return [pin.name(indexed=True) for pin in self.pins]

    def serialize(self) -> dict:
        """Serialize the BEL port to a dictionary."""
        return super().serialize() | {
            "kind": self.kind.value,
            "prefix": self.prefix,
            "carry": self.carry,
            "local_shared": self.local_shared,
        }


class ConfigPort(Port):
    """A port that carries configuration bits into a generated module.

    Every configurable module (a BEL, a switch matrix, a tile) receives its
    configuration through such a port.
    """

    def __repr__(self) -> str:
        """Return a string representation of the ConfigPort."""
        return f"ConfigPort({self.io_direction.value} {self.name}[{self.width - 1}:0])"


class BelConfigPort(ConfigPort):
    """The configuration port of a BEL, with the BEL's feature map.

    The port is `ConfigBits` in the BEL's HDL module, or the port marked with
    the `CONFIG` attribute. A BEL has at most one.

    Parameters
    ----------
    name : str
        The name of the port in the BEL's HDL module.
    io_direction : IO
        The I/O direction.
    width : int
        The number of configuration bits.
    bel_map : dict[str, dict]
        The BEL's feature map (`BelMap` attribute), in bit order.
    """

    _bel_map: dict[str, dict]
    _bel: Bel | None

    def __init__(
        self, name: str, io_direction: IO, width: int, bel_map: dict[str, dict]
    ) -> None:
        super().__init__(name, io_direction, width)
        self._bel_map = bel_map
        self._bel = None

    @property
    def bel_map(self) -> dict[str, dict]:
        """The BEL's feature map, in bit order."""
        return self._bel_map

    @property
    def bel(self) -> Bel | None:
        """The BEL this port belongs to, or None while the port is unattached."""
        return self._bel

    @bel.setter
    def bel(self, bel: Bel) -> None:
        if self._bel is not None:
            raise ValueError(f"{self} already belongs to BEL {self._bel.name}")
        self._bel = bel

    def __repr__(self) -> str:
        """Return a string representation of the BelConfigPort."""
        return (
            f"BelConfigPort({self.io_direction.value} {self.name}[{self.width - 1}:0])"
        )

    def serialize(self) -> dict:
        """Serialize the BEL config port to a dictionary."""
        return super().serialize() | {"bel_map": self.bel_map}


class SwitchMatrixPort(Port):
    """A port of a tile's switch-matrix module.

    The switch matrix is a module of its own inside the tile, and this is its
    interface: every tile port that reaches the matrix, every BEL port, and the
    constant sources. Its pins are the nodes the matrix file (`.list` / `.csv`)
    connects.

    Pins are named the flat way the matrix HDL does (`N1END0`). A port built
    from a `TilePort` or a `BelPort` keeps that port as its `origin`; a BEL
    port's pins take the BEL's own flat names. A constant is a `literal`
    scalar whose pin is named exactly as the port.

    Parameters
    ----------
    name : str
        The name of the port.
    io_direction : IO
        The I/O direction as seen from the switch matrix: a mux output is
        `IO.OUTPUT`, a mux input is `IO.INPUT`.
    width : int
        The bit width of the port. Defaults to 1.
    origin : TilePort | BelPort | None
        The tile or BEL port this port wires to, or None for a constant.
        Defaults to None.
    prefix : str
        Prepended to the pin names of a wire port (a supertile matrix prefixes
        each child tile's pins with the tile name). Defaults to "".
    literal : bool
        Whether the single pin is named exactly as the port (a constant),
        instead of `{name}{index}`. Defaults to False.
    """

    _origin: TilePort | BelPort | None
    _prefix: str
    _literal: bool

    def __init__(
        self,
        name: str,
        io_direction: IO,
        width: int = 1,
        origin: TilePort | BelPort | None = None,
        prefix: str = "",
        literal: bool = False,
    ) -> None:
        super().__init__(name, io_direction, width)
        self._origin = origin
        self._prefix = prefix
        self._literal = literal

    @classmethod
    def from_tile_port(cls, port: TilePort, prefix: str = "") -> SwitchMatrixPort:
        """Build the matrix port for the switch-matrix-facing pins of a tile port.

        Parameters
        ----------
        port : TilePort
            The tile port. Its `sm_pins` set the width; the direction is the
            tile port's own (the matrix drives a tile output).
        prefix : str, optional
            Prefix for the pin names, by default "".

        Returns
        -------
        SwitchMatrixPort
            The matrix port.
        """
        return cls(port.name, port.io_direction, len(port.sm_pins), port, prefix)

    @classmethod
    def from_bel_port(cls, port: BelPort) -> SwitchMatrixPort:
        """Build the matrix port for a BEL port.

        Parameters
        ----------
        port : BelPort
            The BEL port. The direction flips: the matrix drives a BEL input
            and reads a BEL output.

        Returns
        -------
        SwitchMatrixPort
            The matrix port.
        """
        io = IO.OUTPUT if port.is_input else IO.INPUT
        return cls(port.name, io, port.width, port)

    @property
    def origin(self) -> TilePort | BelPort | None:
        """The tile or BEL port this port wires to, or None for a constant."""
        return self._origin

    def pin_name(self, index: int, indexed: bool = False, prefix: str = "") -> str:
        """Return the HDL wire name of bit `index`.

        Parameters
        ----------
        index : int
            The bit index.
        indexed : bool, optional
            Bracket notation when True, by default False.
        prefix : str, optional
            A prefix to prepend, by default "".

        Returns
        -------
        str
            The wire name; a `literal` port keeps its bare name and a BEL port
            takes the BEL's flat name.
        """
        if isinstance(self._origin, BelPort):
            return f"{prefix}{self._origin.pin_name(index)}"
        if self._literal:
            return f"{prefix}{self.name}"
        return super().pin_name(index, indexed, f"{prefix}{self._prefix}")

    def __repr__(self) -> str:
        """Return a string representation of the SwitchMatrixPort."""
        return (
            f"SwitchMatrixPort({self.io_direction.value} "
            f"{self.name}[{self.width - 1}:0])"
        )


GenericPort = Port | TilePort | BelPort | ConfigPort | BelConfigPort | SwitchMatrixPort
