"""Tile generation module for FABulous FPGA architecture.

This module generates RTL code for individual tiles and super tiles within an FPGA
fabric. It handles the integration of Basic Elements of Logic (BELs), switch matrices,
and configuration infrastructure into complete tile implementations.

Key features:
- Individual tile RTL generation with BEL instantiation
- Switch matrix integration and port mapping
- Configuration data routing and management
- Supertile wrapper generation for hierarchical designs
- Support for both VHDL and Verilog code generation
- External I/O port handling and clock distribution
"""

from pathlib import Path

from fabulous.fabric_definition.bel import Bel
from fabulous.fabric_definition.cell_spec import CellSpec
from fabulous.fabric_definition.connection import (
    JumpConnection,
    StagingConnection,
    SwitchMatrixConnection,
)
from fabulous.fabric_definition.define import (
    IO,
    USER_CLK_PREDECESSOR,
    BelPortKind,
    ConfigBitMode,
    Side,
    grid_at,
)
from fabulous.fabric_definition.fabric_cell_library import (
    FabricCellFunction,
    FabricCellLibrary,
)
from fabulous.fabric_definition.supertile import SuperTile
from fabulous.fabric_definition.tile import Tile
from fabulous.fabric_generator.code_generator.code_generator import CodeGenerator
from fabulous.fabric_generator.code_generator.code_generator_Verilog import (
    VerilogCodeGenerator,
)
from fabulous.fabric_generator.code_generator.code_generator_VHDL import (
    VHDLCodeGenerator,
)
from fabulous.fabric_generator.gen_fabric.gen_switchmatrix import (
    tile_matrix_module_ports,
)


def _bel_bus_pairs(bel: Bel) -> list[tuple[str, str]]:
    """Connect each internal and external BEL port to its flat pin signals.

    A vector port is bussed MSB first (`.A({A3, A2, A1, A0})`), a one-pin
    port takes its pin signal directly.

    Parameters
    ----------
    bel : Bel
        The BEL being instantiated.

    Returns
    -------
    list[tuple[str, str]]
        `(port, signal)` pairs: internal ports, then external ports, each in
        module order.
    """
    pairs = []
    for kind in (BelPortKind.INTERNAL, BelPortKind.EXTERNAL):
        for port in bel.ports:
            if port.kind != kind:
                continue
            names = [pin.name() for pin in port.pins]
            if port.width == 1:
                pairs.append((port.base_name, names[0]))
            else:
                pairs.append((port.base_name, f"{{{', '.join(reversed(names))}}}"))
    return pairs


def _add_buffer(
    writer: CodeGenerator, cell: CellSpec, name: str, signal_in: str, signal_out: str
) -> None:
    """Instantiate the buffer `cell` from `signal_in` to `signal_out`.

    Parameters
    ----------
    writer : CodeGenerator
        The code generator.
    cell : CellSpec
        The buffer cell.
    name : str
        The instance name.
    signal_in : str
        The signal the buffer reads.
    signal_out : str
        The signal the buffer drives.
    """
    writer.addInstantiation(
        cell.cell,
        name,
        portsPairs=[
            (cell.input_ports[0], signal_in),
            (cell.output_ports[0], signal_out),
        ],
    )


def generateTile(
    writer: CodeGenerator,
    tile: Tile,
    frame_bits_per_row: int = 32,
    max_frame_per_col: int = 20,
    disable_user_clk: bool = False,
    config_bit_mode: ConfigBitMode = ConfigBitMode.FRAME_BASED,
    *,
    cells: FabricCellLibrary,
) -> None:
    """Generate the RTL code for a tile given the tile object.

    This function creates the complete RTL implementation for a tile, including:
    - Port declarations for all tile connections
    - BEL instantiations with proper port mapping
    - Switch matrix instantiation and connections
    - Configuration infrastructure (frame-based or FlipFlop chain)
    - Clock and reset signal distribution
    - Jump wire handling for long-distance connections

    We first check if we need a configuration port. Currently we assume that each
    primitive needs a configuration port. However, a switch matrix can have no switch
    matrix multiplexers (e.g., when only bouncing back in border termination tiles)

    TODO: we don't do this and always create a configuration port for each tile.
    This dangle the CLK and MODE ports hanging in the air, which will throw a warning

    Each switch matrix entity is build up is a specific order:
    1.a) interconnect wire INPUTS (in the order defined by the fabric file,)
    2.a) BEL primitive INPUTS (in the order the BEL-VHDLs are listed
         in the fabric CSV) within each BEL, the order from the entity is maintained
         Note that INPUTS refers to the view of the switch matrix!
         Which corresponds to BEL outputs at the actual BEL
    3.a) JUMP wire INPUTS (in the order defined by the fabric file)
    1.b) interconnect wire OUTPUTS
    2.b) BEL primitive OUTPUTS
         Again: OUTPUTS refers to the view of the switch matrix which corresponds to
                BEL inputs at the actual BEL
    3.b) JUMP wire OUTPUTS
    The switch matrix uses single-pin ports (std_logic and not std_logic_vector)!!!


    Parameters
    ----------
    writer : CodeGenerator
        The code generator instance for RTL output
    tile : Tile
        The tile object containing BELs and port information
    frame_bits_per_row : int
        Number of configuration bits per row for frame-based configuration
    max_frame_per_col : int
        Maximum number of frames per column for frame-based configuration
    disable_user_clk : bool
        If True, the UserCLK port will not be generated or connected
    config_bit_mode : ConfigBitMode
        The configuration bit mode to use (frame-based or FlipFlop chain)
    cells : FabricCellLibrary
        The fabric cells the buffers are instantiated from.

    Raises
    ------
    FileNotFoundError
        Only raised for VHDL output. If required component files
        (e.g., switch matrix or config memory) are missing.
    NotImplementedError
        If a staged pin passes other buffers than one at the tile entry and
        one at the exit; where buffers sit is not modelled yet.
    """
    staging = [pc for pc in tile.port_connections if isinstance(pc, StagingConnection)]
    buffer = cells.cells[FabricCellFunction.BUFFER][0]
    writer.addHeader(f"{tile.name}")
    writer.addParameterStart(indentLevel=1)
    if isinstance(writer, VerilogCodeGenerator):  # emulation only in Verilog
        maxBits = frame_bits_per_row * max_frame_per_col
        writer.addPreprocIfDef("EMULATION")
        writer.addParameter(
            "Emulate_Bitstream",
            f"[{maxBits - 1}:0]",
            f"{maxBits}'b0",
            indentLevel=2,
        )
        writer.addPreprocEndif()
    writer.addParameter("MaxFramesPerCol", "integer", max_frame_per_col, indentLevel=2)
    writer.addParameter("FrameBitsPerRow", "integer", frame_bits_per_row, indentLevel=2)
    if tile.globalConfigBits > 0:
        writer.addParameter(
            "NoConfigBits", "integer", tile.globalConfigBits, indentLevel=2
        )

    writer.addParameterEnd(indentLevel=1)
    writer.addPortStart(indentLevel=1)

    # holder for each direction of port string
    portList = (
        tile.getNorthSidePorts()
        + tile.getEastSidePorts()
        + tile.getWestSidePorts()
        + tile.getSouthSidePorts()
    )

    side_of_port = None
    for port in portList:
        if side_of_port is not port.side_of_tile:
            side_of_port = port.side_of_tile
            writer.addComment(str(side_of_port), onNewLine=True)
        # destination port are input to the tile
        # source port are output of the tile
        writer.addPortVector(
            port.name, port.io_direction, port.width - 1, indentLevel=2
        )
        writer.addComment(str(port), indentLevel=2, onNewLine=False)

    # SJUMP ports: OUTPUT exits toward supertile SM; INPUT enters from supertile SM
    if tile.sjump_ports:
        writer.addComment(
            "SJUMP ports (supertile BEL interface)", onNewLine=True, indentLevel=1
        )
        for p in tile.sjump_ports:
            writer.addPortVector(p.name, p.io_direction, f"{p.width}-1", indentLevel=2)

    # The BELs' EXTERNAL ports leave the tile as one scalar port per pin.
    for port in tile.bel_external_ports:
        for pin in port.pins:
            writer.addPortScalar(pin.name(), port.io_direction, indentLevel=2)

    writer.addComment("Tile IO ports from BELs", onNewLine=True, indentLevel=1)

    if not disable_user_clk:
        writer.addPortScalar("UserCLK", IO.INPUT, indentLevel=2)
        writer.addPortScalar("UserCLKo", IO.OUTPUT, indentLevel=2)

    if config_bit_mode == ConfigBitMode.FRAME_BASED:
        writer.addPortVector("FrameData", IO.INPUT, "FrameBitsPerRow-1", indentLevel=2)
        writer.addComment("CONFIG_PORT", onNewLine=False, end="")
        writer.addPortVector(
            "FrameData_O", IO.OUTPUT, "FrameBitsPerRow-1", indentLevel=2
        )
        writer.addPortVector(
            "FrameStrobe", IO.INPUT, "MaxFramesPerCol-1", indentLevel=2
        )
        writer.addComment("CONFIG_PORT", onNewLine=False, end="")
        writer.addPortVector(
            "FrameStrobe_O", IO.OUTPUT, "MaxFramesPerCol-1", indentLevel=2
        )

    elif config_bit_mode == ConfigBitMode.FLIPFLOP_CHAIN:
        writer.addPortScalar("MODE", IO.INPUT, indentLevel=2)
        writer.addPortScalar("CONFin", IO.INPUT, indentLevel=2)
        writer.addPortScalar("CONFout", IO.OUTPUT, indentLevel=2)
        writer.addPortScalar("CLK", IO.INPUT, indentLevel=2)

    writer.addComment("global", onNewLine=True, indentLevel=1)

    writer.addPortEnd()
    writer.addHeaderEnd(f"{tile.name}")
    writer.addDesignDescriptionStart(f"{tile.name}")

    # insert switch matrix and config_mem component declaration
    if isinstance(writer, VHDLCodeGenerator):
        # insert CLB, I/O (or whatever BEL) component declaration
        # specified in the fabric csv file after the 'BEL' key word
        # we use this list to check if we have seen a BEL description
        # before so we only insert one component declaration
        BEL_VHDL_riles_processed = []
        for i in tile.bels:
            if i.src not in BEL_VHDL_riles_processed:
                BEL_VHDL_riles_processed.append(i.src)
                writer.addComponentDeclarationForFile(i.src)

        basePath = Path(writer.outFileName).parent

        if (basePath / f"{tile.name}_switch_matrix.vhdl").exists():
            writer.addComponentDeclarationForFile(
                f"{basePath}/{tile.name}_switch_matrix.vhdl"
            )
        else:
            raise FileNotFoundError(
                f"Could not find {tile.name}_switch_matrix.vhdl in {basePath} "
                "Need to run matrix generation first"
            )

        if tile.globalConfigBits > 0 and config_bit_mode == ConfigBitMode.FRAME_BASED:
            if (basePath / f"{tile.name}_ConfigMem.vhdl").exists():
                writer.addComponentDeclarationForFile(
                    f"{basePath}/{tile.name}_ConfigMem.vhdl"
                )
            else:
                raise FileNotFoundError(
                    f"Could not find {tile.name}_ConfigMem.vhdl in {basePath} "
                    "config_mem generation first"
                )

    # signal declarations
    writer.addComment("signal declarations", onNewLine=True)
    # BEL port wires
    writer.addComment("BEL ports (e.g., slices)", onNewLine=True)
    repeatDeclaration = set()
    for bel in tile.bels:
        for i in bel.pin_names(BelPortKind.INTERNAL, IO.INPUT) + bel.pin_names(
            BelPortKind.INTERNAL, IO.OUTPUT
        ):
            if f"{i}" not in repeatDeclaration:
                writer.addConnectionScalar(i)
                repeatDeclaration.add(f"{bel.prefix}{i}")

    # Jump wires
    writer.addComment("Jump wires", onNewLine=True)
    for wire in tile.jump_wires:
        if wire.source is not None and wire.destination is not None:
            writer.addConnectionVector(wire.source.name, f"{wire.wire_count}-1")

    # SJUMP ports are module ports (declared above) and are wired to the switch
    # matrix directly in the instantiation below, so they need no internal wire.

    # internal configuration data signal to daisy-chain all BELs
    # (if any and in the order they are listed in the fabric.csv)
    writer.addComment(
        "internal configuration data signal to daisy-chain all BELs (if any and in "
        "the order they are listed in the fabric.csv)",
        onNewLine=True,
    )

    # the signal has to be number of BELs+2 bits wide (Bel_counter+1 downto 0)
    # we chain switch matrices only to the configuration port,
    # if they really contain configuration bits
    # i.e. switch matrices have a config port which is indicated by
    # `NumberOfConfigBits:0 is false`

    # The following conditional as intended to only generate the config_data signal if
    # really anything is actually configured
    # however, we leave it and just use this signal as conf_data(0 downto 0) for simply
    # touting through CONFin to CONFout
    # maybe even useful if we want to add a buffer here

    # all the signal wire need to declare first for compatibility with VHDL
    if tile.globalConfigBits > 0:
        writer.addConnectionVector("ConfigBits", "NoConfigBits-1", 0)
        writer.addConnectionVector("ConfigBits_N", "NoConfigBits-1", 0)

    writer.addNewLine()
    writer.addComment("Connection for outgoing wires", onNewLine=True)
    writer.addConnectionVector("FrameData_i", "FrameBitsPerRow-1", 0)
    writer.addConnectionVector("FrameData_O_i", "FrameBitsPerRow-1", 0)
    writer.addConnectionVector("FrameStrobe_i", "MaxFramesPerCol-1", 0)
    writer.addConnectionVector("FrameStrobe_O_i", "MaxFramesPerCol-1", 0)

    # A staged line passes an entry buffer into `<source>_i` and an exit
    # buffer out of `<sink>_i`.
    for line in staging:
        writer.addConnectionVector(f"{line.source.name}_i", line.source.width - 1)
        writer.addConnectionVector(
            f"{line.sink.name}_i", line.sink_offset + line.width - 1
        )

    writer.addNewLine()
    writer.addLogicStart()

    # buffer FrameData signals
    writer.addAssignScalar("FrameData_O_i", "FrameData_i")
    writer.addNewLine()
    for i in range(frame_bits_per_row):
        _add_buffer(
            writer, buffer, f"data_inbuf_{i}", f"FrameData[{i}]", f"FrameData_i[{i}]"
        )
    for i in range(frame_bits_per_row):
        _add_buffer(
            writer,
            buffer,
            f"data_outbuf_{i}",
            f"FrameData_O_i[{i}]",
            f"FrameData_O[{i}]",
        )

    # strobe is always added even when config bits are 0
    writer.addAssignScalar("FrameStrobe_O_i", "FrameStrobe_i")
    writer.addNewLine()
    for i in range(max_frame_per_col):
        _add_buffer(
            writer,
            buffer,
            f"strobe_inbuf_{i}",
            f"FrameStrobe[{i}]",
            f"FrameStrobe_i[{i}]",
        )

    for i in range(max_frame_per_col):
        _add_buffer(
            writer,
            buffer,
            f"strobe_outbuf_{i}",
            f"FrameStrobe_O_i[{i}]",
            f"FrameStrobe_O[{i}]",
        )

    for line in staging:
        source, sink = line.source, line.sink
        if line.buffers != (FabricCellFunction.BUFFER, FabricCellFunction.BUFFER):
            raise NotImplementedError(
                f"{source.name} -> {sink.name} passes the buffers {line.buffers}; "
                "only an entry and an exit buffer can be placed so far."
            )
        source_high = line.source_offset + line.width - 1
        shift = line.source_offset - line.sink_offset
        sink_high = f"{source_high}-{shift}" if shift else source_high
        writer.addAssignScalar(
            f"{sink.name}_i[{sink_high}:{line.sink_offset}]",
            f"{source.name}_i[{source_high}:{line.source_offset}]",
        )
        writer.addNewLine()
        entry = cells.cells[line.buffers[0]][0]
        exit_ = cells.cells[line.buffers[-1]][0]
        for c in line.pin_connections:
            name = f"{source.name}_inbuf_{c.sink.index}"
            out = f"{source.name}_i[{c.source.index}]"
            _add_buffer(writer, entry, name, c.source.name(indexed=True), out)
        for c in line.pin_connections:
            name = f"{sink.name}_outbuf_{c.sink.index}"
            into = f"{sink.name}_i[{c.sink.index}]"
            _add_buffer(writer, exit_, name, into, c.sink.name(indexed=True))

    if not disable_user_clk:
        writer.addInstantiation(
            "clk_buf",
            "inst_clk_buf",
            portsPairs=[("A", "UserCLK"), ("X", "UserCLKo")],
        )

    writer.addNewLine()
    # top configuration data daisy chaining
    if config_bit_mode == ConfigBitMode.FLIPFLOP_CHAIN:
        writer.addComment("top configuration data daisy chaining", onNewLine=True)
        writer.addAssignScalar("conf_data(conf_data'low)", "CONFin")
        writer.addComment("conf_data'low=0 and CONFin is from tile entity")
        writer.addAssignScalar("conf_data(conf_data'high)", "CONFout")
        writer.addComment("CONFout is from tile entity")

    if config_bit_mode == ConfigBitMode.FRAME_BASED and tile.globalConfigBits > 0:
        writer.addComment("configuration storage latches", onNewLine=True)
        writer.addInstantiation(
            compName=f"{tile.name}_ConfigMem",
            compInsName=f"Inst_{tile.name}_ConfigMem",
            portsPairs=[
                ("FrameData", "FrameData"),
                ("FrameStrobe", "FrameStrobe"),
                ("ConfigBits", "ConfigBits"),
                ("ConfigBits_N", "ConfigBits_N"),
            ],
            emulateParamPairs=[("Emulate_Bitstream", "Emulate_Bitstream")],
        )

    # BEL component instantiations
    if tile.bels:
        writer.addNewLine()
        writer.addComment("BEL component instantiations", onNewLine=True)

    belCounter = 0
    config_bit_ranges = tile.config_bit_ranges
    for bel in tile.bels:
        ports_pairs = []
        userclk_pair = None

        # Shared ports
        for port in bel.ports:
            if port.kind != BelPortKind.SHARED:
                continue
            for pin in port.pins:
                if port.is_clock:
                    if not disable_user_clk:
                        userclk_pair = (pin.name(), pin.name())
                else:
                    ports_pairs.append((pin.name(), pin.name()))

        ports_pairs += _bel_bus_pairs(bel)

        # Makes sure UserCLK is after ports.
        if userclk_pair is not None:
            ports_pairs.append(userclk_pair)

        if config_bit_mode == ConfigBitMode.FRAME_BASED:
            if bel.config_port is not None:
                bits = config_bit_ranges[bel]
                ports_pairs.append(
                    (
                        bel.config_port.name,
                        f"ConfigBits[{bits.stop}-1:{bits.start}]",
                    )
                )
        elif config_bit_mode == ConfigBitMode.FLIPFLOP_CHAIN:
            ports_pairs.append(("MODE", "Mode"))
            ports_pairs.append(("CONFin", f"conf_data({belCounter})"))
            ports_pairs.append(("CONFout", f"conf_data({belCounter + 1})"))
            ports_pairs.append(("CLK", "CLK"))

        writer.addInstantiation(
            compName=bel.name,
            compInsName=bel.instance_name,
            portsPairs=ports_pairs,
        )

        # FIXME: Why is the belCounter increased here by 2 and afterwards by 1?
        belCounter += 2

        # for the next BEL (if any) for cascading configuration chain
        # (this information is also needed for chaining the switch matrix)
        belCounter += 1

    # Each matrix pin is mapped to the tile signal of its wire at the matrix
    # boundary or of its jump wire, in the order the matrix module declares
    # its ports.
    signals = {
        pin: c.signal
        for pc in tile.port_connections
        if isinstance(pc, SwitchMatrixConnection | JumpConnection)
        for c in pc.pin_connections
        for pin in (c.source, c.sink)
    }
    ports_pairs = [
        (pin.name(), signals[pin])
        for ports in tile_matrix_module_ports(tile.switch_matrix)
        for port in ports
        for pin in port.pins
    ]

    if config_bit_mode == ConfigBitMode.FLIPFLOP_CHAIN:
        ports_pairs.append(("MODE", "Mode"))
        ports_pairs.append(("CONFin", f"conf_data({belCounter})"))
        ports_pairs.append(("CONFout", f"conf_data({belCounter + 1})"))
        ports_pairs.append(("CLK", "CLK"))

    if config_bit_mode == ConfigBitMode.FRAME_BASED and tile.globalConfigBits > 0:
        bits = config_bit_ranges[tile.switch_matrix]
        ports_pairs.append(("ConfigBits", f"ConfigBits[{bits.stop}-1:{bits.start}]"))
        ports_pairs.append(
            ("ConfigBits_N", f"ConfigBits_N[{bits.stop}-1:{bits.start}]")
        )

    writer.addInstantiation(
        compName=tile.switch_matrix.module_name,
        compInsName=tile.switch_matrix.instance_name,
        portsPairs=ports_pairs,
    )

    writer.addDesignDescriptionEnd()
    writer.writeToFile()


def generateSuperTile(
    writer: CodeGenerator,
    superTile: SuperTile,
    frame_bits_per_row: int = 32,
    max_frame_per_col: int = 20,
    disable_user_clk: bool = False,
    config_bit_mode: ConfigBitMode = ConfigBitMode.FRAME_BASED,
    user_clk_side: Side = Side.SOUTH,
) -> None:
    """Generate a super tile wrapper for given super tile.

    Creates a hierarchical wrapper that instantiates multiple individual tiles
    and manages their interconnections. The supertile handles:
    - Internal tile-to-tile connections within the supertile
    - External port mapping to fabric-level connections
    - Configuration data distribution to sub-tiles
    - Clock signal routing and buffering
    - External I/O port aggregation

    Parameters
    ----------
    writer : CodeGenerator
        The code generator instance for RTL output
    superTile : SuperTile
        Super tile object containing tile map and configuration
    frame_bits_per_row : int
        Number of configuration bits per row for frame-based configuration
    max_frame_per_col : int
        Maximum number of frames per column for frame-based configuration
    disable_user_clk : bool
        If True, the UserCLK port will not be generated or connected
    config_bit_mode : ConfigBitMode
        The configuration bit mode to use (frame-based or FlipFlop chain)
    user_clk_side : Side
        Side on which UserCLK enters each subtile; the chain runs towards the
        opposite side. Default SOUTH (S->N ladder).

    Raises
    ------
    FileNotFoundError
        If the supertile's switch matrix or ConfigMem HDL file is expected
        (per the instantiation conditions) but missing; run matrix / config_mem
        generation first.
    """
    writer.addHeader(f"{superTile.name}")
    writer.addParameterStart(indentLevel=1)
    if isinstance(writer, VerilogCodeGenerator):
        writer.addPreprocIfDef("EMULATION")
        maxBits = frame_bits_per_row * max_frame_per_col
        for y, row in enumerate(superTile.tileMap):
            for x, tile in enumerate(row):
                if not tile:
                    continue
                writer.addParameter(
                    f"Tile_X{x}Y{y}_Emulate_Bitstream",
                    f"[{maxBits - 1}:0]",
                    f"{maxBits}'b0",
                    indentLevel=2,
                )
        writer.addPreprocEndif()
    writer.addParameter("MaxFramesPerCol", "integer", max_frame_per_col, indentLevel=2)
    writer.addParameter("FrameBitsPerRow", "integer", frame_bits_per_row, indentLevel=2)

    writer.addParameterEnd(indentLevel=1)
    writer.addPortStart(indentLevel=1)

    ports_around = superTile.get_ports_around_tile()

    for k, v in ports_around.items():
        if not v:
            continue
        x, y = k.split(",")
        for pList in v:
            # Skip empty port lists
            if not pList:
                continue

            writer.addComment(
                f"Tile_X{x}Y{y}_{pList[0].wire_direction}",
                onNewLine=True,
                indentLevel=1,
            )
            for p in pList:
                wire = (abs(p.x_offset) + abs(p.y_offset)) * p.wire_count - 1
                writer.addPortVector(
                    f"Tile_X{x}Y{y}_{p.name}", p.io_direction, wire, indentLevel=2
                )
                writer.addComment(str(p), onNewLine=False)

    # add tile external bel port
    writer.addComment("Tile IO ports from BELs", onNewLine=True, indentLevel=1)
    for i in superTile.tiles:
        for b in i.bels:
            for p in b.pin_names(BelPortKind.EXTERNAL, IO.INPUT):
                writer.addPortScalar(p, IO.INPUT, indentLevel=2)
            for p in b.pin_names(BelPortKind.EXTERNAL, IO.OUTPUT):
                writer.addPortScalar(p, IO.OUTPUT, indentLevel=2)
            for port in b.ports:
                if port.kind != BelPortKind.SHARED or port.is_clock:
                    continue
                for pin in port.pins:
                    writer.addPortScalar(pin.name(), port.io_direction, indentLevel=2)

    # add supertile-level BEL external ports
    if superTile.bels:
        writer.addComment("SuperTile BEL IO ports", onNewLine=True, indentLevel=1)
        for b in superTile.bels:
            for p in b.pin_names(BelPortKind.EXTERNAL, IO.INPUT):
                writer.addPortScalar(p, IO.INPUT, indentLevel=2)
            for p in b.pin_names(BelPortKind.EXTERNAL, IO.OUTPUT):
                writer.addPortScalar(p, IO.OUTPUT, indentLevel=2)

    st_config_bits = superTile.total_config_bits

    # add config port
    if config_bit_mode == ConfigBitMode.FRAME_BASED:
        for y, row in enumerate(superTile.tileMap):
            for x, tile in enumerate(row):
                if tile is None:
                    continue
                if y - 1 < 0 or superTile.tileMap[y - 1][x] is None:
                    writer.addPortVector(
                        f"Tile_X{x}Y{y}_FrameStrobe_O",
                        IO.OUTPUT,
                        "MaxFramesPerCol-1",
                        indentLevel=2,
                    )
                    writer.addComment("CONFIG_PORT", onNewLine=False)
                if x - 1 < 0 or superTile.tileMap[y][x - 1] is None:
                    writer.addPortVector(
                        f"Tile_X{x}Y{y}_FrameData",
                        IO.INPUT,
                        "FrameBitsPerRow-1",
                        indentLevel=2,
                    )
                    writer.addComment("CONFIG_PORT", onNewLine=False)
                if (
                    y + 1 >= len(superTile.tileMap)
                    or superTile.tileMap[y + 1][x] is None
                ):
                    writer.addPortVector(
                        f"Tile_X{x}Y{y}_FrameStrobe",
                        IO.INPUT,
                        "MaxFramesPerCol-1",
                        indentLevel=2,
                    )
                    writer.addComment("CONFIG_PORT", onNewLine=False)
                if (
                    x + 1 >= len(superTile.tileMap[y])
                    or superTile.tileMap[y][x + 1] is None
                ):
                    writer.addPortVector(
                        f"Tile_X{x}Y{y}_FrameData_O",
                        IO.OUTPUT,
                        "FrameBitsPerRow-1",
                        indentLevel=2,
                    )
                    writer.addComment("CONFIG_PORT", onNewLine=False)
    dx, dy = USER_CLK_PREDECESSOR[user_clk_side]
    if not disable_user_clk:
        # A subtile only exposes the clock ports whose partner lies outside
        # the supertile: UserCLKo when no successor, UserCLK when no predecessor.
        for y, row in enumerate(superTile.tileMap):
            for x, tile in enumerate(row):
                if tile is None:
                    continue
                if grid_at(superTile.tileMap, x - dx, y - dy) is None:
                    writer.addPortScalar(
                        f"Tile_X{x}Y{y}_UserCLKo", IO.OUTPUT, indentLevel=2
                    )
                if grid_at(superTile.tileMap, x + dx, y + dy) is None:
                    writer.addPortScalar(
                        f"Tile_X{x}Y{y}_UserCLK", IO.INPUT, indentLevel=2
                    )
    writer.addPortEnd()
    writer.addHeaderEnd(f"{superTile.name}")
    writer.addDesignDescriptionStart(f"{superTile.name}")
    writer.addNewLine()

    if isinstance(writer, VHDLCodeGenerator):
        basePath = Path(writer.outFileName).parent
        for t in superTile.tiles:
            # This is only relevant to VHDL code generation,
            # will not affect Verilog code generation
            writer.addComponentDeclarationForFile(f"{basePath}/{t.name}/{t.name}.vhdl")
        # The wrapper instantiates the supertile-level BELs directly, so declare
        # each BEL's component once (mirroring generateTile).
        bel_srcs_seen: list = []
        for bel in superTile.bels:
            if bel.src not in bel_srcs_seen:
                bel_srcs_seen.append(bel.src)
                writer.addComponentDeclarationForFile(bel.src)
        # The wrapper also instantiates its own switch matrix and ConfigMem
        # (see below), so VHDL needs their component declarations too - mirroring
        # generateTile. Guarded by the same conditions as those instantiations.
        if superTile.supertile_matrix_dir is not None:
            sm_file = basePath / f"{superTile.name}_switch_matrix.vhdl"
            if not sm_file.exists():
                raise FileNotFoundError(
                    f"Could not find {sm_file.name} in {basePath}. "
                    "Need to run matrix generation first"
                )
            writer.addComponentDeclarationForFile(str(sm_file))
        if st_config_bits > 0 and config_bit_mode == ConfigBitMode.FRAME_BASED:
            cm_file = basePath / f"{superTile.name}_ConfigMem.vhdl"
            if not cm_file.exists():
                raise FileNotFoundError(
                    f"Could not find {cm_file.name} in {basePath}. "
                    "Need to run config_mem generation first"
                )
            writer.addComponentDeclarationForFile(str(cm_file))

    # find all internal connections
    internal_connections = superTile.get_internal_connections()

    # declare internal connections
    writer.addComment("signal declarations", onNewLine=True)

    # SJUMP signals: one vector per (child tile, SJUMP port) pair
    forward = superTile.forward_sjump_wires()
    if forward:
        writer.addComment("SJUMP signals (child tile -> supertile SM)", onNewLine=True)
        for wire in forward:
            writer.addConnectionVector(
                wire.signal_name, f"{wire.wire_count}-1", indentLevel=1
            )

    # Reverse SJUMP signals: supertile SM -> child tile inputs
    reverse = superTile.reverse_sjump_wires()
    if reverse:
        writer.addComment("SJUMP signals (supertile SM -> child tile)", onNewLine=True)
        for wire in reverse:
            writer.addConnectionVector(
                wire.signal_name, f"{wire.wire_count}-1", indentLevel=1
            )

    # BEL pin signals bridging the supertile BELs and the switch matrix
    bel_pin_signals = [
        pin
        for bel in superTile.bels
        for io in (IO.INPUT, IO.OUTPUT)
        for pin in bel.pin_names(BelPortKind.INTERNAL, io)
    ]
    if bel_pin_signals:
        writer.addComment("BEL pin signals (BEL <-> supertile SM)", onNewLine=True)
        for pin in bel_pin_signals:
            writer.addConnectionScalar(pin, indentLevel=1)

    for i, x, y in internal_connections:
        if i:
            writer.addComment(f"Tile_X{x}Y{y}_{i[0].wire_direction}", onNewLine=True)
            for p in i:
                if p.is_output:
                    wire = (abs(p.x_offset) + abs(p.y_offset)) * p.wire_count - 1
                    writer.addConnectionVector(
                        f"Tile_X{x}Y{y}_{p.name}", wire, indentLevel=1
                    )
                    writer.addComment(str(p), onNewLine=False)

    # declare internal connections for frameData, frameStrobe, and UserCLK
    for y, row in enumerate(superTile.tileMap):
        for x, tile in enumerate(row):
            if tile is None:
                continue
            if (
                0 <= y - 1 < len(superTile.tileMap)
                and superTile.tileMap[y - 1][x] is not None
            ):
                writer.addConnectionVector(
                    f"Tile_X{x}Y{y}_FrameStrobe_O",
                    "MaxFramesPerCol-1",
                    indentLevel=1,
                )
            if (
                not disable_user_clk
                and grid_at(superTile.tileMap, x - dx, y - dy) is not None
            ):
                writer.addConnectionScalar(f"Tile_X{x}Y{y}_UserCLKo", indentLevel=1)
            if (
                0 <= x + 1 < len(superTile.tileMap[y])
                and superTile.tileMap[y][x + 1] is not None
            ):
                writer.addConnectionVector(
                    f"Tile_X{x}Y{y}_FrameData_O", "FrameBitsPerRow-1", indentLevel=1
                )

    if st_config_bits > 0 and config_bit_mode == ConfigBitMode.FRAME_BASED:
        writer.addConnectionVector(
            "ST_ConfigBits", f"{st_config_bits}-1", indentLevel=1
        )
        writer.addConnectionVector(
            "ST_ConfigBits_N", f"{st_config_bits}-1", indentLevel=1
        )

    writer.addNewLine()

    writer.addLogicStart()

    # pair up the connection for tile instantiation
    for y, row in enumerate(superTile.tileMap):
        for x, tile in enumerate(row):
            north_input, south_input, east_input, west_input = [], [], [], []
            ports_pairs = []
            if tile is None:
                continue

            # north direction input connection
            north_port = [i.name for i in tile.getNorthPorts(IO.INPUT)]
            if (
                0 <= y + 1 < len(superTile.tileMap)
                and superTile.tileMap[y + 1][x] is not None
            ):
                for p in superTile.tileMap[y + 1][x].getNorthPorts(IO.OUTPUT):
                    north_input.append(f"Tile_X{x}Y{y + 1}_{p.name}")
            else:
                for p in tile.getNorthPorts(IO.INPUT):
                    north_input.append(f"Tile_X{x}Y{y}_{p.name}")

            ports_pairs += list(zip(north_port, north_input, strict=False))
            # east direction input connection
            east_port = [i.name for i in tile.getEastPorts(IO.INPUT)]
            if (
                0 <= x - 1 < len(superTile.tileMap[0])
                and superTile.tileMap[y][x - 1] is not None
            ):
                for p in superTile.tileMap[y][x - 1].getEastPorts(IO.OUTPUT):
                    east_input.append(f"Tile_X{x - 1}Y{y}_{p.name}")
            else:
                for p in tile.getEastPorts(IO.INPUT):
                    east_input.append(f"Tile_X{x}Y{y}_{p.name}")

            ports_pairs += list(zip(east_port, east_input, strict=False))

            # south direction input connection
            south_port = [i.name for i in tile.getSouthPorts(IO.INPUT)]
            if (
                0 <= y - 1 < len(superTile.tileMap)
                and superTile.tileMap[y - 1][x] is not None
            ):
                for p in superTile.tileMap[y - 1][x].getSouthPorts(IO.OUTPUT):
                    south_input.append(f"Tile_X{x}Y{y - 1}_{p.name}")
            else:
                for p in tile.getSouthPorts(IO.INPUT):
                    south_input.append(f"Tile_X{x}Y{y}_{p.name}")

            ports_pairs += list(zip(south_port, south_input, strict=False))

            # west direction input connection
            west_port = [i.name for i in tile.getWestPorts(IO.INPUT)]
            if (
                0 <= x + 1 < len(superTile.tileMap[0])
                and superTile.tileMap[y][x + 1] is not None
            ):
                for p in superTile.tileMap[y][x + 1].getWestPorts(IO.OUTPUT):
                    west_input.append(f"Tile_X{x + 1}Y{y}_{p.name}")
            else:
                for p in tile.getWestPorts(IO.INPUT):
                    west_input.append(f"Tile_X{x}Y{y}_{p.name}")

            ports_pairs += list(zip(west_port, west_input, strict=False))

            for p in (
                tile.getNorthPorts(IO.OUTPUT)
                + tile.getEastPorts(IO.OUTPUT)
                + tile.getSouthPorts(IO.OUTPUT)
                + tile.getWestPorts(IO.OUTPUT)
            ):
                ports_pairs.append((p.name, f"Tile_X{x}Y{y}_{p.name}"))

            for b in tile.bels:
                for p in b.pin_names(BelPortKind.EXTERNAL, IO.INPUT):
                    ports_pairs.append((p, p))

                for p in b.pin_names(BelPortKind.EXTERNAL, IO.OUTPUT):
                    ports_pairs.append((p, p))

            # connect SJUMP ports to supertile-level signals
            for wire in superTile.sjump_wires:
                if wire.tile_name == tile.name:
                    ports_pairs.append((wire.child_port.name, wire.signal_name))

            # add clock to tile
            if not disable_user_clk:
                if grid_at(superTile.tileMap, x + dx, y + dy) is not None:
                    ports_pairs.append(("UserCLK", f"Tile_X{x + dx}Y{y + dy}_UserCLKo"))
                else:
                    ports_pairs.append(("UserCLK", f"Tile_X{x}Y{y}_UserCLK"))
                ports_pairs.append(("UserCLKo", f"Tile_X{x}Y{y}_UserCLKo"))
            if config_bit_mode == ConfigBitMode.FRAME_BASED:
                # add connection for frameData, frameStrobe
                if (
                    0 <= x - 1 < len(superTile.tileMap[0])
                    and superTile.tileMap[y][x - 1] is not None
                ):
                    ports_pairs.append(("FrameData", f"Tile_X{x - 1}Y{y}_FrameData_O"))
                else:
                    ports_pairs.append(("FrameData", f"Tile_X{x}Y{y}_FrameData"))

                ports_pairs.append(("FrameData_O", f"Tile_X{x}Y{y}_FrameData_O"))

                if (
                    0 <= y + 1 < len(superTile.tileMap)
                    and superTile.tileMap[y + 1][x] is not None
                ):
                    ports_pairs.append(
                        ("FrameStrobe", f"Tile_X{x}Y{y + 1}_FrameStrobe_O")
                    )
                else:
                    ports_pairs.append(("FrameStrobe", f"Tile_X{x}Y{y}_FrameStrobe"))

                ports_pairs.append(("FrameStrobe_O", f"Tile_X{x}Y{y}_FrameStrobe_O"))

            emulateParamPairs = [
                ("Emulate_Bitstream", f"Tile_X{x}Y{y}_Emulate_Bitstream")
            ]

            writer.addInstantiation(
                compName=tile.name,
                compInsName=f"Tile_X{x}Y{y}_{tile.name}",
                portsPairs=ports_pairs,
                emulateParamPairs=emulateParamPairs,
            )

    # Instantiate supertile ConfigMem (shares free slots in master tile's frame space)
    if st_config_bits > 0 and config_bit_mode == ConfigBitMode.FRAME_BASED:
        mx, my = superTile.get_master_tile_coords()
        if (
            0 <= mx - 1 < len(superTile.tileMap[0])
            and superTile.tileMap[my][mx - 1] is not None
        ):
            cm_frame_data = f"Tile_X{mx - 1}Y{my}_FrameData_O"
        else:
            cm_frame_data = f"Tile_X{mx}Y{my}_FrameData"
        if (
            0 <= my + 1 < len(superTile.tileMap)
            and superTile.tileMap[my + 1][mx] is not None
        ):
            cm_frame_strobe = f"Tile_X{mx}Y{my + 1}_FrameStrobe_O"
        else:
            cm_frame_strobe = f"Tile_X{mx}Y{my}_FrameStrobe"
        writer.addInstantiation(
            compName=f"{superTile.name}_ConfigMem",
            compInsName=f"Inst_{superTile.name}_ConfigMem",
            portsPairs=[
                ("FrameData", cm_frame_data),
                ("FrameStrobe", cm_frame_strobe),
                ("ConfigBits", f"ST_ConfigBits[{st_config_bits}-1:0]"),
                ("ConfigBits_N", f"ST_ConfigBits_N[{st_config_bits}-1:0]"),
            ],
            # The supertile config bits live in free slots of the master tile's
            # frame space, so in emulation they are preloaded from the master
            # tile's bitstream parameter (not its own frame shift register).
            emulateParamPairs=[
                ("Emulate_Bitstream", f"Tile_X{mx}Y{my}_Emulate_Bitstream")
            ],
        )

    # Instantiate supertile switch matrix (if a matrix file was found)
    if superTile.supertile_matrix_dir is not None:
        sm_ports_pairs = []
        # Connect SJUMP vector signals to SM scalar input ports
        for wire in superTile.forward_sjump_wires():
            for k, pin in enumerate(wire.matrix_port.pins):
                sm_ports_pairs.append((pin.name(), f"{wire.signal_name}[{k}]"))
        # SM outputs drive BEL input signals (signals named after the BEL ports)
        for bel in superTile.bels:
            for ip in bel.pin_names(BelPortKind.INTERNAL, IO.INPUT):
                sm_ports_pairs.append((ip, ip))
        # BEL output signals feed back into the SM (routed to reverse SJUMP wires)
        for bel in superTile.bels:
            for op in bel.pin_names(BelPortKind.INTERNAL, IO.OUTPUT):
                sm_ports_pairs.append((op, op))
        # SM outputs also drive reverse SJUMP signals into child tiles
        for wire in superTile.reverse_sjump_wires():
            for k, pin in enumerate(wire.matrix_port.pins):
                sm_ports_pairs.append((pin.name(), f"{wire.signal_name}[{k}]"))
        if (
            superTile.supertile_matrix_config_bits > 0
            and config_bit_mode == ConfigBitMode.FRAME_BASED
        ):
            bits = superTile.config_bit_ranges[superTile.switch_matrix]
            sm_ports_pairs.append(
                ("ConfigBits", f"ST_ConfigBits[{bits.stop}-1:{bits.start}]")
            )
            sm_ports_pairs.append(
                ("ConfigBits_N", f"ST_ConfigBits_N[{bits.stop}-1:{bits.start}]")
            )
        writer.addInstantiation(
            compName=superTile.switch_matrix.module_name,
            compInsName=superTile.switch_matrix.instance_name,
            portsPairs=sm_ports_pairs,
        )

    # Instantiate supertile BELs
    config_bit_ranges = superTile.config_bit_ranges
    for bel in superTile.bels:
        # The signals are the supertile SM outputs / BEL inputs.
        bel_ports_pairs = _bel_bus_pairs(bel)
        if not disable_user_clk and bel.withUserCLK:
            # The supertile wrapper has no bare "UserCLK"; the BEL shares the
            # master tile's clock net (same selection the master tile uses: the
            # chained UserCLKo from its predecessor, or its own UserCLK input).
            mx, my = superTile.get_master_tile_coords()
            if grid_at(superTile.tileMap, mx + dx, my + dy) is not None:
                bel_user_clk = f"Tile_X{mx + dx}Y{my + dy}_UserCLKo"
            else:
                bel_user_clk = f"Tile_X{mx}Y{my}_UserCLK"
            bel_ports_pairs.append(("UserCLK", bel_user_clk))
        if bel.config_port is not None and config_bit_mode == ConfigBitMode.FRAME_BASED:
            bits = config_bit_ranges[bel]
            bel_ports_pairs.append(
                (bel.config_port.name, f"ST_ConfigBits[{bits.stop}-1:{bits.start}]")
            )
        writer.addInstantiation(
            compName=bel.name,
            compInsName=bel.instance_name,
            portsPairs=bel_ports_pairs,
        )

    writer.addDesignDescriptionEnd()
    writer.writeToFile()
