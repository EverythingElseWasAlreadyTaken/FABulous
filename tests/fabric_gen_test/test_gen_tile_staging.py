"""The tile RTL stages a routing channel from its staging connections."""

from dataclasses import replace
from pathlib import Path

import pytest

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
    """A tile staging four pins of a routing channel `B` -> `A` over three tiles."""
    ports, _ = parse_port_line("NORTH,A,0,-3,B,2")
    return Tile(
        name="T",
        ports=ports,
        bels=[],
        tileDir=Path(),
        switch_matrix=SwitchMatrix(Path(), switch_matrix_ports(ports, []), {}, "T"),
        gen_ios=[],
        pinOrderConfig={},
    )


def _rtl(tile: Tile, tmp_path: Path) -> str:
    writer = VerilogCodeGenerator()
    writer.outFileName = tmp_path / "T.v"
    generateTile(writer, tile, cells=FabricCellLibrary())
    return writer.outFileName.read_text()


def test_a_staged_line_is_one_assignment(tmp_path: Path) -> None:
    rtl = _rtl(_tile(), tmp_path)

    assert "assign A_i[5-2:0] = B_i[5:2];" in rtl
    assert "my_buf B_inbuf_0" in rtl
    assert "my_buf A_outbuf_3" in rtl


def test_other_buffer_chains_are_not_placed_yet(tmp_path: Path) -> None:
    tile = _tile()
    single = (FabricCellFunction.BUFFER,)
    tile.__dict__["port_connections"] = [
        replace(pc, buffers=single) for pc in tile.port_connections
    ]

    with pytest.raises(NotImplementedError, match="entry and an exit buffer"):
        _rtl(tile, tmp_path)
