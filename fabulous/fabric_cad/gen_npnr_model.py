"""Nextpnr model generation for FABulous FPGA fabrics.

This module provides functionality to generate nextpnr models from FABulous fabric
definitions. The nextpnr model includes detailed descriptions of programmable
interconnect points (PIPs), basic elements of logic (BELs), and routing resources needed
for place-and-route operations.

The generated models enable nextpnr to understand the fabric architecture and perform
placement and routing for user designs.
"""

import string
from functools import cache
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from fabulous.custom_exception import InvalidState
from fabulous.fabric_cad.timing_model.FABulous_timing_model_interface import (
    FABulousTimingModelInterface,
)
from fabulous.fabric_definition.bel import Bel
from fabulous.fabric_definition.fabric import Fabric


@cache
def _npnr_template_env() -> Environment:
    """Return the cached Jinja environment for nextpnr model templates.

    Templates live in the ``fabulous/template`` package directory. Mirrors
    `fabulous.tools.tool`'s environment but with ``keep_trailing_newline=False``:
    each template renders one line/block and the module joins them, so a kept
    trailing newline would break the byte-exact output contract.

    Returns
    -------
    Environment
        The cached Jinja environment.
    """
    return Environment(
        loader=PackageLoader("fabulous", "template"),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


# Dummy BEL timing values (ns), mirroring nextpnr's historical hardcoded
# constants (fabulous.cc, update_cell_timing).
LUT_DELAY = 3.0
CARRY_CICO_DELAY = 0.2
CARRY_I_DELAY = 1.0
FF_SETUP = 2.5
FF_HOLD = 0.1
FF_CLK_TO_Q = 1.0
IO_SETUP = 2.5
IO_HOLD = 0.1
IO_CLK_TO_OUT = 2.5

# Base delay (ns) for nextpnr's placement heuristic (placement_estimate.txt).
# Static until a real timing model exists; reproduces nextpnr's old default.
BASE_DELAY_DEFAULT = 3.0

# Extra nextpnr tunables written to placement_estimate.txt. Values reproduce
# nextpnr's historical hardcoded defaults, so P&R behaviour is unchanged.
DELAY_EPSILON = 0.25
RIPUP_PENALTY = 0.5
CARRY_PREDICT_DELAY = 0.5

# Arbitrary placeholder pip delay used when no delay_model is supplied.
DUMMY_PIP_DELAY = 8


def _build_timing_arcs(cType: str, inputs: list[str], outputs: list[str]) -> list[str]:
    """Return the bel.v3 timing-arc lines for a BEL type (empty if untimed).

    Only the BEL types nextpnr times get arcs: ``FABULOUS_LC`` and the
    ``In/OutPass4_frame_config`` families. The arc set and ordering reproduce
    nextpnr's historical hardcoded constants.

    Parameters
    ----------
    cType : str
        The nextpnr cell type (``FABULOUS_LC`` for LUT4c bels).
    inputs : list[str]
        Prefix-stripped input port names.
    outputs : list[str]
        Prefix-stripped output port names.

    Returns
    -------
    list[str]
        The timing-arc lines, in emission order.
    """
    # Port selection stays in Python; the template owns the arc line shapes.
    rendered = (
        _npnr_template_env()
        .get_template("npnr_timing_arcs.j2")
        .render(
            cType=cType,
            in_ports=[p for p in inputs if p.startswith("I") and p[1:].isdigit()],
            out_ports=[p for p in outputs if p.startswith("O") and p[1:].isdigit()],
            lut_delay=LUT_DELAY,
            carry_cico_delay=CARRY_CICO_DELAY,
            carry_i_delay=CARRY_I_DELAY,
            ff_setup=FF_SETUP,
            ff_hold=FF_HOLD,
            ff_clk_to_q=FF_CLK_TO_Q,
            io_setup=IO_SETUP,
            io_hold=IO_HOLD,
            io_clk_to_out=IO_CLK_TO_OUT,
        )
        .strip("\n")
    )
    return rendered.split("\n") if rendered else []


# Representative FABULOUS_LC timing arcs for nextpnr's placement estimate.
# Static while every LC instance shares these constants (I0-I3 LUT4); a real
# per-instance timing model would regenerate this.
LC_ESTIMATE_LINES: list[str] = _build_timing_arcs(
    "FABULOUS_LC", ["I0", "I1", "I2", "I3"], []
)

# Full static placement_estimate.txt content: nextpnr placer/router tunables
# plus the representative FABULOUS_LC estimate. All values reproduce nextpnr's
# historical hardcoded defaults, so P&R behaviour is unchanged. The template
# emits exactly one trailing newline.
PLACEMENT_ESTIMATE_TEXT: str = (
    _npnr_template_env()
    .get_template("placement_estimate.j2")
    .render(
        delayScale=BASE_DELAY_DEFAULT,
        delayOffset=BASE_DELAY_DEFAULT,
        delayEpsilon=DELAY_EPSILON,
        ripupPenalty=RIPUP_PENALTY,
        carryPredictDelay=CARRY_PREDICT_DELAY,
        lc_estimate_lines=LC_ESTIMATE_LINES,
    )
)

# BEL types whose ports are exposed as fabric pins in the per-tile
# loop; a matching BEL gets a `set_io` constraint line.
IO_BEL_TYPES = (
    "IO_1_bidirectional_frame_config_pass",
    "InPass4_frame_config",
    "OutPass4_frame_config",
    "InPass4_frame_config_mux",
    "OutPass4_frame_config_mux",
)


def belLines(
    bel: Bel, letter: str, x: int, y: int
) -> tuple[str, list[str], list[str], list[str]]:
    """Build a BEL's legacy v1 line, its v2/v3 blocks, and any pin constraint.

    The bel.v3 block additionally carries timing-arc lines, but only for the
    BEL types nextpnr currently times (``FABULOUS_LC``, ``InPass4_frame_config``,
    ``OutPass4_frame_config`` and their ``_mux`` variants); every other type
    gets no arcs so no new timing paths are introduced.

    Parameters
    ----------
    bel : Bel
        The BEL to describe.
    letter : str
        The BEL's Z-position letter within its tile.
    x : int
        Tile X coordinate the BEL belongs to.
    y : int
        Tile Y coordinate the BEL belongs to.

    Returns
    -------
    tuple[str, list[str], list[str], list[str]]
        `(v1_line, v2_lines, v3_lines, constrain_lines)` - the legacy bel.txt
        line, the bel.v2/bel.v3 block lines, and zero or one `set_io` line.
    """
    cType = bel.name
    if bel.name in ("LUT4c_frame_config", "LUT4c_frame_config_dffesr"):
        cType = "FABULOUS_LC"
    inputs = [p.removeprefix(bel.prefix) for p in bel.inputs]
    outputs = [p.removeprefix(bel.prefix) for p in bel.outputs]

    env = _npnr_template_env()
    v1_line = env.get_template("npnr_bel_v1.j2").render(
        x=x, y=y, letter=letter, cType=cType, ports=bel.inputs + bel.outputs
    )

    inports = [
        {"raw": raw, "stripped": stripped}
        for raw, stripped in zip(bel.inputs, inputs, strict=True)
    ]
    outports = [
        {"raw": raw, "stripped": stripped}
        for raw, stripped in zip(bel.outputs, outputs, strict=True)
    ]
    features = [feat for feat, _cfg in sorted(bel.belFeatureMap.items())]

    def block(timing: bool) -> list[str]:
        # Business logic (which arcs apply) stays in Python; the template only
        # emits the resulting line list. Split back to lines so the caller keeps
        # extending list[str] and the reference line-diff is unchanged.
        rendered = env.get_template("npnr_bel_block.j2").render(
            x=x,
            y=y,
            letter=letter,
            cType=cType,
            prefix=bel.prefix,
            inports=inports,
            outports=outports,
            features=features,
            timing_arcs=_build_timing_arcs(cType, inputs, outputs) if timing else [],
            withUserCLK=bel.withUserCLK,
        )
        return rendered.split("\n")

    v2_lines = block(timing=False)
    v3_lines = block(timing=True)

    constrain_lines = (
        [env.get_template("npnr_pcf.j2").render(x=x, y=y, letter=letter)]
        if bel.name in IO_BEL_TYPES
        else []
    )

    return v1_line, v2_lines, v3_lines, constrain_lines


def genNextpnrModel(
    fabric: Fabric, delay_model: FABulousTimingModelInterface = None
) -> tuple[str, str, str, str, str]:
    """Generate the fabric's nextpnr model.

    Parameters
    ----------
    fabric : Fabric
        Fabric object containing tile information.
    delay_model : FABulousTimingModelInterface, optional
        Timing model interface to provide delay information, by default None.

    Returns
    -------
    tuple[str, str, str, str, str]
        - pipStr: A string with tile-internal and tile-external pip descriptions.
        - belStr: A string with old style BEL definitions.
        - belv2Str: A string with new style BEL definitions.
        - belv3Str: A string with new style BEL definitions including timing.
        - constrainStr: A string with constraint definitions.

    Raises
    ------
    InvalidState
        If a wire in a tile points to an invalid tile outside the fabric bounds.
    """
    header = (
        f"# BEL descriptions: top left corner Tile_X0Y0, "
        f"bottom right Tile_X{fabric.numberOfColumns}Y{fabric.numberOfRows}"
    )
    belStr = [header]
    belv2Str = [header]
    belv3Str = [header]
    constrainStr: list[str] = []

    # Pip context for npnr_pips.j2: one entry per non-None tile (row-major),
    # plus supertile switch-matrix pips appended at the end.
    tiles: list[dict] = []
    supertile_pips: list[dict] = []

    for y, row in enumerate(fabric.tile):
        for x, tile in enumerate(row):
            if tile is None:
                continue
            internal = []
            for source, sinkList in tile.switch_matrix.connections.items():
                for sink in sinkList:
                    delay = DUMMY_PIP_DELAY
                    if delay_model is not None:
                        delay = delay_model.pip_delay(tile.name, sink, source)
                    internal.append({"sink": sink, "source": source, "delay": delay})

            external = []
            for wire in tile.wireList:
                xDst = x + wire.xOffset
                yDst = y + wire.yOffset
                if (not (0 <= xDst <= fabric.numberOfColumns)) or (
                    not (0 <= yDst <= fabric.numberOfRows)
                ):
                    raise InvalidState(
                        f"Wire {wire} in tile X{x}Y{y} points to an invalid tile "
                        f"X{xDst}Y{yDst}. "
                        "Please check your tile CSV file for unmatching wires/offsets!"
                    )
                delay = DUMMY_PIP_DELAY
                if delay_model is not None:
                    delay = delay_model.pip_delay(
                        tile.name, wire.source, wire.destination
                    )
                external.append(
                    {
                        "source": wire.source,
                        "destination": wire.destination,
                        "xDst": xDst,
                        "yDst": yDst,
                        "delay": delay,
                    }
                )
            tiles.append({"x": x, "y": y, "internal": internal, "external": external})

            # BEL definitions: legacy v1, and new-style v2 / v3 (with timing arcs).
            belStr.append(f"#Tile_X{x}Y{y}")
            belv2Str.append(f"#Tile_X{x}Y{y}")
            belv3Str.append(f"#Tile_X{x}Y{y}")
            for i, bel in enumerate(tile.bels):
                letter = string.ascii_uppercase[i]
                v1_line, v2_lines, v3_lines, constrain_lines = belLines(
                    bel, letter, x, y
                )
                belStr.append(v1_line)
                belv2Str.extend(v2_lines)
                belv3Str.extend(v3_lines)
                constrainStr.extend(constrain_lines)

    # Supertile BEL and switch-matrix PIP emission.
    # SJUMP PIPs live in tile.wireList (added by Fabric.__post_init__) and are
    # already emitted by the per-tile loop above.
    for base_fx, base_fy, super_tile in fabric.iter_super_tile_placements():
        if not super_tile.bels and super_tile.supertile_matrix_dir is None:
            continue

        tx_local, ty_local = super_tile.get_master_tile_coords()
        ftx = base_fx + tx_local
        fty = base_fy + ty_local

        bel_offset = len(fabric.tile[fty][ftx].bels)
        belStr.append(f"#SuperTile_{super_tile.name}_X{ftx}Y{fty}")
        belv2Str.append(f"#SuperTile_{super_tile.name}_X{ftx}Y{fty}")
        belv3Str.append(f"#SuperTile_{super_tile.name}_X{ftx}Y{fty}")
        for i, bel in enumerate(super_tile.bels):
            letter = string.ascii_uppercase[bel_offset + i]
            v1_line, v2_lines, v3_lines, constrain_lines = belLines(
                bel, letter, ftx, fty
            )
            belStr.append(v1_line)
            belv2Str.extend(v2_lines)
            belv3Str.extend(v3_lines)
            constrainStr.extend(constrain_lines)

        if super_tile.switch_matrix is not None:
            for sink, sources in super_tile.switch_matrix.connections.items():
                for src in sources:
                    delay = DUMMY_PIP_DELAY
                    if delay_model is not None:
                        delay = delay_model.pip_delay(super_tile.name, sink, src)
                    supertile_pips.append(
                        {
                            "ftx": ftx,
                            "fty": fty,
                            "src": src,
                            "sink": sink,
                            "delay": delay,
                        }
                    )

    # Whole-file pips render ends with the last data line's newline; strip it so
    # the output matches the old "\n".join (no trailing newline).
    pip_str = (
        _npnr_template_env()
        .get_template("npnr_pips.j2")
        .render(tiles=tiles, supertile_pips=supertile_pips)
        .rstrip("\n")
    )

    return (
        pip_str,
        "\n".join(belStr),
        "\n".join(belv2Str),
        "\n".join(belv3Str),
        "\n".join(constrainStr),
    )


def writeNextpnrPipFile(
    fabric: Fabric,
    outputFile: Path,
    delay_model: FABulousTimingModelInterface = None,
) -> None:
    """Write the nextpnr pip file for the given fabric.

    Parameters
    ----------
    fabric : Fabric
        Fabric object containing tile information.
    outputFile : Path
        File to write the pip information to.
    delay_model : FABulousTimingModelInterface, optional
        Timing model interface to provide delay information, by default None.
    """
    pip_str, *_ = genNextpnrModel(fabric, delay_model)
    outputFile.write_text(pip_str, encoding="utf-8")
