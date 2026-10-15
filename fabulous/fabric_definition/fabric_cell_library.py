"""The FABulous fabric cells: the cells that implement the fabric's functions.

The model describes what a cell does (`FabricCellFunction`), the fabric cell
library which cells do it and through which ports, mirroring the PDK's
`StdCellLibrary` but kept apart from it. A project overrides the cells per
function in `Fabric/fabric_cell_library.yaml`; every function it leaves out, or the
whole file when absent, falls back to the cells of the shipped `models_pack`.

The same file may name the rules deciding which function sits where
(`cell_binding`), as `<python file>:<function>` relative to the file:

```yaml
rules:
  buffers: cell_rules.py:tile_buffers
```
"""

import importlib.util
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from fabulous.fabric_definition.cell_spec import CellSpec


class FabricCellFunction(StrEnum):
    """What a FABulous fabric cell does.

    A behaviour, not a cell: the fabric cell library maps it to the cells that
    implement it. Kept apart from the PDK standard cells (`std_cell_library`),
    since the fabric cells shape the generated RTL.

    - BUFFER: passes the signal on unchanged.
    """

    BUFFER = "buffer"


FABRIC_CELL_LIBRARY_RELPATH = Path("Fabric") / "fabric_cell_library.yaml"
"""Project-relative path to the fabric cell library file."""

MODELS_PACK_CELLS = {
    FabricCellFunction.BUFFER: [
        CellSpec(cell="my_buf", input_ports=("A",), output_ports=("X",))
    ]
}
"""The `models_pack` cells of each function."""


class FabricCellRules(BaseModel):
    """The project's rules deciding which fabric cell function sits where.

    `buffers` is a `cell_binding.BufferRule`, None for the default
    (`cell_binding.staged_pin_buffers`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    buffers: Callable[..., dict] | None = None


def _load_rule(spec: str, base: Path) -> Callable[..., dict]:
    """Load the rule `<python file>:<function>`, the file relative to `base`.

    Parameters
    ----------
    spec : str
        The rule, e.g. `cell_rules.py:tile_buffers`.
    base : Path
        The directory the file is relative to.

    Returns
    -------
    Callable[..., dict]
        The function.

    Raises
    ------
    ValueError
        If `spec` is not `<file>:<function>` or the file has no such name.
    FileNotFoundError
        If the file does not exist.
    TypeError
        If the name is not callable.
    """
    file, sep, name = spec.rpartition(":")
    if not sep or not file or not name:
        raise ValueError(f"cell rule {spec!r} is not <python file>:<function>")
    path = base / file
    if not path.is_file():
        raise FileNotFoundError(f"cell rule file {path} not found")
    module_spec = importlib.util.spec_from_file_location(
        f"fabulous_cell_rules.{path.stem}", path
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    if not hasattr(module, name):
        raise ValueError(f"cell rule file {path} has no function {name!r}")
    function = getattr(module, name)
    if not callable(function):
        raise TypeError(f"cell rule {name!r} in {path} is not a function")
    return function


class FabricCellLibrary(BaseModel):
    """The cells implementing the fabric's functions, and the project's rules.

    `cells` holds the cells of each function, at least one; a function may list
    several (e.g. muxes of different sizes). `rules` decides which function
    sits where.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: FabricCellRules = FabricCellRules()

    cells: dict[FabricCellFunction, Annotated[list[CellSpec], Field(min_length=1)]] = (
        MODELS_PACK_CELLS
    )

    @field_validator("cells")
    @classmethod
    def _fall_back_per_function(
        cls, value: dict[FabricCellFunction, list[CellSpec]]
    ) -> dict[FabricCellFunction, list[CellSpec]]:
        """Fill in the `models_pack` cells for each function not given.

        Parameters
        ----------
        value : dict[FabricCellFunction, list[CellSpec]]
            The cells given in the file.

        Returns
        -------
        dict[FabricCellFunction, list[CellSpec]]
            Cells for every function.
        """
        return {**MODELS_PACK_CELLS, **value}

    @classmethod
    def load(cls, project_dir: Path) -> Self:
        """Load the project's fabric cell library.

        Parameters
        ----------
        project_dir : Path
            Project directory, which may contain `Fabric/fabric_cell_library.yaml`.

        Returns
        -------
        Self
            The library: the file's cells, the `models_pack` ones for the rest,
            and the file's rules, loaded relative to it.
        """
        path = project_dir / FABRIC_CELL_LIBRARY_RELPATH
        data = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
        rules = data.get("rules") or {}
        data["rules"] = {
            kind: _load_rule(spec, path.parent) if isinstance(spec, str) else spec
            for kind, spec in rules.items()
        }
        return cls.model_validate(data)
