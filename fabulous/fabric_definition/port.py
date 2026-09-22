"""Port class hierarchy for FPGA fabric.

This module contains the port class hierarchy for representing different types of ports
in the FPGA fabric:
- Pin: A single bit of a port; the node of the routing graph
- Port: Base class for all port types
- TilePort: Port on a tile with side and termination information
- SJumpPort: Tile port facing the switch matrix of the surrounding supertile
- BelPort: Port on a BEL (Basic Element of Logic)
- SharedPort: A port shared between multiple BELs
- SwitchMatrixPort: A port of a tile's switch-matrix module
- ConfigPort: A configuration port with features
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property, total_ordering
from typing import TYPE_CHECKING

from fabulous.fabric_definition.define import (
    IO,
    Direction,
    FeatureType,
    FeatureValue,
    Side,
)

if TYPE_CHECKING:
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
    def name_is_null(self) -> bool:
        """Whether the port name is the NULL placeholder.

        Only the port's own name is considered. A wire's `source_name` and
        `destination_name` are NULL independently of it and of each other.
        """
        return self._name == NULL_PORT_NAME

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
    tile : Tile | None
        The tile this port belongs to. Set once at construction and read-only
        thereafter. Defaults to None, leaving the port unattached.
    wire_direction : Direction | None
        The wire direction (for backward compatibility with legacy Port).
        Defaults to None, which resolves to Direction.JUMP.
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
    # Backward compatibility fields for wire routing
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
        tile: Tile | None = None,
        # Backward compatibility parameters
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
        self._tile = tile
        # Backward compatibility
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

    # Backward compatibility properties
    @property
    def wire_direction(self) -> Direction:
        """Wire direction (backward compatibility)."""
        return self._wire_direction

    @property
    def source_name(self) -> str:
        """Source name (backward compatibility)."""
        return self._source_name

    @property
    def x_offset(self) -> int:
        """X-offset (backward compatibility)."""
        return self._x_offset

    @property
    def y_offset(self) -> int:
        """Y-offset (backward compatibility)."""
        return self._y_offset

    @property
    def destination_name(self) -> str:
        """Destination name (backward compatibility)."""
        return self._destination_name

    @property
    def wire_count(self) -> int:
        """Wire count (backward compatibility)."""
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
    def _is_null_terminated(self) -> bool:
        return (
            self.source_name == NULL_PORT_NAME
            or self.destination_name == NULL_PORT_NAME
        )

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

        The NULL placeholder port of a one-sided wire has none. A
        NULL-terminated spanning wire has no partner tile to hand the wire on
        to, so every hop's slice is driven or read locally. A named wire only
        exposes its first `wire_count` bits; the remaining slices pass through
        the tile untouched.
        """
        if self.name_is_null:
            return ()
        if self._is_null_terminated and self.wire_direction != Direction.SJUMP:
            return self._spanned_pins
        return self.pins[: self.wire_count]

    @property
    def top_pins(self) -> tuple[Pin, ...]:
        """The pins that face the neighbouring tile at the fabric top level.

        The mirror image of `sm_pins`: a named spanning wire hands its last
        `wire_count` bits to the neighbour, everything else stays inside.
        """
        if self.name_is_null:
            return ()
        if self.wire_direction == Direction.SJUMP:
            return self.pins
        if self._is_null_terminated:
            return self._spanned_pins
        return self.pins[self.width - self.wire_count :]

    def _pin_names(
        self, pins: tuple[Pin, ...], indexed: bool, prefix: str, escape: bool
    ) -> list[str]:
        names = [pin.name(indexed, prefix) for pin in pins]
        if indexed and escape:
            return [n.replace("[", r"\[").replace("]", r"\]") for n in names]
        return names

    # Backward compatibility methods from old Port class
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
        if self.width == 1 and not self.name_is_null:
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

    def expand_port_info(
        self, mode: str = "SwitchMatrix"
    ) -> tuple[list[str], list[str]]:
        """Expand the port information to the individual bit signal.

        If 'Indexed' is in the mode, then brackets are added to the signal name.

        Parameters
        ----------
        mode : str, optional
            Mode for expansion. Defaults to "SwitchMatrix".
            Possible modes are 'all', 'allIndexed', 'Top', 'TopIndexed', 'AutoTop',
            'AutoTopIndexed', 'SwitchMatrix', 'SwitchMatrixIndexed', 'AutoSwitchMatrix',
            'AutoSwitchMatrixIndexed'

        Returns
        -------
        tuple[list[str], list[str]]
            A tuple of two lists. The first list contains the source names of the ports
            and the second list contains the destination names of the ports.
        """
        inputs, outputs = [], []
        thisRange = 0
        openIndex = ""
        closeIndex = ""

        if "Indexed" in mode:
            openIndex = "("
            closeIndex = ")"

        # range (wires-1 downto 0) as connected to the switch matrix
        if mode == "SwitchMatrix" or mode == "SwitchMatrixIndexed":
            thisRange = self.wire_count
        elif mode == "AutoSwitchMatrix" or mode == "AutoSwitchMatrixIndexed":
            if self.wire_direction == Direction.SJUMP:
                thisRange = self.wire_count
            elif self.source_name == "NULL" or self.destination_name == "NULL":
                # the following line connects all wires to the switch matrix in the case
                # one port is NULL (typically termination)
                thisRange = (abs(self.x_offset) + abs(self.y_offset)) * self.wire_count
            else:
                # the following line connects all bottom wires to the switch matrix in
                # the case begin and end ports are used
                thisRange = self.wire_count
        # range ((wires*distance)-1 downto 0) as connected to the tile top
        elif mode in [
            "all",
            "allIndexed",
            "Top",
            "TopIndexed",
            "AutoTop",
            "AutoTopIndexed",
        ]:
            thisRange = (abs(self.x_offset) + abs(self.y_offset)) * self.wire_count

        # the following three lines are needed to get the top line[wires] that
        # are actually the connection from a switch matrix to the routing fabric
        startIndex = 0
        if mode in ["Top", "TopIndexed"]:
            startIndex = (
                (abs(self.x_offset) + abs(self.y_offset)) - 1
            ) * self.wire_count

        elif mode in ["AutoTop", "AutoTopIndexed"]:
            if self.source_name == "NULL" or self.destination_name == "NULL":
                # in case one port is NULL, then the all the other port wires get
                # connected to the switch matrix.
                startIndex = 0
            else:
                # "normal" case as for the CLBs
                startIndex = (
                    (abs(self.x_offset) + abs(self.y_offset)) - 1
                ) * self.wire_count
        if startIndex == thisRange:
            thisRange = 1

        for i in range(startIndex, thisRange):
            if self.source_name != "NULL":
                inputs.append(f"{self.source_name}{openIndex}{str(i)}{closeIndex}")

            if self.destination_name != "NULL":
                outputs.append(
                    f"{self.destination_name}{openIndex}{str(i)}{closeIndex}"
                )
        return inputs, outputs


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
    tile : Tile | None
        The tile this port belongs to. Defaults to None, leaving it unattached.
    """

    def __init__(
        self,
        name: str,
        io_direction: IO,
        wire_count: int,
        tile: Tile | None = None,
    ) -> None:
        is_output = io_direction == IO.OUTPUT
        super().__init__(
            name=name,
            io_direction=io_direction,
            side_of_tile=Side.ANY,
            tile=tile,
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

    Parameters
    ----------
    name : str
        The name of the port.
    io_direction : IO
        The I/O direction (INPUT, OUTPUT, INOUT).
    width : int
        The bit width of the port.
    prefix : str
        Prefix added to the port name. Defaults to "".
    external : bool
        Whether the port is exposed externally. Defaults to False.
    control : bool
        Whether the port is a control signal. Defaults to False.
    is_clock : bool
        Whether the port carries a clock. Defaults to False.
    is_global : bool
        Whether the port is driven by a fabric-wide signal. Defaults to False.
    net : str
        The net the port belongs to. Defaults to "", the global net.
    """

    _prefix: str
    _external: bool
    _control: bool

    def __init__(
        self,
        name: str,
        io_direction: IO,
        width: int,
        prefix: str = "",
        external: bool = False,
        control: bool = False,
        is_clock: bool = False,
        is_global: bool = False,
        net: str = "",
    ) -> None:
        super().__init__(name, io_direction, width, is_clock, is_global, net)
        self._prefix = prefix
        self._external = external
        self._control = control

    @property
    def prefix(self) -> str:
        """The prefix added to the port name."""
        return self._prefix

    @property
    def external(self) -> bool:
        """Whether the port is exposed externally."""
        return self._external

    @property
    def control(self) -> bool:
        """Whether the port is a control signal."""
        return self._control

    def __repr__(self) -> str:
        """Return a string representation of the BelPort."""
        return f"BelPort({self.io_direction.value} {self.name}[{self.width - 1}:0])"

    @property
    def name(self) -> str:
        """The port name including its prefix."""
        return f"{self.prefix}{self._name}"

    def expand(self) -> list[str]:
        """Expand the port name into a list of strings based on the width.

        Returns
        -------
        list[str]
            A list of expanded port names.
        """
        if self.width == 1:
            return [f"{self.name}"]
        return [f"{self.name}[{i}]" for i in range(self.width)]

    def serialize(self) -> dict:
        """Serialize the BEL port to a dictionary."""
        return super().serialize() | {
            "prefix": self.prefix,
            "external": self.external,
            "control": self.control,
        }


class ConfigPort(Port):
    """A configuration port with features.

    Parameters
    ----------
    name : str
        The name of the port.
    io_direction : IO
        The I/O direction (INPUT, OUTPUT, INOUT).
    width : int
        The bit width of the port.
    features : list[FeatureValue] | None
        List of features associated with this port.
        Defaults to None, which resolves to an empty list.
    feature_type : FeatureType
        The type of feature encoding. Defaults to FeatureType.ENUMERATE.
    """

    _features: list[FeatureValue]
    _feature_type: FeatureType

    def __init__(
        self,
        name: str,
        io_direction: IO,
        width: int,
        features: list[FeatureValue] | None = None,
        feature_type: FeatureType = FeatureType.ENUMERATE,
    ) -> None:
        super().__init__(name, io_direction, width)
        self._features = features if features is not None else []
        self._feature_type = feature_type

    @property
    def features(self) -> list[FeatureValue]:
        """The list of features associated with this port."""
        return self._features

    @property
    def feature_type(self) -> FeatureType:
        """The type of feature encoding."""
        return self._feature_type

    def __repr__(self) -> str:
        """Return a string representation of the ConfigPort."""
        return (
            f"ConfigPort({self.io_direction.value} "
            f"{self.name}[{self.width - 1}:0], features={self.features})"
        )

    def serialize(self) -> dict:
        """Serialize the config port to a dictionary."""
        return super().serialize() | {
            "features": self.features,
            "feature_type": self.feature_type.value,
        }


class SharedPort(Port):
    """A port shared between multiple BELs.

    Parameters
    ----------
    name : str
        The name of the port.
    io_direction : IO
        The I/O direction (INPUT, OUTPUT, INOUT).
    width : int
        The bit width of the port.
    shared_with : str
        Name of the entity this port is shared with. Defaults to "".
    """

    _shared_with: str

    def __init__(
        self,
        name: str,
        io_direction: IO,
        width: int,
        shared_with: str = "",
    ) -> None:
        super().__init__(name, io_direction, width)
        self._shared_with = shared_with

    @property
    def shared_with(self) -> str:
        """Name of the entity this port is shared with."""
        return self._shared_with

    def share_expand(self) -> list[str]:
        """Expand the port name into a list of strings based on the width.

        Returns
        -------
        list[str]
            A list of expanded port names using the shared_with name.
        """
        expand = []
        if self.width == 1:
            expand.append(f"{self.shared_with}")
        else:
            for i in range(self.width):
                expand.append(f"{self.shared_with}[{i}]")

        return expand

    def serialize(self) -> dict:
        """Serialize the shared port to a dictionary."""
        return super().serialize() | {"shared_with": self.shared_with}


# Type alias for any port type
class SwitchMatrixPort(Port):
    """A port of a tile's switch-matrix module.

    The switch matrix is a module of its own inside the tile, and this is its
    interface: every tile port that reaches the matrix, every BEL port, and the
    constant sources. Its pins are the nodes the matrix file (`.list` / `.csv`)
    connects.

    Pins are named the flat way the matrix HDL does (`N1END0`). A BEL signal
    or a constant arrives already flattened, so it is a `literal` scalar whose
    pin is named exactly as the port. A port built from a `TilePort` keeps
    that port as its `origin`.

    Parameters
    ----------
    name : str
        The name of the port.
    io_direction : IO
        The I/O direction as seen from the switch matrix: a mux output is
        `IO.OUTPUT`, a mux input is `IO.INPUT`.
    width : int
        The bit width of the port. Defaults to 1.
    origin : TilePort | None
        The tile port this port wires to, or None for a BEL port / constant.
        Defaults to None.
    prefix : str
        Prepended to the pin names of a wire port (a supertile matrix prefixes
        each child tile's pins with the tile name). Defaults to "".
    literal : bool
        Whether the single pin is named exactly as the port (a BEL signal or a
        constant), instead of `{name}{index}`. Defaults to False.
    """

    _origin: TilePort | None
    _prefix: str
    _literal: bool

    def __init__(
        self,
        name: str,
        io_direction: IO,
        width: int = 1,
        origin: TilePort | None = None,
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

    @property
    def origin(self) -> TilePort | None:
        """The tile port this port wires to, or None for a BEL / constant."""
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
            The wire name; a `literal` port keeps its bare name.
        """
        if self._literal:
            return f"{prefix}{self.name}"
        return super().pin_name(index, indexed, f"{prefix}{self._prefix}")

    def __repr__(self) -> str:
        """Return a string representation of the SwitchMatrixPort."""
        return (
            f"SwitchMatrixPort({self.io_direction.value} "
            f"{self.name}[{self.width - 1}:0])"
        )


GenericPort = Port | TilePort | BelPort | ConfigPort | SharedPort | SwitchMatrixPort
