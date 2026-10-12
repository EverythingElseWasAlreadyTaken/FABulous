"""Tests for the fabric cell library and its `models_pack` fallback."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from fabulous.fabric_definition.cell_spec import CellSpec
from fabulous.fabric_definition.fabric_cell_library import (
    FABRIC_CELL_LIBRARY_RELPATH,
    MODELS_PACK_CELLS,
    FabricCellFunction,
    FabricCellLibrary,
)

_OWN_BUFFER = CellSpec(cell="buf_x2", input_ports=("I",), output_ports=("Z",))


def _write(project_dir: Path, body: str) -> None:
    (project_dir / FABRIC_CELL_LIBRARY_RELPATH).parent.mkdir()
    (project_dir / FABRIC_CELL_LIBRARY_RELPATH).write_text(body)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, MODELS_PACK_CELLS),
        ("", MODELS_PACK_CELLS),
        ("cells: {}", MODELS_PACK_CELLS),
        (
            "cells:\n  buffer:\n    - {cell: buf_x2, input_ports: [I], "
            "output_ports: [Z]}",
            {FabricCellFunction.BUFFER: [_OWN_BUFFER]},
        ),
    ],
    ids=["no file", "empty file", "no cells", "own buffer"],
)
def test_load_falls_back_per_function(
    tmp_path: Path,
    body: str | None,
    expected: dict[FabricCellFunction, list[CellSpec]],
) -> None:
    """Functions the file does not give keep their `models_pack` cells."""
    if body is not None:
        _write(tmp_path, body)
    assert FabricCellLibrary.load(tmp_path).cells == expected


@pytest.mark.parametrize(
    "body",
    ["buffers: {}", "cells:\n  inverter: [{cell: inv}]", "cells:\n  buffer: []"],
    ids=["unknown section", "unknown function", "no cell for a function"],
)
def test_load_rejects_bad_entries(tmp_path: Path, body: str) -> None:
    """A mistake in the file is an error, not silently the default."""
    _write(tmp_path, body)
    with pytest.raises(ValidationError):
        FabricCellLibrary.load(tmp_path)
