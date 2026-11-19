"""Configuration memory generation module.

This module provides functions to generate configuration memory initialization files and
RTL code for fabric tiles. It handles the mapping of configuration bits to frames and
generates the necessary hardware description language code for memory access and
control.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from fabulous.fabric_definition.configmem import ConfigMem
from fabulous.fabric_definition.define import IO
from fabulous.fabric_generator.code_generator.code_generator import CodeGenerator
from fabulous.fabric_generator.code_generator.code_generator_Verilog import (
    VerilogCodeGenerator,
)
from fabulous.fabric_generator.code_generator.code_generator_VHDL import (
    VHDLCodeGenerator,
)

if TYPE_CHECKING:
    from fabulous.fabric_definition.supertile import SuperTile
    from fabulous.fabric_definition.tile import Tile


def generate_config_mem(writer: CodeGenerator, name: str, memory: ConfigMem) -> None:
    """Generate the RTL code for a configuration memory.

    Parameters
    ----------
    writer : CodeGenerator
        The code generator the module is written with.
    name : str
        The tile the memory belongs to.
    memory : ConfigMem
        The mapping the module implements. A mapping holding no bits gets no
        module, since `ConfigBits[-1:0]` is not a vector.
    """
    if memory.config_bits == 0:
        logger.info(f"{name} maps no configuration bits, so it gets no module")
        return
    logger.info(
        f"{name} maps {memory.config_bits} configuration bits over "
        f"{len(memory.used_frames)} frames"
    )
    logger.info(f"Generating {writer.outFileName} for {name}")
    writer.addHeader(f"{name}_ConfigMem")
    writer.addParameterStart(indentLevel=1)
    if isinstance(writer, VerilogCodeGenerator):  # emulation only in Verilog
        maxBits = memory.frame_bits_per_row * memory.max_frames_per_col
        writer.addPreprocIfDef("EMULATION")
        writer.addParameter(
            "Emulate_Bitstream",
            f"[{maxBits - 1}:0]",
            f"{maxBits}'b0",
            indentLevel=2,
        )
        writer.addPreprocEndif()
    if memory.max_frames_per_col != 0:
        writer.addParameter(
            "MaxFramesPerCol", "integer", memory.max_frames_per_col, indentLevel=2
        )
    if memory.frame_bits_per_row != 0:
        writer.addParameter(
            "FrameBitsPerRow", "integer", memory.frame_bits_per_row, indentLevel=2
        )
    writer.addParameter("NoConfigBits", "integer", memory.config_bits, indentLevel=2)
    writer.addParameterEnd(indentLevel=1)
    writer.addPortStart(indentLevel=1)
    # the port definitions are generic
    writer.addPortVector("FrameData", IO.INPUT, "FrameBitsPerRow - 1", indentLevel=2)
    writer.addPortVector("FrameStrobe", IO.INPUT, "MaxFramesPerCol - 1", indentLevel=2)
    writer.addPortVector("ConfigBits", IO.OUTPUT, "NoConfigBits - 1", indentLevel=2)
    writer.addPortVector("ConfigBits_N", IO.OUTPUT, "NoConfigBits - 1", indentLevel=2)
    writer.addPortEnd(indentLevel=1)
    writer.addHeaderEnd(f"{name}_ConfigMem")
    writer.addNewLine()
    # declare architecture
    writer.addDesignDescriptionStart(f"{name}_ConfigMem")

    if isinstance(writer, VerilogCodeGenerator):  # emulation only in Verilog
        writer.addPreprocIfDef("EMULATION")
        for crosspoint, bit in memory.bit_at.items():
            index = crosspoint.frame * memory.frame_bits_per_row + crosspoint.data_bit
            writer.addAssignScalar(f"ConfigBits[{bit}]", f"Emulate_Bitstream[{index}]")
        writer.addPreprocElse()
    writer.addNewLine()
    writer.addNewLine()
    writer.addLogicStart()
    writer.addComment("instantiate frame latches", end="")
    for crosspoint, bit in memory.bit_at.items():
        writer.addInstantiation(
            compName="config_latch",
            compInsName=(
                f"Inst_{memory.frame_names[crosspoint.frame]}_bit{crosspoint.data_bit}"
            ),
            portsPairs=[
                ("D", f"FrameData[{crosspoint.data_bit}]"),
                ("E", f"FrameStrobe[{crosspoint.frame}]"),
                ("Q", f"ConfigBits[{bit}]"),
                ("QN", f"ConfigBits_N[{bit}]"),
            ],
        )
    if isinstance(writer, VerilogCodeGenerator):  # emulation only in Verilog
        writer.addPreprocEndif()
    writer.addDesignDescriptionEnd()
    writer.writeToFile()


def _module_path(writer: CodeGenerator, name: str, tile_dir: Path) -> Path:
    """Return the `<name>_ConfigMem` module path in the tile's own directory.

    `gen_tile` looks for the module there, so a `CONFIGMEM` entry naming another
    directory moves only the CSV.
    """
    extension = "vhdl" if isinstance(writer, VHDLCodeGenerator) else "v"
    return tile_dir / f"{name}_ConfigMem.{extension}"


def generate_tile_config_mem(writer: CodeGenerator, holder: "Tile | SuperTile") -> None:
    """Write a holder's mapping file if missing, then its memory module.

    A tile's mapping defaults to the default packing, a supertile's to the
    crosspoints its master tile's memory leaves free (`ConfigMem`). A holder
    with no configuration bits gets neither file.

    Parameters
    ----------
    writer : CodeGenerator
        The code generator the module is written with, which also decides the
        HDL the module is written in.
    holder : Tile | SuperTile
        The tile or supertile the memory belongs to.
    """
    memory = holder.config_mem
    if memory.holder_bits() == 0:
        logger.info(f"{holder.name} has no configuration bits to map")
        return
    if not memory.source.is_file():
        memory.to_csv()
        logger.info(f"Wrote the mapping of {holder.name} to {memory.source}")
    writer.outFileName = _module_path(writer, holder.name, holder.directory)
    generate_config_mem(writer, holder.name, memory)
