"""The generated tile RTL connects exactly what the model's fixed connections say.

yosys reads each tile module of the demo fabric, with its switch matrix, BELs
and ConfigMem as black boxes, and its nets are compared with the nets that the
tile's fixed connections form. Both sides are seen as sets of port bits: the
tile module's ports and the ports of the switch matrix and BEL instances.
Connections inside the switch matrix module are not part of the tile module.

The buffer cells of the fabric cell library pass their signal on, so a net runs
through them; every connection must pass as many of them as it has `buffers`.
"""

import json
import shutil
from pathlib import Path

import pytest

from fabulous.fabric_definition.connection import FixedConnection
from fabulous.fabric_definition.define import IO, BelPortKind
from fabulous.fabric_definition.fabric_cell_library import (
    FabricCellFunction,
    FabricCellLibrary,
)
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


def _model_connections(tile: Tile) -> list[FixedConnection[Pin]]:
    """The tile's fixed connections, without those inside the switch matrix."""
    return [
        c for c in tile.fixed_connections if not isinstance(c.declaration, SwitchMatrix)
    ]


def _model_nets(tile: Tile, universe: set[Endpoint]) -> set[Net]:
    parent: dict[Endpoint, Endpoint] = {}

    def root(e: Endpoint) -> Endpoint:
        while parent.setdefault(e, e) != e:
            e = parent[e]
        return e

    for c in _model_connections(tile):
        parent[root(_endpoint(c.source))] = root(_endpoint(c.sink))
    nets: dict[Endpoint, set[Endpoint]] = {}
    for e in universe:
        nets.setdefault(root(e), set()).add(e)
    return {frozenset(net) for net in nets.values()}


class _Rtl:
    """A tile module's nets, read by yosys, with the buffer cells passed through.

    Parameters
    ----------
    tile : Tile
        The tile whose generated module is read.
    project : Path
        The project directory, for `models_pack.v`.
    cells : FabricCellLibrary
        The library naming the buffer cells.
    workdir : Path
        Where yosys writes the netlist.

    Attributes
    ----------
    net_id : dict[Endpoint, int]
        The yosys net id of each port bit of the module and its instances.
    through : dict[int, int]
        The net id a buffer cell drives from each net id it reads.
    signal_net_ids : dict[str, list[int | str]]
        The net ids of each named signal of the module, by bit.
    """

    net_id: dict[Endpoint, int]
    through: dict[int, int]
    signal_net_ids: dict[str, list[int | str]]

    def __init__(
        self, tile: Tile, project: Path, cells: FabricCellLibrary, workdir: Path
    ) -> None:
        tile_dir = tile.tileDir.parent
        black_boxes = [
            tile_dir / f"{tile.name}_switch_matrix.v",
            tile_dir / f"{tile.name}_ConfigMem.v",
            project / "Fabric" / "models_pack.v",
            *(bel.src for bel in tile.bels),
        ]
        netlist = workdir / f"{tile.name}.json"
        reads = "".join(
            f"read_verilog -sv -lib {f}; " for f in black_boxes if f.exists()
        )
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
        buffers = {spec.cell: spec for spec in cells.cells[FabricCellFunction.BUFFER]}
        self.net_id = {}
        self.through = {}
        self.signal_net_ids = {
            name: net["bits"] for name, net in module["netnames"].items()
        }
        for name, port in module["ports"].items():
            for i, net_id in enumerate(port["bits"]):
                self.net_id[("", name, i)] = net_id
        for instance, cell in module["cells"].items():
            ports = cell["connections"]
            if spec := buffers.get(cell["type"]):
                (a,) = ports[spec.input_ports[0]]
                (x,) = ports[spec.output_ports[0]]
                # ponytail: one buffer per driving net, enough for the staging
                assert a not in self.through, f"net {a} drives two buffers"
                self.through[a] = x
                continue
            for name, net_ids in ports.items():
                for i, net_id in enumerate(net_ids):
                    self.net_id[(instance, name, i)] = net_id

    def buffers_between(self, source: Endpoint, sink: Endpoint) -> int | None:
        """Count the buffer cells from `source` to `sink`, None if not joined."""
        net_id, count = self.net_id[source], 0
        while net_id != self.net_id[sink]:
            if net_id not in self.through:
                return None
            net_id, count = self.through[net_id], count + 1
        return count

    def net_id_of(self, signal: str) -> int | str | None:
        """The net id of `signal` (`N4END[3]` or `LA_I`), None if no such signal."""
        name, _, index = signal.rstrip("]").partition("[")
        net_ids = self.signal_net_ids.get(name)
        return None if net_ids is None else net_ids[int(index or 0)]

    def nets(self, universe: set[Endpoint]) -> set[Net]:
        """The nets over `universe`, a buffer cell joining its input and output."""
        driven_by = {x: a for a, x in self.through.items()}

        def root(net_id: int | str) -> int | str:
            while net_id in driven_by:
                net_id = driven_by[net_id]
            return net_id

        nets: dict[int | str, set[Endpoint]] = {}
        unconnected = []
        for e in universe:
            net_id = self.net_id.get(e)
            if net_id is None or isinstance(net_id, str):  # unconnected or constant
                unconnected.append(frozenset([e]))
            else:
                nets.setdefault(root(net_id), set()).add(e)
        return {frozenset(net) for net in nets.values()} | set(unconnected)


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("yosys") is None, reason="needs yosys")
def test_tile_rtl_matches_the_model(
    cli: FABulousREPL, fabulous_project: Path, tmp_path: Path
) -> None:
    """Every tile module has the model's nets, buffers and signal names."""
    run_cmd(cli, "gen_fabric")
    cells = FabricCellLibrary.load(fabulous_project)
    problems = []
    for tile in cli.fabulousAPI.fabric.tileDic.values():
        universe = _modelled(tile)
        model = _model_nets(tile, universe)
        rtl = _Rtl(tile, fabulous_project, cells, tmp_path)
        rtl_nets = rtl.nets(universe)
        if model != rtl_nets:
            problems.append(
                f"{tile.name}: only in the model "
                f"{sorted(map(sorted, model - rtl_nets))[:5]}, only in the "
                f"RTL {sorted(map(sorted, rtl_nets - model))[:5]}"
            )
        miscounted = [
            c.feature
            for c in _model_connections(tile)
            if rtl.buffers_between(_endpoint(c.source), _endpoint(c.sink))
            != len(c.buffers)
        ]
        if miscounted:
            problems.append(f"{tile.name}: buffers differ on {miscounted[:5]}")
        misnamed = [
            f"{c.feature} as {c.signal}"
            for c in _model_connections(tile)
            if not c.buffers
            and rtl.net_id_of(c.signal) != rtl.net_id[_endpoint(c.source)]
        ]
        if misnamed:
            problems.append(f"{tile.name}: signals differ on {misnamed[:5]}")
    assert not problems, "\n".join(problems)
