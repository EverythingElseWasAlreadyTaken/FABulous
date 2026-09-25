"""Tests for the automatic switch-matrix list generation of custom tiles."""

from pathlib import Path

from pytest_mock import MockerFixture

from fabulous.fabric_definition.bel import Bel
from fabulous.fabric_definition.define import IO, Side
from fabulous.fabric_definition.port import BelPort, TilePort
from fabulous.fabric_generator.gen_fabric import fabric_automation
from fabulous.fabric_generator.gen_fabric.fabric_automation import (
    generateSwitchmatrixList,
)


def test_shared_reset_only_wires_bels_that_have_one(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """A tile-level shared reset reaches the BELs with a RESET port, and no others."""
    mocker.patch.object(
        fabric_automation, "get_context"
    ).return_value.proj_dir = tmp_path
    mocker.patch.object(fabric_automation, "addBelsToPrim")
    (tmp_path / "user_design").mkdir()
    with_reset = Bel(
        Path("FF.v"),
        "A_",
        "FF",
        [
            BelPort("D", IO.INPUT, 1, prefix="A_"),
            BelPort("SR", IO.INPUT, 1, prefix="A_", local_shared="RESET"),
            BelPort("Q", IO.OUTPUT, 1, prefix="A_"),
        ],
    )
    without_reset = Bel(
        Path("LUT.v"),
        "B_",
        "LUT",
        [
            BelPort("I", IO.INPUT, 2, prefix="B_"),
            BelPort("O", IO.OUTPUT, 1, prefix="B_"),
        ],
    )
    shared_reset = [
        TilePort("J_SRST_BEG", IO.OUTPUT, Side.ANY),
        TilePort("J_SRST_END", IO.INPUT, Side.ANY),
    ]
    out = tmp_path / "T_switch_matrix.list"

    generateSwitchmatrixList(
        "T", [with_reset, without_reset], out, {}, {"RESET": shared_reset}
    )

    reset_lines = [line for line in out.read_text().splitlines() if "GND0]" in line]
    assert reset_lines == ["{2}A_SR,[J_SRST_END0|GND0]"]
