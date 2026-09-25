"""Switch matrix generation module for FABulous FPGA tiles.

This module generates RTL code for configurable switch matrices within FPGA tiles.
Switch matrices handle the routing of signals between tile ports, BEL inputs/outputs,
and jump wires. The module supports various configuration modes and multiplexer styles.

Key features:
- CSV and list file parsing for switch matrix configurations
- Support for custom and generic multiplexer implementations
- Configuration bit calculation and management
- Debug signal generation for switch matrix analysis
- Multiple configuration modes (FlipFlop chain, Frame-based)
"""

import math

from loguru import logger

from fabulous.fabric_definition.define import (
    IO,
    SWITCH_MATRIX_CONSTANTS,
    ConfigBitMode,
    MultiplexerStyle,
)
from fabulous.fabric_definition.port import (
    BelPort,
    Port,
    SJumpPort,
    SwitchMatrixPort,
    TilePort,
)
from fabulous.fabric_definition.supertile import SuperTile
from fabulous.fabric_definition.tile import Tile
from fabulous.fabric_generator.code_generator.code_generator import CodeGenerator
from fabulous.fabric_generator.code_generator.code_generator_VHDL import (
    VHDLCodeGenerator,
)


def _unconnected_port_diagnostic(ports: list[Port], port_name: str) -> str:
    """Explain an unconnected switch matrix port caused by NULL-wire expansion.

    A NULL-terminated spanning wire expands to `wires x distance` nested
    wires (see `Port.expand_port_info_by_name`). When the switch matrix leaves some
    of those nested wires unconnected, the bare wire name is unhelpful, so this
    traces the wire back to its originating port and explains the expansion.

    Parameters
    ----------
    ports : list[Port]
        The ports of the tile whose switch matrix is being generated.
    port_name : str
        The expanded wire name that has no connections.

    Returns
    -------
    str
        A diagnostic message to append to the base error, or an empty string
        when `port_name` is not a nested wire of a NULL-terminated spanning
        wire.
    """
    for port in ports:
        expanded = port.expand_port_info_by_name()
        if port_name not in expanded:
            continue
        distance = abs(port.x_offset) + abs(port.y_offset)
        if not (port.is_null_terminated and distance > 1):
            return ""
        return (
            f"\n  '{port_name}' is one of {len(expanded)} nested wires expanded "
            f"from wire spec '{port.name}' (wires={port.wire_count}, "
            f"distance={distance}). A NULL-terminated wire connects all nested "
            f"wires: wires x distance = {port.wire_count} x {distance} = "
            f"{len(expanded)} ({expanded[0]}..{expanded[-1]}). The switch matrix "
            f"connects fewer than {len(expanded)} of them. Either connect all "
            f"{len(expanded)} nested wires, or name both ends of the wire "
            f"(instead of NULL) for a direct {port.wire_count}-wire "
            "point-to-point bus."
        )
    return ""


def _ports_from(
    ports: tuple[SwitchMatrixPort, ...], origin: type, io: IO
) -> list[SwitchMatrixPort]:
    """Return the matrix ports of one origin type and direction, in port order.

    The origin type is matched exactly: an `SJumpPort` is a `TilePort`, but
    its matrix ports are grouped apart from the routing wires'.

    Parameters
    ----------
    ports : tuple[SwitchMatrixPort, ...]
        The matrix ports.
    origin : type
        The type of the port each matrix port wires to.
    io : IO
        The direction as seen from the switch matrix.

    Returns
    -------
    list[SwitchMatrixPort]
        The matching ports.
    """
    return [p for p in ports if type(p.origin) is origin and p.io_direction == io]


def _add_port_pins(writer: CodeGenerator, ports: list[SwitchMatrixPort]) -> None:
    """Declare every pin of the given matrix ports as a scalar module port.

    The switch matrix module has one scalar port per pin, named the flat way
    (`N1END0`), in the order of `ports` and least significant bit first.

    Parameters
    ----------
    writer : CodeGenerator
        The code generator writing the switch matrix module.
    ports : list[SwitchMatrixPort]
        The matrix ports to declare; each keeps its own direction.
    """
    for port in ports:
        for pin in port.pins:
            writer.addPortScalar(pin.name(), port.io_direction, indentLevel=2)


def genTileSwitchMatrix(
    writer: CodeGenerator,
    tile: Tile,
    switch_matrix_debug_signal: bool,
    config_bit_mode: ConfigBitMode = ConfigBitMode.FRAME_BASED,
    multiplexer_style: MultiplexerStyle = MultiplexerStyle.CUSTOM,
    default_pip_delay: int = 80,
) -> None:
    """Generate the RTL code for the tile switch matrix.

    The switch matrix is read straight from the tile's already-canonical
    `tile.switch_matrix.connections` (built once when the fabric was parsed);
    no CSV is written or re-read here. A tile whose matrix is hand-written HDL
    is skipped - it supplies its own switch matrix module.

    Parameters
    ----------
    writer : CodeGenerator
        The code generator instance for RTL output
    tile : Tile
        The tile object containing BELs and port information
    switch_matrix_debug_signal : bool
        Whether to generate debug signals for the switch matrix.
    config_bit_mode : ConfigBitMode
        The configuration-bit mode for the tile (frame-based or flip-flop chain).
    multiplexer_style : MultiplexerStyle
        The multiplexer style used to implement switch-matrix muxes.
    default_pip_delay : int
        Per-mux delay (ps) emitted on assign statements in the switch matrix.

    Raises
    ------
    ValueError
        If any port in the switch matrix is not connected to anything.
    """
    if tile.switch_matrix.matrix_file.suffix in (".v", ".sv", ".vhdl", ".vhd"):
        logger.info(
            f"{tile.name} provides a hand-written switch matrix HDL; "
            "skipping matrix generation."
        )
        return

    # Unconnected outputs are checked here (not at parse) because tile ports are
    # only final after fabric assembly; the switch matrix connections are read
    # once but the port set backing the diagnostic changes.
    connections = tile.switch_matrix.named_connections
    for port_name in connections:
        if not connections[port_name]:
            hint = _unconnected_port_diagnostic(tile.portsInfo, port_name)
            raise ValueError(f"{port_name} not connected to anything!{hint}")
    noConfigBits = tile.switch_matrix.no_config_bits

    # we pass the NumberOfConfigBits as a comment in the beginning of the file.
    # This simplifies it to generate the configuration port only if needed later when
    # building the fabric where we are only working with the VHDL files

    # Generate header
    writer.addComment(f"NumberOfConfigBits: {noConfigBits}")
    writer.addHeader(f"{tile.name}_switch_matrix")
    if noConfigBits > 0:
        writer.addParameterStart(indentLevel=1)
        writer.addParameter("NoConfigBits", "integer", noConfigBits, indentLevel=2)
        writer.addParameterEnd(indentLevel=1)
    writer.addPortStart(indentLevel=1)

    # The module's ports are the matrix's own ports. A source-less jump names a
    # constant (declared in the body), not a port, and neither is a literal
    # constant; the jump ports that remain are the matrix ports of the wires.
    sm_ports = tile.switch_matrix.ports
    jump_ports = {
        end
        for wire in tile.jump_wires
        if wire.source is not None
        for end in (wire.source, wire.destination)
        if end is not None
    }
    jump = [p for p in sm_ports if p in jump_ports]
    for ports in (
        _ports_from(sm_ports, TilePort, IO.INPUT),
        _ports_from(sm_ports, BelPort, IO.INPUT),
        [p for p in jump if p.is_input],
        _ports_from(sm_ports, TilePort, IO.OUTPUT),
        _ports_from(sm_ports, BelPort, IO.OUTPUT),
        [p for p in jump if p.is_output],
        # SJUMP: the matrix drives signals out to the supertile matrix and
        # receives signals back from it
        _ports_from(sm_ports, SJumpPort, IO.OUTPUT),
        _ports_from(sm_ports, SJumpPort, IO.INPUT),
    ):
        _add_port_pins(writer, ports)

    writer.addComment("global", onNewLine=True)
    if noConfigBits > 0:
        if config_bit_mode == ConfigBitMode.FLIPFLOP_CHAIN:
            writer.addPortScalar("MODE", IO.INPUT, indentLevel=2)
            writer.addComment("global signal 1: configuration, 0: operation")
            writer.addPortScalar("CONFin", IO.INPUT, indentLevel=2)
            writer.addPortScalar("CONFout", IO.OUTPUT, indentLevel=2)
            writer.addPortScalar("CLK", IO.INPUT, indentLevel=2)
        if config_bit_mode == ConfigBitMode.FRAME_BASED:
            writer.addPortVector(
                "ConfigBits", IO.INPUT, "NoConfigBits-1", indentLevel=2
            )
            writer.addPortVector(
                "ConfigBits_N", IO.INPUT, "NoConfigBits-1", indentLevel=2
            )
    writer.addPortEnd()
    writer.addHeaderEnd(f"{tile.name}_switch_matrix")
    writer.addDesignDescriptionStart(f"{tile.name}_switch_matrix")
    _gen_switch_matrix_body(
        writer,
        tile.name,
        connections,
        noConfigBits,
        config_bit_mode,
        multiplexer_style,
        default_pip_delay,
        switch_matrix_debug_signal,
    )


def _gen_switch_matrix_body(
    writer: CodeGenerator,
    name: str,
    connections: dict[str, list[str]],
    noConfigBits: int,
    config_bit_mode: ConfigBitMode,
    multiplexer_style: MultiplexerStyle,
    default_pip_delay: int,
    switch_matrix_debug_signal: bool,
) -> None:
    """Emit the body of a switch matrix module (constants, signals, mux logic).

    Called after the port list has been written. Handles constant declarations,
    signal declarations, mux instantiation, optional debug signals, and the
    closing `addDesignDescriptionEnd` / `writeToFile` calls.

    Parameters
    ----------
    writer : CodeGenerator
        Code generator instance for RTL output.
    name : str
        Module/tile name used in log messages.
    connections : dict[str, list[str]]
        Mapping from sink port name to list of source port names.
    noConfigBits : int
        Total number of configuration bits for this matrix.
    config_bit_mode : ConfigBitMode
        Frame-based or flip-flop chain configuration.
    multiplexer_style : MultiplexerStyle
        Custom or generic multiplexer implementation.
    default_pip_delay : int
        Per-mux delay (ps) emitted on assign statements.
    switch_matrix_debug_signal : bool
        Whether to generate debug signals.
    """
    # constant declaration - provides '0'/'1' as padding inputs to muxes
    vhdl = isinstance(writer, VHDLCodeGenerator)
    for const in SWITCH_MATRIX_CONSTANTS:
        if const.startswith("GND"):
            writer.addConstant(const, "0" if vhdl else "1'b0")
        else:
            writer.addConstant(const, "1" if vhdl else "1'b1")
    writer.addNewLine()

    # signal declaration - one input-concat vector per multi-input mux
    for portName in connections:
        if len(connections[portName]) > 1:
            writer.addConnectionVector(
                f"{portName}_input", f"{len(connections[portName])}-1"
            )

    ### SwitchMatrixDebugSignals ### SwitchMatrixDebugSignals ###
    if switch_matrix_debug_signal:
        writer.addNewLine()
        for portName in connections:
            muxSize = len(connections[portName])
            if muxSize >= 2:
                paddedMuxSize = 2 ** (muxSize - 1).bit_length() - 1
                writer.addConnectionVector(
                    f"DEBUG_select_{portName}",
                    f"{paddedMuxSize.bit_length() - 1}",
                )
    writer.addComment(
        "The configuration bits (if any) are just a long shift register",
        onNewLine=True,
    )
    writer.addComment(
        "This shift register is padded to an even number of flops/latches",
        onNewLine=True,
    )

    if noConfigBits > 0:
        if config_bit_mode == "ff_chain":
            writer.addConnectionVector("ConfigBits", noConfigBits)
        if config_bit_mode == "FlipFlopChain":
            writer.addConnectionVector(
                "ConfigBits", int(math.ceil(noConfigBits / 2.0)) * 2
            )
            writer.addConnectionVector(
                "ConfigBitsInput", int(math.ceil(noConfigBits / 2.0)) * 2
            )

    writer.addLogicStart()

    # TODO Should ff_chain be the same as FlipFlopChain?
    if noConfigBits > 0:
        if config_bit_mode == "ff_chain":
            writer.addShiftRegister(noConfigBits)
        elif config_bit_mode == ConfigBitMode.FLIPFLOP_CHAIN:
            writer.addFlipFlopChain(noConfigBits)
        elif config_bit_mode == ConfigBitMode.FRAME_BASED:
            pass

    # the switch matrix implementation
    # we use the following variable to count the configuration bits of a
    # long shift register which actually holds the switch matrix configuration
    configBitstreamPosition = 0
    for portName in connections:
        muxSize = len(connections[portName])
        writer.addComment(
            f"switch matrix multiplexer {portName} MUX-{muxSize}", onNewLine=True
        )
        if muxSize == 0:
            logger.warning(
                f"Input port {portName} of switch matrix in {name} is unused"
            )
            writer.addComment(
                f"WARNING unused multiplexer MUX-{portName}", onNewLine=True
            )
        elif muxSize == 1:
            if connections[portName][0] == "0":
                writer.addAssignScalar(portName, 0)
            elif connections[portName][0] == "1":
                writer.addAssignScalar(portName, 1)
            else:
                writer.addAssignScalar(
                    portName,
                    connections[portName][0],
                    delay=default_pip_delay,
                )
            writer.addNewLine()
        elif muxSize >= 2:
            paddedMuxSize = 2 ** (muxSize - 1).bit_length()
            muxComponentName = f"cus_mux{paddedMuxSize}1"

            portsPairs = []
            start = 0
            for start in range(muxSize):
                portsPairs.append((f"A{start}", f"{portName}_input[{start}]"))
            for end in range(start + 1, paddedMuxSize):
                portsPairs.append((f"A{end}", "GND0"))

            if multiplexer_style == MultiplexerStyle.CUSTOM:
                if paddedMuxSize == 2:
                    portsPairs.append(("S", f"ConfigBits[{configBitstreamPosition}+0]"))
                else:
                    for i in range(paddedMuxSize.bit_length() - 1):
                        portsPairs.append(
                            (f"S{i}", f"ConfigBits[{configBitstreamPosition}+{i}]")
                        )
                        portsPairs.append(
                            (
                                f"S{i}N",
                                f"ConfigBits_N[{configBitstreamPosition}+{i}]",
                            )
                        )

            portsPairs.append(("X", f"{portName}"))

            # Drive the mux input vector for both mux styles.
            writer.addAssignScalar(
                f"{portName}_input",
                connections[portName][::-1],
                delay=default_pip_delay,
            )

            if multiplexer_style == MultiplexerStyle.CUSTOM:
                writer.addInstantiation(
                    compName=muxComponentName,
                    compInsName=f"inst_{muxComponentName}_{portName}",
                    portsPairs=portsPairs,
                )
                if muxSize not in (2, 4, 8, 16):
                    logger.warning(
                        f"creating a MUX-{muxSize} for port {portName} using "
                        f"MUX-{muxSize} in switch matrix for {name}"
                    )
            else:
                # generic multiplexer: select the input behaviorally so it
                # synthesises to standard cells. The writer emits the indexing
                # in language-correct syntax for Verilog and VHDL.
                select_width = paddedMuxSize.bit_length() - 1
                writer.addMuxAssign(
                    portName,
                    f"{portName}_input",
                    "ConfigBits",
                    configBitstreamPosition,
                    select_width,
                    delay=default_pip_delay,
                )

            configBitstreamPosition += paddedMuxSize.bit_length() - 1

    if switch_matrix_debug_signal:
        logger.info(f"Generate debug signals for switch matrix in {name}")
        writer.addNewLine()
        configBitstreamPosition = 0
        old_ConfigBitstreamPosition = 0
        for portName in connections:
            muxSize = len(connections[portName])
            if muxSize >= 2:
                paddedMuxSize = 2 ** (muxSize - 1).bit_length()
                configBitstreamPosition += paddedMuxSize.bit_length() - 1
                writer.addAssignVector(
                    f"DEBUG_select_{portName:<15}",
                    "ConfigBits",
                    f"{configBitstreamPosition - 1}",
                    old_ConfigBitstreamPosition,
                )
                old_ConfigBitstreamPosition = configBitstreamPosition
    ### SwitchMatrixDebugSignals ### SwitchMatrixDebugSignals ###

    writer.addDesignDescriptionEnd()
    writer.writeToFile()


def gen_super_tile_switch_matrix(
    writer: CodeGenerator,
    superTile: SuperTile,
    config_bit_mode: ConfigBitMode = ConfigBitMode.FRAME_BASED,
    multiplexer_style: MultiplexerStyle = MultiplexerStyle.CUSTOM,
    default_pip_delay: int = 80,
) -> None:
    """Generate the switch matrix RTL for a supertile.

    The supertile switch matrix routes SJUMP output signals from child tiles to
    the input ports of supertile-level BELs. Its connectivity is described by
    `superTile.supertile_matrix_dir` (a `.list` or `.csv` file using the same
    format as tile switch matrices).

    Parameters
    ----------
    writer : CodeGenerator
        Code generator instance for RTL output.
    superTile : SuperTile
        The supertile whose BELs and SJUMP ports drive this matrix.
    config_bit_mode : ConfigBitMode
        Frame-based or flipflop-chain configuration.
    multiplexer_style : MultiplexerStyle
        Custom or generic multiplexer implementation.
    default_pip_delay : int
        Default PIP delay value for timing annotation.
    """
    if superTile.switch_matrix is None:
        return

    noConfigBits = superTile.switch_matrix.no_config_bits
    module_name = f"{superTile.name}_switch_matrix"

    # Connectivity (destination -> [sources]) held on the supertile.
    connections = superTile.switch_matrix.named_connections

    writer.addComment(f"NumberOfConfigBits: {noConfigBits}")
    writer.addHeader(module_name)
    if noConfigBits > 0:
        writer.addParameterStart(indentLevel=1)
        writer.addParameter("NoConfigBits", "integer", noConfigBits, indentLevel=2)
        writer.addParameterEnd(indentLevel=1)
    writer.addPortStart(indentLevel=1)

    sm_ports = superTile.switch_matrix.ports
    forward = _ports_from(sm_ports, SJumpPort, IO.INPUT)
    bel_inputs = _ports_from(sm_ports, BelPort, IO.OUTPUT)
    bel_outputs = _ports_from(sm_ports, BelPort, IO.INPUT)
    reverse = _ports_from(sm_ports, SJumpPort, IO.OUTPUT)

    # Inputs: SJUMP OUTPUT signals from each child tile ({tileName}_{portName}{i})
    if forward:
        writer.addComment("SJUMP inputs from child tiles", onNewLine=True)
    _add_port_pins(writer, forward)

    # Outputs: input ports of supertile BELs (SM drives BEL inputs)
    if superTile.bels:
        writer.addComment("BEL input ports (SM outputs)", onNewLine=True)
    _add_port_pins(writer, bel_inputs)

    # Inputs: output ports of supertile BELs (SM routes them back to child tiles)
    if bel_outputs:
        writer.addComment("BEL output ports (SM inputs)", onNewLine=True)
    _add_port_pins(writer, bel_outputs)

    # Outputs: reverse SJUMP signals driven back into child tiles
    if reverse:
        writer.addComment("Reverse SJUMP outputs (SM -> child tile)", onNewLine=True)
    _add_port_pins(writer, reverse)

    writer.addComment("global", onNewLine=True)
    if noConfigBits > 0:
        if config_bit_mode == ConfigBitMode.FLIPFLOP_CHAIN:
            writer.addPortScalar("MODE", IO.INPUT, indentLevel=2)
            writer.addPortScalar("CONFin", IO.INPUT, indentLevel=2)
            writer.addPortScalar("CONFout", IO.OUTPUT, indentLevel=2)
            writer.addPortScalar("CLK", IO.INPUT, indentLevel=2)
        if config_bit_mode == ConfigBitMode.FRAME_BASED:
            writer.addPortVector(
                "ConfigBits", IO.INPUT, "NoConfigBits-1", indentLevel=2
            )
            writer.addPortVector(
                "ConfigBits_N", IO.INPUT, "NoConfigBits-1", indentLevel=2
            )
    writer.addPortEnd()
    writer.addHeaderEnd(module_name)
    writer.addDesignDescriptionStart(module_name)
    _gen_switch_matrix_body(
        writer,
        superTile.name,
        connections,
        noConfigBits,
        config_bit_mode,
        multiplexer_style,
        default_pip_delay,
        switch_matrix_debug_signal=False,
    )
