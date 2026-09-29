"""Jump wires: fixed wires between switch matrix ports of a tile or supertile."""

from __future__ import annotations

from dataclasses import dataclass

from fabulous.fabric_definition.define import IO
from fabulous.fabric_definition.port import (
    NULL_PORT_NAME,
    SJumpPort,
    SwitchMatrixPort,
)


@dataclass(frozen=True)
class JumpWire:
    """A wire that leaves the switch matrix and comes straight back into it.

    A `JUMP` line in the tile CSV never reaches the tile boundary: the matrix
    drives `source` (`J_SR_BEG`), the tile loops it back, and the matrix reads
    it on `destination` (`J_SR_END`). Both are ports of the switch matrix, not
    of the tile. An end written as `NULL` is None; a jump with no source is how
    the CSV declares a constant matrix input (`JUMP,NULL,0,0,GND,1`).

    Attributes
    ----------
    source : SwitchMatrixPort | None
        The matrix output the wire starts at.
    destination : SwitchMatrixPort | None
        The matrix input the wire ends at.
    """

    source: SwitchMatrixPort | None
    destination: SwitchMatrixPort | None

    def __post_init__(self) -> None:
        """Reject a wire with neither end."""
        if self.source is None and self.destination is None:
            raise ValueError("A jump wire needs a source or a destination")

    @classmethod
    def create(
        cls, source_name: str, destination_name: str, wire_count: int
    ) -> JumpWire:
        """Build a jump wire and its matrix ports from a CSV line.

        Parameters
        ----------
        source_name : str
            The source port name, or `NULL`.
        destination_name : str
            The destination port name, or `NULL`.
        wire_count : int
            The number of wires (the width of both ports).

        Returns
        -------
        JumpWire
            The wire.
        """
        source = None
        if source_name != NULL_PORT_NAME:
            source = SwitchMatrixPort(source_name, IO.OUTPUT, wire_count)
        destination = None
        if destination_name != NULL_PORT_NAME:
            destination = SwitchMatrixPort(
                destination_name, IO.INPUT, wire_count, constant=source is None
            )
        return cls(source, destination)

    @property
    def wire_count(self) -> int:
        """The number of wires."""
        if self.source is not None:
            return self.source.width
        return self.destination.width  # type: ignore[union-attr]  # see __post_init__


@dataclass(frozen=True)
class SJumpWire:
    """A wire between a child tile and its supertile's switch matrix.

    An SJUMP line declares one end: a port of the child tile. The other end is
    a port of the supertile's switch matrix, named `{tile}_{port}` so the
    matrix can tell apart the same port of two child tiles. This pairs the two
    and owns that naming, the direction flip between them (what the child
    drives, the matrix reads) and the offset from one end to the other.

    The supertile's switch matrix lives in the wrapper at its master tile, so
    the wire spans from the child tile's cell of the `tileMap` to the master's.
    That is fixed by the supertile's own layout, independently of where the
    supertile is placed in the fabric.

    Attributes
    ----------
    tile_name : str
        The child tile's name, which prefixes the matrix-side pin names.
    x : int
        The child tile's column in the supertile's `tileMap`.
    y : int
        The child tile's row in the supertile's `tileMap`.
    master_x : int
        The master tile's column in the supertile's `tileMap`.
    master_y : int
        The master tile's row in the supertile's `tileMap`.
    child_port : SJumpPort
        The child tile's port.
    matrix_port : SwitchMatrixPort
        The supertile switch matrix's port.
    """

    tile_name: str
    x: int
    y: int
    master_x: int
    master_y: int
    child_port: SJumpPort
    matrix_port: SwitchMatrixPort

    @classmethod
    def create(
        cls, tile_name: str, x: int, y: int, master: tuple[int, int], port: SJumpPort
    ) -> SJumpWire:
        """Build the wire and the supertile matrix port for a child tile port.

        Parameters
        ----------
        tile_name : str
            The child tile's name.
        x : int
            The child tile's column in the supertile's `tileMap`.
        y : int
            The child tile's row in the supertile's `tileMap`.
        master : tuple[int, int]
            The master tile's `(column, row)` in the supertile's `tileMap`.
        port : SJumpPort
            The child tile's port.

        Returns
        -------
        SJumpWire
            The wire.
        """
        io = IO.INPUT if port.is_output else IO.OUTPUT
        matrix_port = SwitchMatrixPort(port.name, io, port.width, port, f"{tile_name}_")
        return cls(tile_name, x, y, master[0], master[1], port, matrix_port)

    @property
    def signal_name(self) -> str:
        """The name of the vector joining the two ends inside the wrapper."""
        return f"{self.tile_name}_{self.child_port.name}"

    @property
    def is_forward(self) -> bool:
        """Whether the child tile drives the supertile matrix."""
        return self.child_port.is_output

    @property
    def wire_count(self) -> int:
        """The number of wires."""
        return self.child_port.width

    @property
    def x_offset(self) -> int:
        """Columns from this wire's source cell to its destination cell."""
        delta = self.master_x - self.x
        return delta if self.is_forward else -delta

    @property
    def y_offset(self) -> int:
        """Rows from this wire's source cell to its destination cell."""
        delta = self.master_y - self.y
        return delta if self.is_forward else -delta

    def source_cell(self, base_x: int, base_y: int) -> tuple[int, int]:
        """Return the fabric cell this wire starts in.

        That is the cell that owns the wire: the child tile's for a forward
        wire, the master tile's (which hosts the matrix) for a reverse one. The
        destination cell is this plus `(x_offset, y_offset)`.

        Parameters
        ----------
        base_x : int
            The fabric column of the supertile placement's top-left cell.
        base_y : int
            The fabric row of the supertile placement's top-left cell.

        Returns
        -------
        tuple[int, int]
            The `(column, row)` of the source cell.
        """
        if self.is_forward:
            return base_x + self.x, base_y + self.y
        return base_x + self.master_x, base_y + self.master_y

    @property
    def pin_names(self) -> list[tuple[str, str]]:
        """The `(source, destination)` wire name of each bit, in bit order."""
        pairs = zip(self.child_port.pins, self.matrix_port.pins, strict=True)
        if self.is_forward:
            return [(child.name(), matrix.name()) for child, matrix in pairs]
        return [(matrix.name(), child.name()) for child, matrix in pairs]
