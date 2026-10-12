"""A cell: a module instantiated by name, and its ports.

Used for the PDK standard cells (`std_cell_library`) and for the fabric
cells (`fabric_cell_library`).
"""

from pydantic import BaseModel, ConfigDict


class CellSpec(BaseModel):
    """A cell and its ports.

    ``cell`` is the module name, a PDK standard cell or a fabric cell;
    ``input_ports`` and ``output_ports`` list its ports (a buffer has one of
    each, a tie cell only an output, and cells with several inputs/outputs list
    them all). Add further fields -- drive strength, area, ... -- as more cell
    metadata is needed.
    """

    model_config = ConfigDict(frozen=True)

    cell: str
    input_ports: tuple[str, ...] = ()
    output_ports: tuple[str, ...] = ()

    @property
    def yosys_arg(self) -> str:
        """Render the cell and its ports as a Yosys cell-mapping argument.

        Returns
        -------
        str
            The cell followed by its input ports then output ports, as Yosys
            options such as ``insbuf -buf`` and ``hilomap`` expect: a buffer
            renders ``"buf_1 A X"``, a tie cell ``"conb_1 HI"``.
        """
        return " ".join((self.cell, *self.input_ports, *self.output_ports))
