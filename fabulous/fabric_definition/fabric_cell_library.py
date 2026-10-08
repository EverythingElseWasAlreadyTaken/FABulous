"""The FABulous fabric cells: the cells that implement the fabric's functions.

The model describes what a cell does (`FabricCellFunction`), the fabric cell
library which cells do it and through which ports, mirroring the PDK's
`StdCellLibrary` but kept apart from it. A project overrides the cells per
function in `Fabric/fabric_cell_library.yaml`; every function it leaves out, or the
whole file when absent, falls back to the cells of the shipped `models_pack`.
"""

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


class FabricCellLibrary(BaseModel):
    """The cells implementing the fabric's functions.

    `cells` holds the cells of each function, at least one; a function may list
    several (e.g. muxes of different sizes).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

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
            The library: the file's cells, the `models_pack` ones for the rest.
        """
        path = project_dir / FABRIC_CELL_LIBRARY_RELPATH
        data = yaml.safe_load(path.read_text()) if path.exists() else None
        return cls.model_validate(data or {})
