"""Configuration memory of a frame-based tile.

A tile latches its configuration bits at the crosspoints of `FrameData` and
`FrameStrobe` lines. Which logical bit sits at each crosspoint is recorded in
`<tile>_ConfigMem.csv`. `ConfigMem` is the only reader and writer of that file,
so the HDL generator and the bitstream specification read one encoding.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, NamedTuple, Self

from bitarray import bitarray

from fabulous.fabric_definition.define import IO
from fabulous.fabric_definition.port import ConfigBitPort, ConfigLogicPort

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from fabulous.fabric_definition.supertile import SuperTile
    from fabulous.fabric_definition.tile import Tile

CONFIG_MEM_CSV_FIELDS = (
    "frame_name",
    "frame_index",
    "bits_used_in_frame",
    "used_bits_mask",
    "ConfigBits_ranges",
)

NULL_RANGE = "# NULL"
"""The `ConfigBits_ranges` entry of a frame that holds no bit."""


class Crosspoint(NamedTuple):
    """A frame grid position: `FrameData[data_bit]` crossing `FrameStrobe[frame]`."""

    frame: int
    data_bit: int


@dataclass(frozen=True, eq=True)
class ConfigMemFrame:
    """One frame of the configuration memory, one row of the `ConfigMem.csv`.

    The CSV columns derive from `bits`. `used_bits_mask` reads from
    `FrameData[width - 1]` down to `FrameData[0]`, and its n-th `1` binds
    `config_bit_ranges[n]`.

    Attributes
    ----------
    frame_name : str
        The name of the frame
    frame_index : int
        The index of the frame
    width : int
        The number of `FrameData` lines the frame spans
    bits : dict[int, int]
        The logical configuration bit on each used `FrameData` line
    """

    frame_name: str
    frame_index: int
    width: int
    bits: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject a line outside the frame or a negative configuration bit."""
        for line, bit in self.bits.items():
            if not 0 <= line < self.width:
                raise ValueError(
                    f"{self.frame_name} holds a bit on FrameData[{line}], outside "
                    f"its {self.width} lines."
                )
            if bit < 0:
                raise ValueError(
                    f"Configuration bit index {bit} in {self.frame_name} is negative."
                )

    @property
    def lines(self) -> list[int]:
        """The used `FrameData` lines, highest line first, the mask read order."""
        return sorted(self.bits, reverse=True)

    @property
    def used_bits_mask(self) -> bitarray:
        """Which `FrameData` lines are used, from the highest line down."""
        mask = bitarray(self.width)
        mask.setall(0)
        for line in self.bits:
            mask[self.width - 1 - line] = 1
        return mask

    @property
    def config_bit_ranges(self) -> list[int]:
        """The bits of the used lines, in mask order, the `ConfigBits_ranges` column."""
        return [self.bits[line] for line in self.lines]


def _parse_ranges(text: str) -> list[int]:
    """Read a `ConfigBits_ranges` entry: `a:b`, `x;y;z`, one integer or NULL."""
    text = text.replace(" ", "").replace("\t", "")
    if ":" in text:
        left, right = (int(v) for v in text.split(":"))
        if right < left:
            return list(range(left, right - 1, -1))
        return list(range(left, right + 1))
    if ";" in text:
        return [int(item) for item in text.split(";")]
    if text.isdigit():
        return [int(text)]
    if "NULL" in text:
        return []
    raise ValueError(
        f"Range {text} is not a valid format. It should be in the form "
        "[int]:[int] or [int]. If there are multiple ranges it should be "
        "separated by ';'."
    )


def _format_ranges(bits: list[int]) -> str:
    """Write bits as `hi:lo` for a descending run, else `;`-separated."""
    if not bits:
        return NULL_RANGE
    if len(bits) > 1 and bits == list(range(bits[0], bits[0] - len(bits), -1)):
        return f"{bits[0]}:{bits[-1]}"
    return ";".join(str(bit) for bit in bits)


@dataclass(eq=False)
class ConfigMem:
    """The configuration memory of a tile or supertile.

    One instance in its holder's RTL: it reads the frames and drives the
    holder's configuration bits (`ports`). And the mapping of those bits onto
    the frame grid, one frame per `FrameStrobe` line (`frames`).

    A holder's memory is built once and never replaced. Its holder and frame
    grid are set when they are known; the mapping is read on first use: from
    `source` if the file exists, else the master's free crosspoints for a
    supertile, else the default packing (`default`). A memory without a frame
    grid (a FLIPFLOP_CHAIN fabric) holds no frames and has no ports. A memory
    built with `given` frames (`default`, `rebuilt_with`) holds exactly those.
    A memory is one object of the model, compared by identity.

    Attributes
    ----------
    given : tuple[ConfigMemFrame, ...] | None
        The frames, or None (the default) to read them on first use.
    frame_bits_per_row : int
        Width of every frame, the number of `FrameData` lines; 0 (the default)
        without a frame grid.
    max_frames_per_col : int
        Number of frames, the number of `FrameStrobe` lines; 0 (the default)
        without a frame grid.
    source : Path
        The CSV the memory is read from and written to, which need not exist
        yet. Keyword only.
    """

    given: tuple[ConfigMemFrame, ...] | None = None
    frame_bits_per_row: int = 0
    max_frames_per_col: int = 0
    source: Path = field(kw_only=True)
    _holder: Tile | SuperTile | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Check `given` frames against the grid at once."""
        if self.given is not None:
            self._check_grid(self.given)

    def __str__(self) -> str:
        """Name the memory in a message by the file it belongs to."""
        return f"bitstream mapping file {self.source}"

    @property
    def holder(self) -> Tile | SuperTile | None:
        """The tile or supertile the memory belongs to, or None while unattached."""
        return self._holder

    @holder.setter
    def holder(self, holder: Tile | SuperTile) -> None:
        if self._holder is not None and self._holder is not holder:
            raise ValueError(f"{self} already belongs to {self._holder.name}")
        self._holder = holder

    def set_grid(self, frame_bits_per_row: int, max_frames_per_col: int) -> None:
        """Give the memory its frame grid, the fabric's frame sizes.

        Parameters
        ----------
        frame_bits_per_row : int
            The fabric's `FrameBitsPerRow`.
        max_frames_per_col : int
            The fabric's `MaxFramesPerCol`.

        Raises
        ------
        ValueError
            If the mapping or the ports were already derived on another grid.
        """
        grid = (frame_bits_per_row, max_frames_per_col)
        if grid == (self.frame_bits_per_row, self.max_frames_per_col):
            return
        if {"frames", "ports"} & self.__dict__.keys():
            raise ValueError(f"{self} is already in use on another frame grid.")
        self.frame_bits_per_row, self.max_frames_per_col = grid

    @property
    def module_name(self) -> str:
        """The name of the memory's RTL module."""
        return f"{self._attached().name}_ConfigMem"

    @property
    def instance_name(self) -> str:
        """The instance name of the memory in its holder's RTL."""
        return f"Inst_{self.module_name}"

    @cached_property
    def ports(self) -> tuple[ConfigLogicPort | ConfigBitPort, ...]:
        """The ports of the memory instance.

        Returns
        -------
        tuple[ConfigLogicPort | ConfigBitPort, ...]
            The frame inputs `FrameData`, `FrameStrobe`, then the bits
            `ConfigBits`, `ConfigBits_N`; none without a frame grid or
            configuration bits.
        """
        bits = self.holder_bits() if self.frame_bits_per_row else 0
        if not bits:
            return ()
        return (
            ConfigLogicPort("FrameData", IO.INPUT, self.frame_bits_per_row, self),
            ConfigLogicPort("FrameStrobe", IO.INPUT, self.max_frames_per_col, self),
            ConfigBitPort("ConfigBits", IO.OUTPUT, bits, self),
            ConfigBitPort("ConfigBits_N", IO.OUTPUT, bits, self),
        )

    @cached_property
    def frames(self) -> tuple[ConfigMemFrame, ...]:
        """Every frame in index order, empty ones included; read on first use.

        Returns
        -------
        tuple[ConfigMemFrame, ...]
            The frames; none without a frame grid.
        """
        frames = self.given if self.given is not None else self._load()
        self._check(frames)
        return frames

    def _attached(self) -> Tile | SuperTile:
        """Return the holder, which the memory must have here."""
        if self._holder is None:
            raise ValueError(f"{self} belongs to no tile or supertile")
        return self._holder

    def holder_bits(self) -> int:
        """Return the number of configuration bits of the holder."""
        # Imported here: supertile.py imports this module.
        from fabulous.fabric_definition.supertile import SuperTile

        holder = self._attached()
        if isinstance(holder, SuperTile):
            return holder.total_config_bits
        return holder.globalConfigBits

    def _master(self) -> ConfigMem | None:
        """Return the master tile's memory for a supertile, else None."""
        from fabulous.fabric_definition.supertile import SuperTile

        holder = self._holder
        if not isinstance(holder, SuperTile):
            return None
        mx, my = holder.get_master_tile_coords()
        return holder.tileMap[my][mx].config_mem

    def _load(self) -> tuple[ConfigMemFrame, ...]:
        """Read the frames: the file, the master's free crosspoints, or the default."""
        if not self.frame_bits_per_row:
            return ()
        if self.source.is_file():
            return _read_frames(self.source)
        bits = self.holder_bits()
        master = self._master()
        if master is None:
            return _default_frames(
                bits, self.frame_bits_per_row, self.max_frames_per_col
            )
        free = master.free_crosspoints
        if len(free) < bits:
            raise ValueError(
                f"Not enough free config bit slots in master tile ({master}): "
                f"need {bits}, found only {len(free)} free slots."
            )
        return master.rebuilt_with(
            {slot: bit for bit, slot in enumerate(free[:bits])}
        ).frames

    def _check_grid(self, frames: tuple[ConfigMemFrame, ...]) -> None:
        """Reject frames that do not fit the grid.

        Parameters
        ----------
        frames : tuple[ConfigMemFrame, ...]
            The frames to hold.

        Raises
        ------
        ValueError
            If there are not `max_frames_per_col` frames indexed 0 .. n-1 in
            order, if one is not `frame_bits_per_row` wide, or if a
            configuration bit is allocated twice.
        """
        if len(frames) != self.max_frames_per_col:
            raise ValueError(
                f"{self} has {len(frames)} entries but "
                f"MaxFramesPerCol is {self.max_frames_per_col}."
            )
        for position, frame in enumerate(frames):
            if frame.frame_index != position:
                raise ValueError(
                    f"{self} frames must be indexed 0 .. n-1 in order, got "
                    f"{frame.frame_name} with index {frame.frame_index} at "
                    f"position {position}."
                )
            if frame.width != self.frame_bits_per_row:
                raise ValueError(
                    f"{self} spans {frame.frame_name} over {frame.width} data "
                    f"lines, not the {self.frame_bits_per_row} of FrameBitsPerRow."
                )
        allocations = Counter(bit for frame in frames for bit in frame.bits.values())
        twice = sorted(bit for bit, count in allocations.items() if count > 1)
        if twice:
            raise ValueError(
                f"Configuration bit index {twice} allocated more than once in {self}."
            )

    def _check(self, frames: tuple[ConfigMemFrame, ...]) -> None:
        """Reject frames that do not fit the grid, the holder or the master.

        Parameters
        ----------
        frames : tuple[ConfigMemFrame, ...]
            The frames to hold.

        Raises
        ------
        ValueError
            If the frames do not fit the grid (`_check_grid`), if the memory
            holds another number of bits than its holder, or if a supertile's
            bit sits where its master tile's does.
        """
        self._check_grid(frames)
        if self._holder is None or not frames:
            return
        bits = sum(len(frame.bits) for frame in frames)
        need = self.holder_bits()
        if bits != need:
            raise ValueError(
                f"{self} maps {bits} bits but {self._holder.name} has {need}. "
                "Delete the file to regenerate it."
            )
        master = self._master()
        if master is None:
            return
        for frame, master_frame in zip(frames, master.frames, strict=True):
            overlap = frame.used_bits_mask & master_frame.used_bits_mask
            conflicts = [k for k, bit in enumerate(overlap) if bit]
            if conflicts:
                raise ValueError(
                    f"Supertile ConfigMem {self} conflicts with the master tile "
                    f"ConfigMem {master} in frame {frame.frame_index} at bit "
                    f"position(s) {conflicts}: both drive the same physical "
                    "config bit. Delete the supertile ConfigMem to regenerate it."
                )

    @property
    def used_frames(self) -> tuple[ConfigMemFrame, ...]:
        """The frames holding at least one bit, in index order."""
        return tuple(frame for frame in self.frames if frame.bits)

    @property
    def config_bits(self) -> int:
        """Number of bits the memory holds."""
        return sum(len(frame.bits) for frame in self.frames)

    @property
    def frame_names(self) -> dict[int, str]:
        """Frame name per frame index."""
        return {frame.frame_index: frame.frame_name for frame in self.frames}

    @property
    def bit_at(self) -> dict[Crosspoint, int]:
        """The logical bit at each occupied crosspoint, in frame then mask order."""
        return {
            Crosspoint(frame.frame_index, line): frame.bits[line]
            for frame in self.frames
            for line in frame.lines
        }

    @property
    def free_crosspoints(self) -> list[Crosspoint]:
        """Unoccupied crosspoints in frame then mask order, highest data line first."""
        return [
            Crosspoint(frame.frame_index, line)
            for frame in self.frames
            for line in range(self.frame_bits_per_row - 1, -1, -1)
            if line not in frame.bits
        ]

    def to_csv(self) -> None:
        """Write the memory to `source`, creating the directory.

        Masks are grouped in fours with `_`. A descending run of bits is written
        `hi:lo` and any other set `;`-separated, so the file reads back the
        same.
        """
        frames = self.frames
        self.source.parent.mkdir(parents=True, exist_ok=True)
        with self.source.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CONFIG_MEM_CSV_FIELDS)
            for frame in frames:
                writer.writerow(
                    [
                        frame.frame_name,
                        frame.frame_index,
                        len(frame.bits),
                        frame.used_bits_mask.to01(group=4, sep="_"),
                        _format_ranges(frame.config_bit_ranges),
                    ]
                )

    @classmethod
    def default(
        cls,
        config_bits: int,
        *,
        frame_bits_per_row: int,
        max_frames_per_col: int,
        source: Path,
    ) -> Self:
        """Pack the bits from the highest down, filling frame 0 first.

        With 100 bits on 32-bit frames, frame 0 holds bits 99 to 68 on
        `FrameData[31]` to `FrameData[0]`, and frame 3 holds bits 3 to 0 on its
        top four lines.

        Parameters
        ----------
        config_bits : int
            The number of bits to place.
        frame_bits_per_row : int
            Width of every frame.
        max_frames_per_col : int
            Number of frames.
        source : Path
            The file the mapping belongs in, which need not exist yet.

        Returns
        -------
        Self
            The enumerated mapping, empty frames included.
        """
        frames = _default_frames(config_bits, frame_bits_per_row, max_frames_per_col)
        return cls(frames, frame_bits_per_row, max_frames_per_col, source=source)

    def rebuilt_with(self, bit_at: Mapping[Crosspoint, int]) -> Self:
        """Return a memory on the same grid and frame names holding `bit_at`.

        Parameters
        ----------
        bit_at : Mapping[Crosspoint, int]
            The logical bit of every occupied crosspoint.

        Returns
        -------
        Self
            One frame per index, empty frames included.

        Raises
        ------
        ValueError
            If the bits are not exactly `0 .. n-1`, or if a crosspoint lies
            outside the grid.
        """
        bits = sorted(bit_at.values())
        if bits != list(range(len(bits))):
            raise ValueError(f"Mapped bits are not a contiguous range from 0: {bits}")
        for crosspoint in bit_at:
            if not (
                0 <= crosspoint.frame < self.max_frames_per_col
                and 0 <= crosspoint.data_bit < self.frame_bits_per_row
            ):
                raise ValueError(
                    f"Crosspoint {crosspoint} lies outside the "
                    f"{self.max_frames_per_col} x {self.frame_bits_per_row} frame grid"
                )
        frames = tuple(
            ConfigMemFrame(
                frame.frame_name,
                frame.frame_index,
                self.frame_bits_per_row,
                {
                    crosspoint.data_bit: bit
                    for crosspoint, bit in bit_at.items()
                    if crosspoint.frame == frame.frame_index
                },
            )
            for frame in self.frames
        )
        return type(self)(
            frames, self.frame_bits_per_row, self.max_frames_per_col, source=self.source
        )


def _read_frames(path: Path) -> tuple[ConfigMemFrame, ...]:
    """Read the frames of a `ConfigMem.csv`, in `frame_index` order.

    Parameters
    ----------
    path : Path
        The CSV to read.

    Returns
    -------
    tuple[ConfigMemFrame, ...]
        The frames.

    Raises
    ------
    ValueError
        If a frame index or range entry is malformed, or if a mask and its
        ranges disagree in count.
    """
    with path.absolute().open() as f:
        rows = list(csv.DictReader(f))
    try:
        rows.sort(key=lambda row: int(row["frame_index"]))
    except ValueError as exc:
        raise ValueError(f"{path}: frame_index is not a number: {exc}") from exc
    frames: list[ConfigMemFrame] = []
    for row in rows:
        mask = row["used_bits_mask"].replace("_", "")
        try:
            ranges = _parse_ranges(row["ConfigBits_ranges"])
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        lines = [len(mask) - 1 - k for k, char in enumerate(mask) if char == "1"]
        if len(lines) != len(ranges):
            raise ValueError(
                f"{path}: frame {row['frame_name']} marks {len(lines)} data "
                f"lines used but lists {len(ranges)} configuration bits; the "
                "n-th 1 of the mask binds the n-th bit of the range."
            )
        frames.append(
            ConfigMemFrame(
                frame_name=row["frame_name"],
                frame_index=int(row["frame_index"]),
                width=len(mask),
                bits=dict(zip(lines, ranges, strict=True)),
            )
        )
    return tuple(frames)


def _default_frames(
    config_bits: int, frame_bits_per_row: int, max_frames_per_col: int
) -> tuple[ConfigMemFrame, ...]:
    """Pack the bits from the highest down, filling frame 0 first.

    Parameters
    ----------
    config_bits : int
        The number of bits to place.
    frame_bits_per_row : int
        Width of every frame.
    max_frames_per_col : int
        Number of frames.

    Returns
    -------
    tuple[ConfigMemFrame, ...]
        One frame per index, empty frames included.

    Raises
    ------
    ValueError
        If the grid cannot hold the bits.
    """
    capacity = frame_bits_per_row * max_frames_per_col
    if config_bits > capacity:
        raise ValueError(
            f"Tile config bits ({config_bits}) exceed fabric capacity "
            f"({capacity} bits). Please adjust the tile configuration."
        )
    # One shared iterator, so each frame takes the bits the last one left.
    remaining = iter(range(config_bits - 1, -1, -1))
    return tuple(
        ConfigMemFrame(
            f"frame{index}",
            index,
            frame_bits_per_row,
            dict(zip(range(frame_bits_per_row - 1, -1, -1), remaining, strict=False)),
        )
        for index in range(max_frames_per_col)
    )
