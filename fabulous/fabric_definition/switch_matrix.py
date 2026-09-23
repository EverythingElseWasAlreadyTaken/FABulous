"""Switch matrix construct for the FABulous fabric model.

A tile's switch matrix is the programmable interconnect: which sources may drive
each destination inside the tile. The connectivity is declared in the tile's
matrix file (a `.csv` adjacency matrix or a `.list` of pairs) and read **once**
into this dataclass as connections between `Pin`s of the matrix's own
`SwitchMatrixPort`s, in canonical port/BEL order. RTL generation
lives in `fabulous.fabric_generator.gen_fabric.gen_switchmatrix`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from fabulous.custom_exception import InvalidFileType, InvalidSwitchMatrixDefinition
from fabulous.fabric_definition.define import IO, SWITCH_MATRIX_CONSTANTS, BelPortKind
from fabulous.fabric_definition.port import Pin, SwitchMatrixPort

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from fabulous.fabric_definition.bel import Bel
    from fabulous.fabric_definition.port import TilePort
    from fabulous.fabric_definition.wire import JumpWire


def switch_matrix_ports(
    ports: Iterable[TilePort],
    bels: Iterable[Bel],
    jump_wires: Iterable[JumpWire] = (),
    prefix: str = "",
) -> tuple[SwitchMatrixPort, ...]:
    """Return the ports of a switch-matrix module in canonical order.

    The order is what the matrix uses for its mux outputs and mux inputs:
    tile wire ports first (in tile port order), then BEL ports, then the jump
    wires' ports, then the constant sources. It depends only on the tile's
    ports, BELs and jump wires, so a `.list` matrix can be read straight into
    this canonical order without a CSV round trip.

    Parameters
    ----------
    ports : Iterable[TilePort]
        The tile's ports (`tile.portsInfo`).
    bels : Iterable[Bel]
        The tile's BELs (`tile.bels`).
    jump_wires : Iterable[JumpWire], optional
        The tile's jump wires (`tile.jump_wires`). Defaults to none.
    prefix : str, optional
        Prefix for the wire-port pin names (a supertile matrix prefixes each
        child tile's pins with the tile name). Defaults to "".

    Returns
    -------
    tuple[SwitchMatrixPort, ...]
        The matrix ports.
    """
    result: list[SwitchMatrixPort] = []
    for port in ports:
        if port.sm_pins:
            result.append(SwitchMatrixPort.from_tile_port(port, prefix))
    for bel in bels:
        bel_ports = (
            bel.get_ports(BelPortKind.INTERNAL, IO.INPUT)
            + bel.get_ports(BelPortKind.INTERNAL, IO.OUTPUT)
            + bel.get_ports(BelPortKind.EXTERNAL, IO.OUTPUT)
        )
        result.extend(SwitchMatrixPort.from_bel_port(p) for p in bel_ports)
    for wire in jump_wires:
        result.extend(end for end in (wire.source, wire.destination) if end)
    # A constant may already be declared by a source-less jump wire.
    declared = {pin.name() for port in result for pin in port.pins}
    for const in SWITCH_MATRIX_CONSTANTS:
        if const not in declared:
            result.append(SwitchMatrixPort(const, IO.INPUT, literal=True))
    return tuple(result)


@dataclass(frozen=True)
class SwitchMatrix:
    """Encapsulates a tile's switch matrix: its ports and their connectivity.

    Read once and immutable: the connectivity is fixed at construction, so the
    same object can be safely shared or deep-copied across fabric-grid placements.

    Attributes
    ----------
    matrix_file : Path
        Source file for the switch matrix (`.csv`, `.list`, or hand-written
        HDL).
    ports : tuple[SwitchMatrixPort, ...]
        The matrix module's ports in canonical order (`switch_matrix_ports`).
        Empty for hand-written HDL.
    connections : dict[Pin, tuple[Pin, ...]]
        Mux output pin -> its mux input pins. Every mux output pin has an
        entry, possibly empty. Empty for hand-written HDL.
    preserve_list_order : bool
        Whether the mux-input order is significant (MSB-first `.list` order)
        rather than the canonical input order. Recorded once at read time
        and reused when exporting so a round trip is faithful. Default False.
    hdl_config_bits : int | None
        Config-bit count declared by a hand-written HDL matrix. None for parsed
        matrices, whose `no_config_bits` is derived from `connections` instead.
    """

    matrix_file: Path
    ports: tuple[SwitchMatrixPort, ...]
    connections: dict[Pin, tuple[Pin, ...]]
    preserve_list_order: bool = False
    hdl_config_bits: int | None = None

    @property
    def named_connections(self) -> dict[str, list[str]]:
        """`connections` by HDL wire name: mux output name -> mux input names."""
        return {
            out.name(): [pin.name() for pin in ins]
            for out, ins in self.connections.items()
        }

    @property
    def mux_outputs(self) -> tuple[Pin, ...]:
        """The mux output pins (the matrix's OUTPUT pins) in canonical order."""
        return tuple(pin for port in self.ports if port.is_output for pin in port.pins)

    @property
    def mux_inputs(self) -> tuple[Pin, ...]:
        """The mux input pins (the matrix's INPUT pins) in canonical order."""
        return tuple(pin for port in self.ports if port.is_input for pin in port.pins)

    @property
    def no_config_bits(self) -> int:
        """Number of configuration bits required by this switch matrix.

        Derived on demand from `connections` (so it tracks any change to them);
        a hand-written HDL matrix has no parsed connections and instead reports
        the count declared in its header (`hdl_config_bits`).

        Returns
        -------
        int
            Total configuration bits across all muxes.
        """
        if self.hdl_config_bits is not None:
            return self.hdl_config_bits
        return self._count_config_bits(self.connections)

    @classmethod
    def from_file(
        cls,
        path: Path,
        tile_name: str,
        ports: Iterable[SwitchMatrixPort],
        preserve_list_order: bool = False,
        canonical: bool = True,
    ) -> SwitchMatrix:
        """Construct a SwitchMatrix by parsing the given source file.

        A `.list` is read once into its canonical form: the mux outputs
        follow the canonical order of `ports`, and each mux's inputs follow the
        canonical input order (or the reversed `.list` order when
        `preserve_list_order`). A `.csv` is authored in its final order and
        kept as is. Every name in the file is resolved to a pin of `ports`; an
        unknown name raises. Hand-written HDL (`.v`/`.sv`/`.vhdl`/
        `.vhd`) is an escape hatch: only its `NumberOfConfigBits` is read and
        the ports and connectivity are left empty.

        Parameters
        ----------
        path : Path
            Path to the switch matrix file. Supported extensions: `.csv`,
            `.list`, `.v`, `.sv`, `.vhdl`, `.vhd`.
        tile_name : str
            Tile name, used only in the hand-written-HDL warning message.
        ports : Iterable[SwitchMatrixPort]
            The matrix ports in canonical order (`switch_matrix_ports`).
        preserve_list_order : bool, optional
            When True, a `.list`'s mux inputs keep the file order (reversed,
            MSB-first) instead of the canonical input order. Defaults to
            False.
        canonical : bool, optional
            Canonicalise a `.list` (see above). When False the `.list` is
            kept in file order like a `.csv`. Defaults to True.

        Returns
        -------
        SwitchMatrix
            Fully initialised switch matrix instance.

        Raises
        ------
        InvalidFileType
            If the file extension is not recognised.
        """
        # Local import keeps fabric_definition free of a module-level dependency
        # on fabric_generator (the same layering pattern Tile uses for its GDS
        # import); the parser itself only depends on custom_exception.
        from fabulous.fabric_generator.parser.parse_switchmatrix import (
            parseList,
            parseMatrix,
        )

        match path.suffix:
            case ".csv":
                # A .csv is authored in its final order: rows are the mux
                # outputs, cell values the per-mux input order.
                raw = parseMatrix(path, preserve_list_order)
                return cls.from_names(path, tuple(ports), raw, preserve_list_order)
            case ".list":
                raw = parseList(path, "source")
                if preserve_list_order:
                    raw = {k: list(reversed(v)) for k, v in raw.items()}
                return cls.from_names(
                    path, tuple(ports), raw, preserve_list_order, canonical
                )
            case ".v" | ".sv" | ".vhdl" | ".vhd":
                logger.warning(
                    f"Switch matrix for tile {tile_name!r} is read from HDL "
                    f"{path.name}: only NumberOfConfigBits is extracted - the "
                    "connectivity is NOT parsed. This tile therefore contributes "
                    "no tile-internal pips to the nextpnr model and no switch-"
                    "matrix bit mapping to the bitstream (its config bits are "
                    "still reserved). nextpnr cannot route through it; you are "
                    "responsible for ensuring the HDL matches the fabric's ports."
                )
                return cls(
                    matrix_file=path,
                    ports=(),
                    connections={},
                    preserve_list_order=preserve_list_order,
                    hdl_config_bits=cls._extract_config_bits_from_hdl(path),
                )
            case _:
                raise InvalidFileType(
                    f"Unrecognised switch matrix file extension: {path.suffix}"
                )

    @classmethod
    def from_names(
        cls,
        path: Path,
        ports: tuple[SwitchMatrixPort, ...],
        raw: dict[str, list[str]],
        preserve_list_order: bool = False,
        canonical: bool = False,
    ) -> SwitchMatrix:
        """Build a matrix from name-level connectivity, resolved against `ports`.

        Parameters
        ----------
        path : Path
            The matrix file the names came from (kept as `matrix_file`).
        ports : tuple[SwitchMatrixPort, ...]
            The matrix ports in canonical order.
        raw : dict[str, list[str]]
            Mux output name -> mux input names, as read from the file. A `.csv`
            already carries each mux's input order; a `.list` is in file order.
        preserve_list_order : bool, optional
            Recorded on the matrix; with `canonical` it also keeps `raw`'s
            per-mux input order instead of the canonical input order.
            Defaults to False.
        canonical : bool, optional
            Re-order into canonical form: every mux output of `ports` gets an
            entry (possibly empty) in port order, and each mux's inputs follow
            the canonical input order unless `preserve_list_order`. When False,
            `raw`'s own order is kept as authored. Defaults to False.

        Returns
        -------
        SwitchMatrix
            The matrix.

        Raises
        ------
        InvalidSwitchMatrixDefinition
            If a name is not a pin of `ports`.
        """
        matrix = cls(path, ports, {}, preserve_list_order)
        outputs = {pin.name(): pin for pin in matrix.mux_outputs}
        inputs = {pin.name(): pin for pin in matrix.mux_inputs}
        input_index = {pin: i for i, pin in enumerate(matrix.mux_inputs)}

        resolved: dict[Pin, list[Pin]] = {}
        for out_name, in_names in raw.items():
            if out_name not in outputs:
                raise InvalidSwitchMatrixDefinition(
                    f"Switch matrix output {out_name!r} in {path.name} is not a "
                    f"signal of the tile.\nAvailable outputs: {sorted(outputs)}"
                )
            ins = []
            for in_name in in_names:
                if in_name not in inputs:
                    raise InvalidSwitchMatrixDefinition(
                        f"Switch matrix input {in_name!r} (driving {out_name!r}) in "
                        f"{path.name} is not a signal of the tile.\n"
                        f"Available inputs: {sorted(inputs)}"
                    )
                ins.append(inputs[in_name])
            resolved[outputs[out_name]] = ins

        if not canonical:
            connections = {out: tuple(ins) for out, ins in resolved.items()}
            return cls(path, ports, connections, preserve_list_order)

        # Unconnected outputs keep an empty entry so generation's
        # "not connected to anything" check still fires.
        connections = {}
        for out in matrix.mux_outputs:
            ins = resolved.get(out, [])
            if not preserve_list_order:
                ins = sorted(ins, key=lambda pin: input_index[pin])
            connections[out] = tuple(ins)
        return cls(path, ports, connections, preserve_list_order)

    def to_csv_file(self, path: Path, tile_name: str) -> None:
        """Write the switch matrix connections to a `.csv` file.

        See `write_matrix_csv` for the format.

        Parameters
        ----------
        path : Path
            Destination `.csv` file. Created (or overwritten) by this call.
        tile_name : str
            Tile name written to the top-left cell of the CSV header.
        """
        from fabulous.fabric_generator.parser.parse_switchmatrix import (
            write_matrix_csv,
        )

        write_matrix_csv(self.named_connections, path, tile_name)

    def to_list_file(self, path: Path) -> None:
        """Write the switch matrix connections to a `.list` file.

        See `write_list` for the format.

        Parameters
        ----------
        path : Path
            Destination `.list` file. Created (or overwritten) by this call.
        """
        from fabulous.fabric_generator.parser.parse_switchmatrix import write_list

        write_list(self.named_connections, path)

    @staticmethod
    def _count_config_bits(connections: dict[Pin, tuple[Pin, ...]]) -> int:
        """Count config bits needed to select each mux's inputs.

        Parameters
        ----------
        connections : dict[Pin, tuple[Pin, ...]]
            Mux output -> mux inputs.

        Returns
        -------
        int
            Total select bits summed over every mux (a mux with fewer than two
            inputs needs none).
        """
        total = 0
        for sources in connections.values():
            if len(sources) >= 2:
                total += (len(sources) - 1).bit_length()
        return total

    @staticmethod
    def _extract_config_bits_from_hdl(path: Path) -> int:
        """Read `NumberOfConfigBits` out of a hand-written HDL matrix.

        Parameters
        ----------
        path : Path
            The `.v`/`.sv`/`.vhdl`/`.vhd` switch matrix file.

        Returns
        -------
        int
            The declared config-bit count, or 0 if none is found (a warning is
            logged in that case).
        """
        content = path.read_text(encoding="utf-8")
        if m := re.search(r"NumberOfConfigBits:\s*(\d+)", content):
            return int(m.group(1))
        logger.warning(
            f"Cannot find NumberOfConfigBits in {path}, assuming 0 config bits."
        )
        return 0
