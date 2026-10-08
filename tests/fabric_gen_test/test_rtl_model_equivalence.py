"""The generated tile RTL connects exactly what the model's fixed connections say.

yosys reads each tile module of the demo fabric, with its switch matrix, BELs
and ConfigMem as black boxes, and its nets are compared with the nets that the
tile's fixed connections form. Both sides are seen as sets of port bits: the
tile module's ports and the ports of the switch matrix and BEL instances.
Connections inside the switch matrix module are not part of the tile module.

Known difference until the buffers are modelled: the RTL stages a routing
channel through `my_buf` cells and `*_i` signals, which split the net the model
draws directly from `N4END[i + wire_count]` to `N4BEG[i]`.
"""

import json
import shutil
from pathlib import Path

import pytest

from fabulous.fabric_definition.channel import ChannelDeclaration
from fabulous.fabric_definition.define import IO, BelPortKind
from fabulous.fabric_definition.port import (
    BelPort,
    Pin,
    SJumpPort,
    SwitchMatrixPort,
    TilePort,
)
from fabulous.fabric_definition.switch_matrix import SwitchMatrix
from fabulous.fabric_definition.tile import Tile
from fabulous.fabulous_repl.fabulous_repl import FABulousREPL
from fabulous.tools.yosys import YosysTool
from tests.conftest import run_cmd

type Endpoint = tuple[str, str, int]
"""A port bit of the tile module: (instance or "" for the tile, port, bit)."""

type Net = frozenset[Endpoint]


def _endpoint(pin: Pin) -> Endpoint:
    port = pin.port
    if isinstance(port, TilePort | SJumpPort):
        return ("", port.name, pin.index)
    if isinstance(port, SwitchMatrixPort):
        return (port.matrix.instance_name, pin.name(), 0)
    if isinstance(port, BelPort):
        return (port.bel.instance_name, port.base_name, pin.index)
    raise TypeError(f"{port} is no port of the tile module or its instances")


def _modelled(tile: Tile) -> set[Endpoint]:
    """The port bits the model knows: tile ports, matrix and BEL ports."""
    pins = [pin for p in [*tile.portsInfo, *tile.sjump_ports] for pin in p.pins]
    pins += [
        pin for p in tile.switch_matrix.ports if not p.is_constant for pin in p.pins
    ]
    pins += [
        pin
        for bel in tile.bels
        for io in (IO.INPUT, IO.OUTPUT)
        for p in bel.get_ports(BelPortKind.INTERNAL, io)
        for pin in p.pins
    ]
    return {_endpoint(pin) for pin in pins}


def _model_nets(tile: Tile, universe: set[Endpoint]) -> set[Net]:
    parent: dict[Endpoint, Endpoint] = {}

    def root(e: Endpoint) -> Endpoint:
        while parent.setdefault(e, e) != e:
            e = parent[e]
        return e

    for c in tile.fixed_connections:
        if not isinstance(c.declaration, SwitchMatrix):
            parent[root(_endpoint(c.source))] = root(_endpoint(c.sink))
    nets: dict[Endpoint, set[Endpoint]] = {}
    for e in universe:
        nets.setdefault(root(e), set()).add(e)
    return {frozenset(net) for net in nets.values()}


def _rtl_nets(
    tile: Tile, project: Path, universe: set[Endpoint], workdir: Path
) -> set[Net]:
    tile_dir = tile.tileDir.parent
    black_boxes = [
        tile_dir / f"{tile.name}_switch_matrix.v",
        tile_dir / f"{tile.name}_ConfigMem.v",
        project / "Fabric" / "models_pack.v",
        *(bel.src for bel in tile.bels),
    ]
    netlist = workdir / f"{tile.name}.json"
    reads = "".join(f"read_verilog -sv -lib {f}; " for f in black_boxes if f.exists())
    YosysTool.run(
        args=[
            "-q",
            "-p",
            f"{reads}read_verilog -sv {tile_dir / f'{tile.name}.v'}; "
            f"hierarchy -top {tile.name}; proc; opt_clean -purge; "
            f"write_json {netlist}",
        ]
    )
    module = json.loads(netlist.read_text())["modules"][tile.name]
    bits: dict[int, set[Endpoint]] = {}
    for name, port in module["ports"].items():
        for i, bit in enumerate(port["bits"]):
            bits.setdefault(bit, set()).add(("", name, i))
    for instance, cell in module["cells"].items():
        for name, port_bits in cell["connections"].items():
            for i, bit in enumerate(port_bits):
                bits.setdefault(bit, set()).add((instance, name, i))
    nets = {
        frozenset(e for e in endpoints if e in universe)
        for bit, endpoints in bits.items()
        if not isinstance(bit, str)  # a constant
    } - {frozenset()}
    unconnected = universe - set().union(*nets)
    return nets | {frozenset([e]) for e in unconnected}


def _staging(tile: Tile) -> set[Net]:
    """The pass-through pairs the RTL splits with buffers (not modelled yet)."""
    return {
        frozenset([_endpoint(c.source), _endpoint(c.sink)])
        for c in tile.fixed_connections
        if isinstance(c.declaration, ChannelDeclaration)
    }


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("yosys") is None, reason="needs yosys")
def test_tile_rtl_matches_the_model(
    cli: FABulousREPL, fabulous_project: Path, tmp_path: Path
) -> None:
    """Every tile module's nets are the model's, apart from the buffered staging."""
    run_cmd(cli, "gen_fabric")
    problems = []
    for tile in cli.fabulousAPI.fabric.tileDic.values():
        universe = _modelled(tile)
        model = _model_nets(tile, universe)
        rtl = _rtl_nets(tile, fabulous_project, universe, tmp_path)
        staging = _staging(tile)
        split = {frozenset([e]) for pair in staging for e in pair}
        if model - rtl != staging or rtl - model != split:
            problems.append(
                f"{tile.name}: only in the model "
                f"{sorted(map(sorted, (model - rtl) - staging))[:5]}, only in the "
                f"RTL {sorted(map(sorted, (rtl - model) - split))[:5]}"
            )
    assert not problems, "\n".join(problems)
