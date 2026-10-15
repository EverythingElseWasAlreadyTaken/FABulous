"""The tile RTL stages a routing channel from its staging connections."""

from pathlib import Path

import pytest

from fabulous.fabric_definition.cell_binding import resolve_buffers
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


def _rtl(tile: Tile, tmp_path: Path, buffers: dict | None = None) -> str:
    writer = VerilogCodeGenerator()
    writer.outFileName = tmp_path / "T.v"
    if buffers is None:
        buffers = resolve_buffers([tile])
    generateTile(writer, tile, cells=FabricCellLibrary(), buffers=buffers)
    return writer.outFileName.read_text()


def test_a_staged_line_is_one_assignment(tmp_path: Path) -> None:
    rtl = _rtl(_tile(), tmp_path)

    assert "assign A_i[5-2:0] = B_i[5:2];" in rtl
    assert "my_buf B_inbuf_2" in rtl
    assert "my_buf A_outbuf_3" in rtl


def test_buffers_off_the_staged_pins_are_not_placed_yet(tmp_path: Path) -> None:
    tile = _tile()
    # A rule buffering every port pin, not only the staged ones.
    buffers = resolve_buffers(
        [tile],
        rule=lambda t: {
            pin: FabricCellFunction.BUFFER for p in t.portsInfo for pin in p.pins
        },
    )

    with pytest.raises(NotImplementedError, match="staged pins only"):
        _rtl(tile, tmp_path, buffers)


def test_unbuffered_staged_pins_are_plain_assignments(tmp_path: Path) -> None:
    rtl = _rtl(_tile(), tmp_path, buffers={})

    assert "assign A[0] = B[2];" in rtl
    assert "assign A[3] = B[5];" in rtl
    assert "my_buf" not in rtl.split("FrameStrobe_O[")[-1]
    assert "A_i" not in rtl
    assert "B_i" not in rtl


def test_partly_buffered_staged_pins_assign_pin_by_pin(tmp_path: Path) -> None:
    tile = _tile()
    # Only the entry buffers: the staged pins of the end port B.
    buffers = {
        pin: function
        for pin, function in resolve_buffers([tile]).items()
        if pin.port.name == "B"
    }
    rtl = _rtl(tile, tmp_path, buffers)

    assert "assign A[0] = B_i[2];" in rtl
    assert "my_buf B_inbuf_2" in rtl
    assert "A_outbuf" not in rtl
