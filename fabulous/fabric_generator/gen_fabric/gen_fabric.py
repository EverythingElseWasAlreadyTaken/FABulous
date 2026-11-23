"""Fabric generation module for FABulous FPGA architecture.

This module generates the top-level RTL description of an FPGA fabric, handling
tile instantiation, interconnect wiring, and configuration infrastructure. The
generated fabric uses a flat description approach for easier debugging and
verification.

Key features:
- Flat fabric instantiation with direct tile-to-tile connections
- Support for both FlipFlop chain and Frame-based configuration
- External I/O port handling for BEL connections
- Supertile support for hierarchical tile organization
- Configuration data distribution and management
"""

from fabulous.fabric_definition.connection import FrameChainConnection
from fabulous.fabric_definition.define import (
    IO,
    USER_CLK_PREDECESSOR,
    ConfigBitMode,
    grid_at,
)
from fabulous.fabric_definition.fabric import Fabric
from fabulous.fabric_definition.tile import Tile
from fabulous.fabric_generator.code_generator.code_generator import CodeGenerator
from fabulous.fabric_generator.code_generator.code_generator_VHDL import (
    VHDLCodeGenerator,
)

# The side port getters, in the order a supertile wrapper lists a subtile's ports.
_SIDE_PORTS = (
    Tile.getNorthSidePorts,
    Tile.getEastSidePorts,
    Tile.getSouthSidePorts,
    Tile.getWestSidePorts,
)

# The input port getters of the four sides, in the order a tile's inputs are
# mapped.
_SIDE_INPUTS = (
    Tile.getNorthPorts,
    Tile.getEastPorts,
    Tile.getSouthPorts,
    Tile.getWestPorts,
)


def generateFabric(writer: CodeGenerator, fabric: Fabric) -> None:
    """Generate the fabric.

    This function creates a flat description of the FPGA fabric by instantiating all
    tiles and connecting them based on the provided fabric definition. It handles the
    generation of top-level I/O ports, wiring between adjacent tiles, and the
    configuration infrastructure (either Frame-based or FlipFlop chain).
    """
    fabric.check_routing_channels()
    # we first scan all tiles if those have IOs that have to go to top
    # the order of this scan is later maintained when instantiating the actual tiles
    # header
    fabricName = fabric.name
    writer.addHeader(fabricName)
    writer.addParameterStart(indentLevel=1)
    writer.addParameter(
        "MaxFramesPerCol", "integer", fabric.maxFramesPerCol, indentLevel=2
    )
    writer.addParameter(
        "FrameBitsPerRow", "integer", fabric.frameBitsPerRow, indentLevel=2
    )
    writer.addParameterEnd(indentLevel=1)
    writer.addPortStart(indentLevel=1)
    for row in fabric.instances:
        for instance in row:
            if instance is None:
                continue
            for port in instance.tile_type.bel_external_ports:
                for pin in port.pins:
                    writer.addPortScalar(
                        f"{instance.name}_{pin.name()}",
                        port.io_direction,
                        indentLevel=2,
                    )
                    writer.addComment("EXTERNAL", onNewLine=False)

    # supertile-level BEL external ports (the BEL lives in the wrapper, not a
    # child tile); declare them at the wrapper's anchor coordinates.
    for placement in fabric.super_tile_instances:
        ax, ay, superTile = placement.anchor.x, placement.anchor.y, placement.super_tile
        for external in superTile.bel_external_ports:
            for pin in external.pins:
                writer.addPortScalar(
                    f"Tile_X{ax}Y{ay}_{pin.name()}",
                    external.io_direction,
                    indentLevel=2,
                )
                writer.addComment("EXTERNAL", onNewLine=False)

    frame_width = {
        "FrameData": f"(FrameBitsPerRow*{fabric.numberOfRows})-1",
        "FrameStrobe": f"(MaxFramesPerCol*{fabric.numberOfColumns})-1",
    }
    for port in fabric.config_logic_ports:
        writer.addPortVector(port.name, IO.INPUT, frame_width[port.name], indentLevel=2)
        writer.addComment("CONFIG_PORT", onNewLine=False)

    if not fabric.disableUserCLK:
        writer.addPortScalar("UserCLK", IO.INPUT, indentLevel=2)

    writer.addPortEnd()
    writer.addHeaderEnd(fabricName)
    writer.addDesignDescriptionStart(fabricName)
    writer.addNewLine()

    if isinstance(writer, VHDLCodeGenerator):
        # Declare a component per entity the fabric body instantiates: regular
        # tiles by their own name, supertiles by the wrapper name. Subtiles are
        # instantiated inside the wrapper, not the fabric, so they are skipped.
        # Every tile HDL lives at Tile/<name>/<name>.vhdl under the project root
        # (fabric_dir is the fabric.csv, so its parent is that root). This is
        # correct for both per-tile CSVs and the legacy inline fabric.csv, where
        # every tileDir is the fabric.csv itself rather than a per-tile directory.
        tileRoot = fabric.fabric_dir.parent / "Tile"
        for tile in fabric.tileDic.values():
            if tile.super_tile is not None:
                continue
            writer.addComponentDeclarationForFile(
                str(tileRoot / tile.name / f"{tile.name}.vhdl")
            )
        for superTile in fabric.superTileDic.values():
            writer.addComponentDeclarationForFile(
                str(tileRoot / superTile.name / f"{superTile.name}.vhdl")
            )

    # VHDL signal declarations
    writer.addComment("signal declarations", onNewLine=True, end="\n")

    if not fabric.disableUserCLK:
        for y, row in enumerate(fabric.tile):
            for x, _tile in enumerate(row):
                writer.addConnectionScalar(f"Tile_X{x}Y{y}_UserCLKo")

    writer.addComment("configuration signal declarations", onNewLine=True, end="\n")

    if fabric.configBitMode == "FlipFlopChain":
        tileCounter = 0
        for row in fabric.tile:
            for t in row:
                if t is not None:
                    tileCounter += 1
        writer.addConnectionVector("conf_data", tileCounter)

    if fabric.configBitMode == ConfigBitMode.FRAME_BASED:
        # FrameData       =>     Tile_Y3_FrameData,
        # FrameStrobe      =>     Tile_X1_FrameStrobe
        # MaxFramesPerCol : integer := 20;
        # FrameBitsPerRow : integer := 32;
        for y in range(fabric.numberOfRows):
            writer.addConnectionVector(f"Row_Y{y}_FrameData", "FrameBitsPerRow -1")

        for x in range(fabric.numberOfColumns):
            writer.addConnectionVector(
                f"Column_X{x}_FrameStrobe", "MaxFramesPerCol - 1"
            )

        for y in range(fabric.numberOfRows):
            for x in range(fabric.numberOfColumns):
                writer.addConnectionVector(
                    f"Tile_X{x}Y{y}_FrameData_O", "FrameBitsPerRow - 1"
                )

        for y in range(fabric.numberOfRows + 1):
            for x in range(fabric.numberOfColumns):
                writer.addConnectionVector(
                    f"Tile_X{x}Y{y}_FrameStrobe_O", "MaxFramesPerCol - 1"
                )

    writer.addComment("tile-to-tile signal declarations", onNewLine=True)
    for row in fabric.instances:
        for instance in row:
            if instance is None:
                continue
            for port in instance.tile_type.portsInfo:
                if port.is_output:
                    writer.addConnectionVector(
                        instance.port_signal(port), port.width - 1
                    )
    writer.addNewLine()
    # VHDL architecture body
    writer.addLogicStart()

    # top configuration data daisy chaining
    # this is copy and paste from tile code generation
    # (so we can modify this here without side effects)
    if fabric.configBitMode == "FlipFlopChain":
        writer.addComment("configuration data daisy chaining", onNewLine=True)
        writer.addAssignScalar("conf_data'low", "CONFin")
        writer.addComment("conf_data'low=0 and CONFin is from tile entity")
        writer.addAssignScalar("CONFout", "conf_data'high")
        writer.addComment("CONFout is from tile entity")

    if fabric.configBitMode == ConfigBitMode.FRAME_BASED:
        for y in range(len(fabric.tile)):
            writer.addAssignVector(
                f"Row_Y{y}_FrameData",
                "FrameData",
                f"FrameBitsPerRow*({y}+1)-1",
                f"FrameBitsPerRow*{y}",
            )
        for x in range(len(fabric.tile[0])):
            writer.addAssignVector(
                f"Column_X{x}_FrameStrobe",
                "FrameStrobe",
                f"MaxFramesPerCol*({x}+1)-1",
                f"MaxFramesPerCol*{x}",
            )

    drivers = {pc.sink: pc.source for pc in fabric.port_connections}
    frame_chain_of = {
        pc.sink: pc
        for pc in fabric.port_connections
        if isinstance(pc, FrameChainConnection)
    }
    instantiatedPosition = []
    # Tile instantiations
    for y, row in enumerate(fabric.tile):
        for x, tile in enumerate(row):
            tileLocationOffset: list[tuple[int, int]] = []
            superTileLoc = []
            if tile is None:
                continue

            if (x, y) in instantiatedPosition:
                continue

            # instantiate super tile when encountered
            # get all the ports of the tile. If is a super tile, we loop over the
            # tile map and find all the offset of the subtile, and all their related
            # ports.
            superTile = tile.super_tile

            if superTile:
                outer = superTile.outer_ports
                outer_frame = superTile.outer_config_logic_ports
                for j, row in enumerate(superTile.tileMap):
                    for i, sub in enumerate(row):
                        if sub is None:
                            continue
                        tileLocationOffset.append((i, j))
                        instantiatedPosition.append((x + i, y + j))
                        superTileLoc.append((x + i, y + j))
            else:
                tileLocationOffset.append((0, 0))

            portsPairs = []
            # use the offset to find all the related tile input, output signal
            # if is a normal tile then the offset is (0, 0)
            for i, j in tileLocationOffset:
                here = fabric.tile[y + j][x + i]
                here_at = fabric.instances[y + j][x + i]
                in_super = superTile is not None

                def _local_names(
                    ports: list, _i: int = i, _j: int = j, in_super: bool = in_super
                ) -> list[str]:
                    """Return local port names."""
                    return (
                        [f"Tile_X{_i}Y{_j}_{p.name}" for p in ports]
                        if in_super
                        else [p.name for p in ports]
                    )

                # input connection from north side of the south tile
                # (NORTH-direction wires entering this tile from south fabric neighbour)
                for get_side_ports in _SIDE_INPUTS:
                    ports = get_side_ports(here, IO.INPUT)
                    # Inside a supertile the wrapper wires the subtiles itself.
                    if in_super:
                        ports = [p for p in ports if ((i, j), p) in outer]
                    for port, local in zip(ports, _local_names(ports), strict=True):
                        driver = drivers.get((here_at, port))
                        signal = (
                            "" if driver is None else driver[0].port_signal(driver[1])
                        )
                        portsPairs.append((local, signal))

            # output signal name is same as the output port name
            if superTile:
                for i, j in tileLocationOffset:
                    at = fabric.instances[y + j][x + i]
                    for side_ports in _SIDE_PORTS:
                        for port in side_ports(at.tile_type):
                            if port.is_output and ((i, j), port) in outer:
                                portsPairs.append(
                                    (f"Tile_X{i}Y{j}_{port.name}", at.port_signal(port))
                                )
            else:
                at = fabric.instances[y][x]
                for port in tile.portsInfo:
                    if port.is_output:
                        portsPairs.append((port.name, at.port_signal(port)))

            writer.addNewLine()
            writer.addComment(
                "tile IO port will get directly connected to top-level tile module",
                onNewLine=True,
                indentLevel=0,
            )
            for i, j in tileLocationOffset:
                at = fabric.instances[y + j][x + i]
                for port in at.tile_type.bel_external_ports:
                    for pin in port.pins:
                        portsPairs.append((pin.name(), f"{at.name}_{pin.name()}"))

            # supertile-level BEL external ports: connect the wrapper's external
            # ports to the top-level nets declared at the anchor coordinates.
            if superTile:
                for external in superTile.bel_external_ports:
                    for pin in external.pins:
                        portsPairs.append((pin.name(), f"Tile_X{x}Y{y}_{pin.name()}"))

            if not fabric.disableUserCLK:
                dx, dy = USER_CLK_PREDECESSOR[fabric.userCLKSide]
                if not superTile:
                    # for userCLK: chain from the neighbour on the entry side,
                    # or the global clock when there is none
                    px = x + dx
                    py = y + dy
                    if grid_at(fabric.tile, px, py) is not None:
                        portsPairs.append(("UserCLK", f"Tile_X{px}Y{py}_UserCLKo"))
                    else:
                        portsPairs.append(("UserCLK", "UserCLK"))

                    # for userCLKo
                    portsPairs.append(("UserCLKo", f"Tile_X{x}Y{y}_UserCLKo"))
                else:
                    for i, j in tileLocationOffset:
                        # prefix for super tile port
                        pre = f"Tile_X{i}Y{j}_"

                        # UserCLK signal
                        px = x + i + dx
                        py = y + j + dy
                        if grid_at(fabric.tile, px, py) is None:
                            portsPairs.append((f"{pre}UserCLK", "UserCLK"))

                        elif (px, py) not in superTileLoc:
                            portsPairs.append(
                                (f"{pre}UserCLK", f"Tile_X{px}Y{py}_UserCLKo")
                            )

                        # UserCLKo signal: only a boundary port when the
                        # consumer is outside the supertile
                        sx = x + i - dx
                        sy = y + j - dy
                        if (sx, sy) not in superTileLoc:
                            portsPairs.append(
                                (f"{pre}UserCLKo", f"Tile_X{x + i}Y{y + j}_UserCLKo")
                            )

            # Frame inputs from their chain; a subtile's only when it faces out.
            for i, j in tileLocationOffset:
                at = fabric.instances[y + j][x + i]
                pre = f"Tile_X{i}Y{j}_" if superTile else ""
                for port in at.tile_type.config_logic_ports:
                    if port.is_input:
                        chain = frame_chain_of.get((at, port))
                        if chain is not None:
                            portsPairs.append((pre + port.name, chain.vector_signal))
                    elif not superTile or ((i, j), port) in outer_frame:
                        portsPairs.append((pre + port.name, at.port_signal(port)))

            name = ""
            emulateParamPairs = []
            # Only a frame-based tile declares an emulation bitstream.
            emulated = fabric.configBitMode == ConfigBitMode.FRAME_BASED
            if superTile:
                name = superTile.name
                for i, j in tileLocationOffset:
                    if emulated and (y + j) not in (0, fabric.numberOfRows - 1):
                        emulateParamPairs.append(
                            (
                                f"Tile_X{i}Y{j}_Emulate_Bitstream",
                                f"`Tile_X{x + i}Y{y + j}_Emulate_Bitstream",
                            )
                        )
            else:
                name = tile.name
                if emulated and y not in (0, fabric.numberOfRows - 1):
                    emulateParamPairs.append(
                        ("Emulate_Bitstream", f"`Tile_X{x}Y{y}_Emulate_Bitstream")
                    )
            writer.addInstantiation(
                compName=name,
                compInsName=f"Tile_X{x}Y{y}_{name}",
                portsPairs=portsPairs,
                emulateParamPairs=emulateParamPairs,
                add_keep=True,
            )
    writer.addDesignDescriptionEnd()
    writer.writeToFile()
