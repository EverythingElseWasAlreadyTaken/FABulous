"""Wire class for managing connections between tiles."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fabulous.fabric_definition.define import IO, Direction
from fabulous.fabric_definition.port import NULL_PORT_NAME, SwitchMatrixPort


@dataclass(frozen=True, eq=True)
class Wire:
    """Store wire connections that span across multiple tiles.

    If working on connections between two adjacent tiles,
    the Port class should have all the required information.
    The main use of this class is to assist model generation,
    where information at individual wire level is needed.

    Attributes
    ----------
    direction : Direction
        The direction of the wire
    source : str
        The source name of the wire
    x_offset : int
        The X-offset of the wire
    y_offset : int
        The Y-offset of the wire
    destination : str
        The destination name of the wire
    sourceTile : str
        The source tile name of the wire
    destinationTile : str
        The destination tile name of the wire
    """

    direction: Direction
    source: str
    x_offset: int
    y_offset: int
    destination: str
    sourceTile: str
    destinationTile: str

    def __repr__(self) -> str:
        """Return string representation of the wire.

        Returns
        -------
        str
            A compact string showing source, offsets, and destination.
        """
        return f"{self.source}-X{self.x_offset}Y{self.y_offset}>{self.destination}"

    def __eq__(self, __o: object, /) -> bool:
        """Check if two `Wire` objects are equal.

        Two wires are considered equal if they have the same
        source and destination names.

        Parameters
        ----------
        __o : object
            The object to compare with.

        Returns
        -------
        bool
            True if the wires are equal, False otherwise.
        """
        if __o is None or not isinstance(__o, Wire):
            return False
        return self.source == __o.source and self.destination == __o.destination

    def __post_init__(self) -> None:
        """Validate wire configuration after initialization.

        Check that source and destination tile names follow the expected format
        (X{num}Y{num}) or are empty for boundary conditions. This validation
        ensures that wires don't reference tiles outside the fabric boundaries.

        Raises
        ------
        ValueError
            If source or destination tile names are invalid for non-zero offsets.
        """

        def validSourceDestination(name: str) -> bool:
            """Check if the source or destination tile name is valid."""
            if self.x_offset == 0 and self.y_offset == 0:
                return True
            if not name:
                return True
            return re.match(r"^X\d+Y\d+$", name) is not None

        if not validSourceDestination(self.sourceTile):
            raise ValueError(
                f"Invalid source tile name: {self.sourceTile} for wire {self}, "
                "your source is located out side of the fabric, please check the "
                "source and destination port offset."
            )
        if not validSourceDestination(self.destinationTile):
            raise ValueError(
                f"Invalid destination tile name: {self.destinationTile} for wire "
                f"{self}, "
                "your destination is located out side of the fabric, please check "
                "the source and destination port offset."
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
            destination = SwitchMatrixPort(destination_name, IO.INPUT, wire_count)
        return cls(source, destination)

    @property
    def wire_count(self) -> int:
        """The number of wires."""
        if self.source is not None:
            return self.source.width
        return self.destination.width  # type: ignore[union-attr]  # see __post_init__
