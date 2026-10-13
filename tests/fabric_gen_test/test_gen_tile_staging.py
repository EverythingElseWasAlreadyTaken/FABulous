"""The tile RTL stages a channel line from its staging connections."""

from dataclasses import replace
from pathlib import Path

import pytest

from fabulous.fabric_definition.configmem import empty_config_mem
from fabulous.fabric_definition.fabric_cell_library import (
    FabricCellFunction,
    FabricCellLibrary,
)
from fabulous.fabric_definition.switch_matrix import SwitchMatrix, switch_matrix_ports
from fabulous.fabric_definition.tile import Tile
from fabulous.fabric_generator.code_generator.code_generator_Verilog import (
    VerilogCodeGenerator,
)
from fabulous.fabric_generator.gen_fabric.gen_tile import generateTile
from fabulous.fabric_generator.parser.parse_csv import parse_port_line


def _tile() -> Tile:
    """A tile staging four bits of a line `B` -> `A` spanning three tiles."""
    ports, _ = parse_port_line("NORTH,A,0,-3,B,2")
    return Tile(
        name="T",
        ports=ports,
        bels=[],
        tileDir=Path(),
        switch_matrix=SwitchMatrix(Path(), switch_matrix_ports(ports, []), {}, "T"),
        config_mem=empty_config_mem(Path("ConfigMem.csv")),
        gen_ios=[],
        pinOrderConfig={},
    )


def _rtl(tile: Tile, tmp_path: Path) -> str:
    writer = VerilogCodeGenerator()
    writer.outFileName = tmp_path / "T.v"
    generateTile(writer, tile, cells=FabricCellLibrary())
    return writer.outFileName.read_text()


def _drop_staged_bit(tile: Tile, index: int) -> None:
    """Remove the staging connection driving `A[index]`."""
    tile.__dict__["fixed_connections"] = [
        c
        for c in tile.fixed_connections
        if not (c.is_staging and c.sink.index == index)
    ]


def test_contiguous_bits_share_one_assignment(tmp_path: Path) -> None:
    rtl = _rtl(_tile(), tmp_path)

    assert "assign A_i[5-2:0] = B_i[5:2];" in rtl
    assert "my_buf B_inbuf_0" in rtl
    assert "my_buf A_outbuf_3" in rtl


def test_a_gap_splits_the_assignment(tmp_path: Path) -> None:
    tile = _tile()
    _drop_staged_bit(tile, 1)

    rtl = _rtl(tile, tmp_path)

    assert "assign A_i[2-2:0] = B_i[2:2];" in rtl
    assert "assign A_i[5-2:2] = B_i[5:4];" in rtl


def test_other_buffer_chains_are_not_placed_yet(tmp_path: Path) -> None:
    tile = _tile()
    single = (FabricCellFunction.BUFFER,)
    tile.__dict__["fixed_connections"] = [
        replace(c, buffers=single) if c.is_staging else c
        for c in tile.fixed_connections
    ]

    with pytest.raises(NotImplementedError, match="entry and an exit buffer"):
        _rtl(tile, tmp_path)
