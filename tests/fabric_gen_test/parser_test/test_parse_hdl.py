"""Tests for the BEL configuration port parsed by `parseBelFile`."""

from pathlib import Path

import pytest

from fabulous.custom_exception import InvalidBelDefinition
from fabulous.fabric_definition.define import IO
from fabulous.fabric_generator.parser.parse_hdl import parseBelFile


def _write_bel(tmp_path: Path, config_decl: str) -> Path:
    bel = tmp_path / "CFG_BEL.v"
    bel.write_text(
        f"""(* FABulous, BelMap, INIT=0, MODE=1 *)
module CFG_BEL (
    input A,
    output Q,
    {config_decl}
);
    assign Q = A;
endmodule
"""
    )
    return bel


@pytest.mark.parametrize(
    ("config_decl", "name"),
    [
        ("(* FABulous, GLOBAL *) input [1:0] ConfigBits", "ConfigBits"),
        ("(* FABulous, CONFIG *) input [1:0] Cfg", "Cfg"),
        ("(* FABulous, CONFIG_PORT *) input [1:0] Cfg", "Cfg"),
    ],
)
def test_config_port_holds_the_bel_map(
    tmp_path: Path, config_decl: str, name: str
) -> None:
    """`ConfigBits` or a `CONFIG` port becomes the BEL's config port, not a BelPort."""
    bel = parseBelFile(_write_bel(tmp_path, config_decl), "X_")
    assert bel.config_port is not None
    assert bel.config_port.name == name
    assert bel.config_port.io_direction == IO.INPUT
    assert bel.config_port.bel is bel
    assert bel.configBit == 2
    assert list(bel.belFeatureMap) == ["INIT", "MODE"]
    assert [p.name for p in bel.ports] == ["X_A", "X_Q"]


def test_second_config_port_raises(tmp_path: Path) -> None:
    """A BEL has at most one configuration port."""
    decl = "input [1:0] ConfigBits,\n    (* FABulous, CONFIG *) input Cfg"
    with pytest.raises(InvalidBelDefinition, match="more than one configuration"):
        parseBelFile(_write_bel(tmp_path, decl))
