"""Tests for the switch matrix geometry's view of the routing ports."""

import pytest

from fabulous.fabric_definition.switch_matrix import switch_matrix_ports
from fabulous.fabric_generator.parser.parse_csv import parse_port_line
from fabulous.geometry_generator.sm_geometry import SmGeometry


@pytest.mark.parametrize(
    ("line", "width", "drawn_offset"),
    [
        # Both ends named: staged through the tile, drawn as a stair.
        pytest.param("NORTH,N4BEG,0,-4,N4END,4", 4, -4, id="staged"),
        # A terminator's half hands every bit to the matrix: direct wires.
        pytest.param("NORTH,NULL,0,-4,N4END,4", 16, 1, id="terminator"),
        pytest.param("NORTH,N1BEG,0,-1,N1END,4", 4, -1, id="single-hop"),
    ],
)
def test_matrix_port_drawing(line: str, width: int, drawn_offset: int) -> None:
    """A matrix port is drawn with as many bits as reach the matrix."""
    ports, _ = parse_port_line(line)
    sm_port = switch_matrix_ports(ports, [])[0]

    assert sm_port.width == width
    assert SmGeometry.drawn_offset(sm_port) == drawn_offset
