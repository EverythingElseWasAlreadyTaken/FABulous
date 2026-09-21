"""Parser functions for switch matrix and list file configurations.

This module provides utilities for parsing switch matrix CSV files and list files used
in fabric definition. It handles expansion of port definitions, connection mappings, and
validation of port configurations.
"""

import re
from collections import defaultdict
from pathlib import Path
from typing import Literal, overload

from loguru import logger

from fabulous.custom_exception import (
    InvalidListFileDefinition,
    InvalidSwitchMatrixDefinition,
)


def parseMatrix(
    fileName: Path, preserve_list_order: bool = False
) -> dict[str, list[str]]:
    """Parse the matrix CSV into a dictionary from destination to source.

    A non-zero cell denotes a configurable connection. When
    `preserve_list_order` is set, the cell's integer encodes the mux input
    position (higher = earlier, MSB-first) and that order is kept; when unset,
    every connection is treated as a plain `1` so the inputs fall back to
    CSV-column order (legacy behaviour). Both sort by `(-value, column)`.

    The top-left header cell is a label only (conventionally the tile name)
    and is not validated: the mux-input columns are `header[1:]`.

    Parameters
    ----------
    fileName : Path
        Directory of the matrix CSV file.
    preserve_list_order : bool, optional
        Keep the cell-encoded mux-input order when True; otherwise treat every
        connection as `1` and use CSV-column order. Defaults to False.

    Raises
    ------
    InvalidSwitchMatrixDefinition
        A non-integer cell value in a row.

    Returns
    -------
    dict[str, list[str]]
        Dictionary from destination to a list of sources.
    """
    path = fileName.absolute()
    with path.open() as f:
        lines = re.sub(r"#.*", "", f.read()).split("\n")

    header = lines[0].split(",")
    dest_list = header[1:]

    connections: dict[str, list[str]] = {}
    for line in lines[1:]:
        fields = line.split(",")
        port_name, row = fields[0], fields[1:]
        if not port_name:
            continue
        items: list[tuple[int, int, str]] = []
        for k, v in enumerate(row):
            stripped = v.strip()
            if stripped == "":
                continue
            if k >= len(dest_list):
                raise InvalidSwitchMatrixDefinition(
                    f"{path}: row {port_name!r} has a non-empty cell {stripped!r} "
                    f"in column {k}, beyond the {len(dest_list)} destination "
                    "columns declared in the header. The row is wider than the "
                    "header, so this connection cannot be mapped to a destination."
                )
            try:
                value = int(stripped)
            except ValueError as exc:
                raise InvalidSwitchMatrixDefinition(
                    f"{path}: row {port_name!r} column {k} has non-integer "
                    f"cell value {stripped!r}"
                ) from exc
            if value != 0 and k < len(dest_list):
                sort_value = value if preserve_list_order else 1
                items.append((sort_value, k, dest_list[k]))
        items.sort(key=lambda x: (-x[0], x[1]))
        connections[port_name] = [d for _, _, d in items]
    return connections


def expandListPorts(port: str) -> list[str]:
    """Expand the .list file entry into a list of port strings.

    Parameters
    ----------
    port : str
        The port entry to expand. If it contains "[", it's split
        into multiple entries based on "|".

    Raises
    ------
    ValueError
        If the port entry contains "[" or "{" without matching closing
        bracket "]"/"}", or if a "{...}" multiplier is not a positive
        integer.

    Returns
    -------
    list[str]
        The expanded list of port strings.
    """
    if port.count("[") != port.count("]") or port.count("{") != port.count("}"):
        raise ValueError(f"Invalid port entry: {port}, mismatched brackets")

    # "[...]" splits the port into alternatives separated by "|",
    # expanding each recursively
    if "[" in port:
        left_index = port.find("[")
        right_index = port.find("]")
        before = port[:left_index]
        after = port[right_index + 1 :]
        result = []
        for entry in port[left_index + 1 : right_index].split("|"):
            result.extend(expandListPorts(before + entry + after))
        return result

    # "{N}" is a multiplier: repeat the port N times and strip the
    # multiplier from the name. N must be a positive integer; "{0}" and
    # non-numeric "{x}" are malformed and must not leak literal braces
    # into the returned port name.
    port = port.replace(" ", "")
    brace_contents = re.findall(r"\{([^{}]*)\}", port)
    if not brace_contents:
        return [port]

    port_multiplier = 0
    for content in brace_contents:
        if not content.isdigit() or int(content) == 0:
            raise ValueError(
                f"Invalid port entry: {port}, multiplier '{{{content}}}' must be "
                "a positive integer"
            )
        port_multiplier += int(content)
    port = re.sub(r"\{[^{}]*\}", "", port)
    return [port] * port_multiplier


@overload
def parseList(
    filePath: Path, collect: Literal["pair"] = "pair"
) -> list[tuple[str, str]]:
    pass


@overload
def parseList(
    filePath: Path, collect: Literal["source", "sink"]
) -> dict[str, list[str]]:
    pass


def parseList(
    filePath: Path,
    collect: Literal["pair", "source", "sink"] = "pair",
) -> list[tuple[str, str]] | dict[str, list[str]]:
    """Parse a list file and expand the list file information into a list of tuples.

    Parameters
    ----------
    filePath : Path
        The path to the list file to parse.
    collect : Literal["pair", "source", "sink"], optional
        Collect value by source, sink or just as (source, sink) pair.
        Defaults to "pair".

    Raises
    ------
    FileNotFoundError
        The file does not exist.
    InvalidListFileDefinition
        Invalid format in the list file.

    Returns
    -------
    list[tuple[str, str]] | dict[str, list[str]]
        Return either a list of connection pairs or a dictionary of lists which is
        collected by the specified option, source or sink.
    """
    path = filePath.absolute()
    if not path.exists():
        raise FileNotFoundError(f"The file {path} does not exist.")

    pairs: list[tuple[str, str]] = []
    with path.open() as f:
        content = re.sub(r"#.*", "", f.read())
    for line_num, raw_line in enumerate(content.split("\n")):
        fields = [
            f for f in raw_line.replace(" ", "").replace("\t", "").split(",") if f
        ]
        if not fields:
            continue
        if len(fields) != 2:
            raise InvalidListFileDefinition(
                f"Invalid list formatting in file: {path} at line {line_num}: {fields}"
            )
        source_entry, sink_entry = fields[0], fields[1]

        if source_entry == "INCLUDE":
            pairs.extend(parseList(path.parent / sink_entry, "pair"))
            continue

        expanded_sources = expandListPorts(source_entry)
        expanded_sinks = expandListPorts(sink_entry)
        if len(expanded_sources) != len(expanded_sinks):
            raise InvalidListFileDefinition(
                f"List file {path} does not have the same number of source and "
                f"sink ports at line {line_num}: {fields}"
            )
        pairs.extend(zip(expanded_sources, expanded_sinks, strict=True))

    unique_pairs = list(dict.fromkeys(pairs))
    if len(unique_pairs) != len(pairs):
        logger.warning(
            f"{path.name}: ignoring {len(pairs) - len(unique_pairs)} duplicate "
            "connection(s)"
        )

    if collect == "source":
        grouped: defaultdict[str, list[str]] = defaultdict(list)
        for source, sink in unique_pairs:
            grouped[source].append(sink)
        return dict(grouped)

    if collect == "sink":
        grouped = defaultdict(list)
        for source, sink in unique_pairs:
            grouped[sink].append(source)
        return dict(grouped)

    return unique_pairs


def write_matrix_csv(
    connections: dict[str, list[str]], path: Path, tile_name: str
) -> None:
    """Write name-level switch matrix connections to a `.csv` file.

    The file is written in the format consumed by `parseMatrix`:
    the header row contains mux-input signal names (column headers),
    each data row is `mux_output_port, v0, v1, ...`, and comment
    annotations (`#,count`) are appended for human readability. Each
    mux input is encoded with a 1-based descending index (not a bare
    `1`) so `parseMatrix` recovers the exact per-mux order regardless of
    the column arrangement, making a `.list` -> `.csv` -> `.list` round
    trip order-faithful.

    Parameters
    ----------
    connections : dict[str, list[str]]
        Mux output name -> mux input names, in the order to encode.
    path : Path
        Destination `.csv` file. Created (or overwritten) by this call.
    tile_name : str
        Tile name written to the top-left cell of the CSV header.
    """
    # Column headers = unique mux-input signals, in first-seen order.
    mux_inputs_ordered: list[str] = []
    seen: set[str] = set()
    for signals in connections.values():
        for s in signals:
            if s not in seen:
                seen.add(s)
                mux_inputs_ordered.append(s)

    input_index = {s: j for j, s in enumerate(mux_inputs_ordered)}
    mux_outputs = list(connections.keys())

    # matrix[row][col]: row = mux output, col = mux input signal. The value
    # is a 1-based descending index (first input = highest) so parseMatrix's
    # (-value, column) sort recovers this exact order, not the column order.
    matrix: list[list[int]] = [[0] * len(mux_inputs_ordered) for _ in mux_outputs]
    for i, signals in enumerate(connections.values()):
        n = len(signals)
        for idx, src in enumerate(signals):
            matrix[i][input_index[src]] = n - idx

    col_counts = [
        sum(1 for row in matrix if row[j] != 0) for j in range(len(mux_inputs_ordered))
    ]

    with path.open("w") as f:
        f.write(f"{tile_name},{','.join(mux_inputs_ordered)}\n")
        for i, dest in enumerate(mux_outputs):
            row_nonzero = sum(1 for v in matrix[i] if v != 0)
            f.write(f"{dest},{','.join(str(v) for v in matrix[i])},#,{row_nonzero}\n")
        f.write(f"#,{','.join(str(c) for c in col_counts)}")


def write_list(connections: dict[str, list[str]], path: Path) -> None:
    """Write name-level switch matrix connections to a `.list` file.

    One line per mux output in the compact form
    `{N}mux_output,[input0|input1|...]` where `N` is the number of mux
    inputs. The `{N}` multiplier repeats the output so `parseList`
    pairs it with each bracketed input. Outputs with no inputs are omitted.

    The inputs are always written reversed (MSB-first), independent of
    `preserve_list_order` - the file always encodes the full order, and the
    reader decides how to interpret it: a `preserve_list_order` read
    recovers this exact order, while a plain read re-derives it from the
    tile's ports.

    Parameters
    ----------
    connections : dict[str, list[str]]
        Mux output name -> mux input names (LSB-first; written reversed).
    path : Path
        Destination `.list` file. Created (or overwritten) by this call.
    """
    with path.open("w") as f:
        for mux_output, mux_inputs in connections.items():
            if not mux_inputs:
                continue
            inputs = mux_inputs[::-1]
            f.write(f"{{{len(mux_inputs)}}}{mux_output},[{'|'.join(inputs)}]\n")
