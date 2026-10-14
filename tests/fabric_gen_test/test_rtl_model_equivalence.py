"""The generated RTL connects exactly what the model's connections say.

yosys reads a generated module of the demo fabric with the modules it
instantiates as black boxes, and its nets are compared with the nets that the
model's fixed connections form inside that module. Both sides are seen as sets
of port pins: the module's ports and the ports of its instances. This is done
for every tile module (its switch matrix, BELs and ConfigMem black boxes), the
supertile wrappers (subtiles, matrix, BELs) and the fabric top (tiles and
wrappers). Connections inside an instantiated module are not part of it.

The buffer cells of the fabric cell library pass their signal on, so a net runs
through them; every connection must pass as many of them as it has `buffers`.
Every connection naming a signal must be on the module's signal of that name.
"""

import json
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from fabulous.fabric_definition.cell_spec import CellSpec
from fabulous.fabric_definition.connection import FixedConnection
from fabulous.fabric_definition.define import IO, BelPortKind
from fabulous.fabric_definition.fabric import Fabric
from fabulous.fabric_definition.fabric_cell_library import (
    FabricCellFunction,
    FabricCellLibrary,
)
from fabulous.fabric_definition.port import (
    BelExternalPort,
    BelPort,
    Pin,
    SJumpPort,
    SwitchMatrixPort,
    TilePort,
)
from fabulous.fabric_definition.supertile import SuperTile
from fabulous.fabric_definition.switch_matrix import SwitchMatrix
from fabulous.fabulous_repl.fabulous_repl import FABulousREPL
from fabulous.tools.yosys import YosysTool
from tests.conftest import run_cmd

type Endpoint = tuple[str, str, int]
"""A port pin of the module: (instance or "" for the module, port, index)."""

type Net = frozenset[Endpoint]


def _bel_pins(bels: Iterable) -> list[Pin]:
    return [
        pin
        for bel in bels
        for kind in (BelPortKind.INTERNAL, BelPortKind.EXTERNAL)
        for io in (IO.INPUT, IO.OUTPUT)
        for p in bel.get_ports(kind, io)
        for pin in p.pins
    ]


def _instance_pin(pin: Pin) -> Endpoint:
    """A pin of a switch matrix or BEL instance."""
    port = pin.port
    if isinstance(port, SwitchMatrixPort):
        return (port.matrix.instance_name, pin.name(), 0)
    if isinstance(port, BelPort):
        return (port.bel.instance_name, port.base_name, pin.index)
    if isinstance(port, BelExternalPort):  # scalar module ports, one per pin
        return ("", pin.name(), 0)
    raise TypeError(f"{port} is no port of the module or its instances")


def _tile_endpoint(pin: Pin) -> Endpoint:
    if isinstance(pin.port, TilePort | SJumpPort):
        return ("", pin.port.name, pin.index)
    return _instance_pin(pin)


def _wrapper_endpoint(super_tile: SuperTile) -> Callable[[tuple], Endpoint]:
    def endpoint(end: tuple) -> Endpoint:
        (x, y), pin = end
        if isinstance(pin.port, TilePort | SJumpPort):
            tile = super_tile.tileMap[y][x]
            return (f"Tile_X{x}Y{y}_{tile.name}", pin.port.name, pin.index)
        return _instance_pin(pin)

    return endpoint


def _top_endpoint(fabric: Fabric) -> Callable[[tuple], Endpoint]:
    wrapper_of = {
        instance: (placement, xy)
        for placement in fabric.super_tile_instances
        for xy, instance in placement.tiles.items()
    }

    def endpoint(end: tuple) -> Endpoint:
        at, pin = end
        if at not in wrapper_of:
            return (f"{at.name}_{at.tile_type.name}", pin.port.name, pin.index)
        placement, (x, y) = wrapper_of[at]
        wrapper = f"{placement.anchor.name}_{placement.super_tile.name}"
        return (wrapper, f"Tile_X{x}Y{y}_{pin.port.name}", pin.index)

    return endpoint


class _Rtl:
    """A module's nets, read by yosys, with the buffer cells passed through.

    Parameters
    ----------
    module : dict
        The module of yosys' JSON netlist.
    buffers : dict[str, CellSpec]
        The buffer cells by name.

    Attributes
    ----------
    net_id : dict[Endpoint, int]
        The yosys net id of each port pin of the module and its instances.
    through : dict[int, int]
        The net id a buffer cell drives from each net id it reads.
    signal_net_ids : dict[str, list[int | str]]
        The net ids of each named signal of the module, by pin index.
    """

    net_id: dict[Endpoint, int]
    through: dict[int, int]
    signal_net_ids: dict[str, list[int | str]]

    def __init__(self, module: dict, buffers: dict[str, CellSpec]) -> None:
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

    @classmethod
    def read(
        cls,
        top_file: Path,
        black_boxes: Iterable[Path],
        top: str,
        buffers: dict[str, CellSpec],
        workdir: Path,
    ) -> "_Rtl":
        """Read module `top` from `top_file`, the other modules as black boxes.

        Parameters
        ----------
        top_file : Path
            The file of the module.
        black_boxes : Iterable[Path]
            The files of the modules it instantiates; missing ones are skipped.
        top : str
            The module name.
        buffers : dict[str, CellSpec]
            The buffer cells by name.
        workdir : Path
            Where yosys writes the netlist.

        Returns
        -------
        _Rtl
            The module's nets.
        """
        netlist = workdir / f"{top}.json"
        reads = "".join(
            f"read_verilog -sv -lib {f}; "
            for f in dict.fromkeys(black_boxes)
            if f.exists()
        )
        YosysTool.run(
            args=[
                "-q",
                "-p",
                f"{reads}read_verilog -sv {top_file}; "
                f"hierarchy -top {top}; proc; opt_clean -purge; "
                f"write_json {netlist}",
            ]
        )
        return cls(json.loads(netlist.read_text())["modules"][top], buffers)

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


def _problems(
    module: str,
    connections: list[FixedConnection],
    endpoint: Callable,
    universe: set[Endpoint],
    rtl: _Rtl,
) -> list[str]:
    """Compare a module's RTL with the model's connections inside it."""
    parent: dict[Endpoint, Endpoint] = {}

    def root(e: Endpoint) -> Endpoint:
        while parent.setdefault(e, e) != e:
            e = parent[e]
        return e

    for c in connections:
        parent[root(endpoint(c.source))] = root(endpoint(c.sink))
    model_nets: dict[Endpoint, set[Endpoint]] = {}
    for e in universe:
        model_nets.setdefault(root(e), set()).add(e)
    model = {frozenset(net) for net in model_nets.values()}
    rtl_nets = rtl.nets(universe)

    problems = []
    if model != rtl_nets:
        problems.append(
            f"{module}: only in the model "
            f"{sorted(map(sorted, model - rtl_nets))[:5]}, only in the "
            f"RTL {sorted(map(sorted, rtl_nets - model))[:5]}"
        )
    miscounted = [
        c.feature
        for c in connections
        if rtl.buffers_between(endpoint(c.source), endpoint(c.sink)) != len(c.buffers)
    ]
    if miscounted:
        problems.append(f"{module}: buffers differ on {miscounted[:5]}")
    misnamed = []
    for c in connections:
        if c.buffers:
            continue
        if rtl.net_id_of(c.signal) != rtl.net_id[endpoint(c.source)]:
            misnamed.append(f"{c.feature} as {c.signal}")
    if misnamed:
        problems.append(f"{module}: signals differ on {misnamed[:5]}")
    return problems


def _inside(connections: Iterable[FixedConnection]) -> list[FixedConnection]:
    """The connections of a module, without those inside its switch matrix."""
    return [c for c in connections if not isinstance(c.declaration, SwitchMatrix)]


@pytest.fixture
def generated(cli: FABulousREPL) -> Fabric:
    """Return the demo fabric with all its RTL generated."""
    run_cmd(cli, "gen_fabric")
    return cli.fabulousAPI.fabric


@pytest.fixture
def buffers(fabulous_project: Path) -> dict[str, CellSpec]:
    """Return the buffer cells of the project's fabric cell library."""
    library = FabricCellLibrary.load(fabulous_project)
    return {spec.cell: spec for spec in library.cells[FabricCellFunction.BUFFER]}


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("yosys") is None, reason="needs yosys")
def test_tile_rtl_matches_the_model(
    generated: Fabric,
    buffers: dict[str, CellSpec],
    fabulous_project: Path,
    tmp_path: Path,
) -> None:
    """Every tile module has the model's nets, buffers and signal names."""
    problems = []
    for tile in generated.tileDic.values():
        tile_dir = tile.tileDir.parent
        black_boxes = [
            tile_dir / f"{tile.name}_switch_matrix.v",
            tile_dir / f"{tile.name}_ConfigMem.v",
            fabulous_project / "Fabric" / "models_pack.v",
            *(bel.src for bel in tile.bels),
        ]
        rtl = _Rtl.read(
            tile_dir / f"{tile.name}.v", black_boxes, tile.name, buffers, tmp_path
        )
        ports = [*tile.portsInfo, *tile.sjump_ports, *tile.bel_external_ports]
        pins = [pin for p in ports for pin in p.pins] + _bel_pins(tile.bels)
        pins += [
            pin for p in tile.switch_matrix.ports if not p.is_constant for pin in p.pins
        ]
        universe = {_tile_endpoint(pin) for pin in pins}
        connections = _inside(tile.fixed_connections)
        problems += _problems(tile.name, connections, _tile_endpoint, universe, rtl)
    assert not problems, "\n".join(problems)


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("yosys") is None, reason="needs yosys")
def test_supertile_wrapper_matches_the_model(
    generated: Fabric,
    buffers: dict[str, CellSpec],
    fabulous_project: Path,
    tmp_path: Path,
) -> None:
    """Every supertile wrapper wires its subtiles, matrix and BELs as modelled."""
    problems = []
    assert generated.superTileDic, "the demo has a supertile"
    for super_tile in generated.superTileDic.values():
        wrapper_dir = super_tile.tileDir.parent
        black_boxes = [
            wrapper_dir / f"{super_tile.name}_switch_matrix.v",
            wrapper_dir / f"{super_tile.name}_ConfigMem.v",
            fabulous_project / "Fabric" / "models_pack.v",
            *(bel.src for bel in super_tile.bels),
            *(wrapper_dir / t.name / f"{t.name}.v" for t in super_tile.tiles),
        ]
        rtl = _Rtl.read(
            wrapper_dir / f"{super_tile.name}.v",
            black_boxes,
            super_tile.name,
            buffers,
            tmp_path,
        )
        endpoint = _wrapper_endpoint(super_tile)
        ends = [
            ((x, y), pin)
            for y, row in enumerate(super_tile.tileMap)
            for x, tile in enumerate(row)
            if tile is not None
            for p in [*tile.portsInfo, *tile.sjump_ports]
            for pin in p.pins
        ]
        universe = {endpoint(end) for end in ends}
        pins = [*_bel_pins(super_tile.bels)]
        pins += [pin for p in super_tile.bel_external_ports for pin in p.pins]
        if super_tile.switch_matrix is not None:
            pins += [
                pin
                for p in super_tile.switch_matrix.ports
                if not p.is_constant
                for pin in p.pins
            ]
        universe |= {_instance_pin(pin) for pin in pins}
        connections = _inside(super_tile.fixed_connections)
        problems += _problems(super_tile.name, connections, endpoint, universe, rtl)
    assert not problems, "\n".join(problems)


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("yosys") is None, reason="needs yosys")
def test_fabric_top_matches_the_model(
    generated: Fabric,
    buffers: dict[str, CellSpec],
    tmp_path: Path,
) -> None:
    """The fabric top wires the tiles by their tile port connections, as modelled."""
    fabric_dir = generated.fabric_dir.parent / "Fabric"
    tile_root = generated.fabric_dir.parent / "Tile"
    black_boxes = [
        *(t.tileDir.parent / f"{t.name}.v" for t in generated.tileDic.values()),
        *(
            tile_root / st.name / f"{st.name}.v"
            for st in generated.superTileDic.values()
        ),
        fabric_dir / "models_pack.v",
    ]
    rtl = _Rtl.read(
        fabric_dir / f"{generated.name}.v",
        black_boxes,
        generated.name,
        buffers,
        tmp_path,
    )
    endpoint = _top_endpoint(generated)
    placement_of = {
        instance: placement
        for placement in generated.super_tile_instances
        for instance in placement.tiles.values()
    }
    universe = set()
    for row in generated.instances:
        for instance in row:
            if instance is None:
                continue
            for port in instance.tile_type.portsInfo:
                for pin in port.pins:
                    e = endpoint((instance, pin))
                    # A subtile port is a wrapper port only if it faces outward.
                    if instance not in placement_of or e in rtl.net_id:
                        universe.add(e)
    connections = [c for pc in generated.port_connections for c in pc.pin_connections]
    problems = _problems(generated.name, connections, endpoint, universe, rtl)
    assert not problems, "\n".join(problems)
