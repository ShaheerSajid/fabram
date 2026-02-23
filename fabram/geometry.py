from __future__ import annotations
import math
from dataclasses import dataclass


def _is_power_of_2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


@dataclass
class ArrayGeometry:
    words: int
    bits: int
    col_mux: int

    def __post_init__(self) -> None:
        if not _is_power_of_2(self.words):
            raise ValueError(f"words={self.words} must be a power of 2")
        if not _is_power_of_2(self.col_mux):
            raise ValueError(f"col_mux={self.col_mux} must be a power of 2")
        if self.col_mux > self.words:
            raise ValueError(f"col_mux={self.col_mux} must be <= words={self.words}")
        if self.bits < 1:
            raise ValueError(f"bits={self.bits} must be >= 1")

    @property
    def num_rows(self) -> int:
        return self.words // self.col_mux

    @property
    def num_cols(self) -> int:
        return self.bits * self.col_mux

    @property
    def addr_bits(self) -> int:
        return int(math.log2(self.words)) if self.words > 1 else 0

    @property
    def row_addr_bits(self) -> int:
        return int(math.log2(self.num_rows)) if self.num_rows > 1 else 0

    @property
    def col_addr_bits(self) -> int:
        return int(math.log2(self.col_mux)) if self.col_mux > 1 else 0

    @property
    def name(self) -> str:
        return f"SRAM_{self.words}x{self.bits}_CM{self.col_mux}"
