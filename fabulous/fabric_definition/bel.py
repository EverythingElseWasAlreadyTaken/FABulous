"""Basic Element of Logic (BEL) definition module.

This module contains the `Bel` class which represents a Basic Element of Logic in the
FPGA fabric.
BELs are the fundamental building blocks that can be placed and configured within tiles,
such as LUTs, flip-flops, and other logic elements.
"""

from dataclasses import dataclass
from pathlib import Path

from fabulous.fabric_definition.define import IO, BelPortKind, HDLType
from fabulous.fabric_definition.port import BelConfigPort, BelPort


@dataclass
class Bel:
    """Information about a single BEL.

    The information is parsed from the directory of the BEL in the CSV definition file.
    The BEL owns one `BelPort` per port of its HDL module (`ports`); every
    other port view (`inputs`, `sharedPort`, `ports_vectors`, ...) is derived
    from them. There are some things to be noted:

    - The parsed name will contain the prefix of the bel.
    - A shared port is not BEL-prefixed.
    - If a port is marked as both shared and external, the port is considered as shared,
      as a result, signals like UserCLK will be in the shared port list,
      but not in the external port list.

    Parameters
    ----------
    src : Path
        The source directory path of the BEL.
    prefix : str
        The prefix of the BEL.
    module_name : str
        The name of the module in the BEL.
    ports : list[BelPort]
        The ports of the BEL's HDL module, in module order, except the
        configuration port. Each port is attached to this BEL.
    config_port : BelConfigPort | None
        The configuration port with the BEL's feature map, or None for a BEL
        without configuration bits. It is attached to this BEL.

    Attributes
    ----------
    src : Path
        The source directory of the BEL given in the CSV file.
    prefix : str
        The prefix of the BEL given in the CSV file.
    name : str
        The name of the BEL, extracted from the source directory.
    module_name : str
        The name of the module in the bel.
        For verlog we can extract this from the RTL.
        For VHDL this is currently the same as name.
    filetype : HDLType
        The file type of the BEL.
    ports : list[BelPort]
        The ports of the BEL's HDL module, in module order, except the
        configuration port.
    config_port : BelConfigPort | None
        The configuration port, or None.
    language : str
        Language of the BEL. Currently only VHDL and Verilog are supported.

    Raises
    ------
    ValueError
        If the file type is not recognized (not .sv, .v, .vhd, or .vhdl).
    """

    src: Path
    prefix: str
    name: str
    module_name: str
    filetype: HDLType
    ports: list[BelPort]
    config_port: BelConfigPort | None
    language: str

    def __init__(
        self,
        src: Path,
        prefix: str,
        module_name: str,
        ports: list[BelPort],
        config_port: BelConfigPort | None = None,
    ) -> None:
        self.src = src
        self.prefix = prefix
        self.name = src.stem
        self.module_name = module_name
        self.ports = ports
        for port in ports:
            port.bel = self
        self.config_port = config_port
        if config_port is not None:
            config_port.bel = self
        if self.src.suffix in [".sv", ".v"]:
            self.language = "verilog"
            self.filetype = HDLType.VERILOG
        elif self.src.suffix in [".vhd", ".vhdl"]:
            self.language = "vhdl"
            self.filetype = HDLType.VHDL
        else:
            raise ValueError(f"Unknown file type {self.src.suffix} for BEL {self.src}")

    @property
    def configBit(self) -> int:
        """The number of configuration bits of the BEL."""
        return 0 if self.config_port is None else self.config_port.width

    @property
    def belFeatureMap(self) -> dict[str, dict]:
        """The feature map of the BEL, in bit order."""
        return {} if self.config_port is None else self.config_port.bel_map

    def get_ports(self, kind: BelPortKind, io: IO) -> list[BelPort]:
        """Return the ports of one kind and direction, in module order.

        Parameters
        ----------
        kind : BelPortKind
            The port kind.
        io : IO
            The port direction.

        Returns
        -------
        list[BelPort]
            The matching ports.
        """
        return [p for p in self.ports if p.kind == kind and p.io_direction == io]

    def _pin_names(self, kind: BelPortKind, io: IO) -> list[str]:
        return [pin.name() for port in self.get_ports(kind, io) for pin in port.pins]

    def _named_pins(self, kind: BelPortKind) -> list[tuple[str, IO]]:
        return [
            (pin.name(), port.io_direction)
            for port in self.ports
            if port.kind == kind
            for pin in port.pins
        ]

    @property
    def inputs(self) -> list[str]:
        """All the normal input pins of the BEL."""
        return self._pin_names(BelPortKind.INTERNAL, IO.INPUT)

    @property
    def outputs(self) -> list[str]:
        """All the normal output pins of the BEL."""
        return self._pin_names(BelPortKind.INTERNAL, IO.OUTPUT)

    @property
    def externalInput(self) -> list[str]:
        """All the external input pins of the BEL."""
        return self._pin_names(BelPortKind.EXTERNAL, IO.INPUT)

    @property
    def externalOutput(self) -> list[str]:
        """All the external output pins of the BEL."""
        return self._pin_names(BelPortKind.EXTERNAL, IO.OUTPUT)

    @property
    def sharedPort(self) -> list[tuple[str, IO]]:
        """All the shared pins of the BEL with their direction."""
        return self._named_pins(BelPortKind.SHARED)

    @property
    def withUserCLK(self) -> bool:
        """Whether the BEL has a user clock port."""
        return any(port.is_clock for port in self.ports)

    @property
    def ports_vectors(self) -> dict[str, dict[str, tuple[IO, int]]]:
        """The ports by kind, then unprefixed name: `(IO, width)`.

        `{<porttype>: {<portname>: (IO, <portwidth>)}}`
        """
        vectors: dict[str, dict[str, tuple[IO, int]]] = {
            k.value: {} for k in BelPortKind
        }
        for port in self.ports:
            vectors[port.kind.value][port.base_name] = (port.io_direction, port.width)
        return vectors

    @property
    def carry(self) -> dict[str, dict[IO, str]]:
        """Carry chains by name: `{carry_name: {direction: port_name}}`."""
        chains: dict[str, dict[IO, str]] = {}
        for port in self.ports:
            if port.carry is not None:
                chains.setdefault(port.carry, {})[port.io_direction] = port.name
        return chains

    @property
    def localShared(self) -> dict[str, tuple[str, IO]]:
        """Local shared ports: `{RESET/ENABLE: (pin_name, IO)}`.

        They are only shared in the tile, not in the fabric. A multi-bit port
        is represented by its last bit.
        """
        return {
            port.local_shared: (port.pins[-1].name(), port.io_direction)
            for port in self.ports
            if port.local_shared is not None
        }
