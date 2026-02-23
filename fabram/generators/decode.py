"""
Decoder SubcktDef builders.

DEC_2TO4 — 2-bit to 4-line active-HIGH decoder (used as predecoder group).
nand_dec  — scalable power-of-2 NAND decoder for any addr_bits 1..8.

Strategy
--------
Split address bits into groups of 2 (plus an optional 1-bit remainder):
  - Each full 2-bit group → DEC_2TO4 → 4 active-high predecode signals
  - 1-bit remainder: AB{top}=NOT(A{top}) and A{top} give 2 active-high signals
Final stage for each word i:
  - n_groups == 1, 2-bit: use DEC_2TO4 output directly → DC{i}
  - n_groups == 1, 1-bit: use NOT / double-NOT  → DC{i}
  - n_groups >= 2:         NAND_n(one signal per group) → NOT → DC{i}
"""
from __future__ import annotations
import math

from spice_gen.model.netlist import SubcktDef
from spice_gen.model.component import SubcktInstance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _si(inst: str, cell: str, pm: dict[str, str]) -> SubcktInstance:
    """Shorthand SubcktInstance constructor."""
    return SubcktInstance(instance_name=inst, subckt_name=cell, port_map=pm)


def _not(inst: str, a: str, b: str) -> SubcktInstance:
    return _si(inst, "NOT", {"VDD": "VDD", "VSS": "VSS", "A": a, "B": b})


def _nand2(inst: str, a: str, b: str, y: str) -> SubcktInstance:
    return _si(inst, "NAND2", {"VDD": "VDD", "VSS": "VSS", "A": a, "B": b, "Y": y})


def _nand3(inst: str, a: str, b: str, c: str, y: str) -> SubcktInstance:
    return _si(inst, "NAND3", {"VDD": "VDD", "VSS": "VSS", "A": a, "B": b, "C": c, "Y": y})


def _nand4(inst: str, a: str, b: str, c: str, d: str, y: str) -> SubcktInstance:
    return _si(inst, "NAND4", {"VDD": "VDD", "VSS": "VSS", "A": a, "B": b, "C": c, "D": d, "Y": y})


# ---------------------------------------------------------------------------
# DEC_2TO4 — 2-bit predecoder (active-high outputs)
# ---------------------------------------------------------------------------

def dec_2to4() -> SubcktDef:
    """
    2-to-4 binary decoder with active-high outputs.

    Ports: VDD VSS A1 A0 Y0 Y1 Y2 Y3
    Yi = 1  iff  {A1,A0} == i
    """
    ports = ["VDD", "VSS", "A1", "A0", "Y0", "Y1", "Y2", "Y3"]
    comps: list[SubcktInstance] = [
        _not("XNB0", "A0", "AB0"),
        _not("XNB1", "A1", "AB1"),
    ]
    # Yi = NOT(NAND2(A1_sel, A0_sel))
    # bit 0 of i selects A0 (1) or AB0 (0); bit 1 selects A1 or AB1
    for i in range(4):
        a0_sel = "A0" if (i & 1) else "AB0"
        a1_sel = "A1" if (i & 2) else "AB1"
        comps.append(_nand2(f"XNAND{i}", a1_sel, a0_sel, f"nand{i}"))
        comps.append(_not(f"XN{i}", f"nand{i}", f"Y{i}"))
    return SubcktDef(name="DEC_2TO4", ports=ports, components=comps)


# ---------------------------------------------------------------------------
# nand_dec — scalable power-of-2 decoder
# ---------------------------------------------------------------------------

def nand_dec(num_words: int) -> SubcktDef:
    """
    Build a ROW_DEC_{num_words} SubcktDef.

    Ports: VDD VSS A0..A{addr_bits-1} DC0..DC{num_words-1}

    Supports num_words = 2**n for n in 0..8
    (num_words=1 → trivially returns empty decoder, DC0 must be tied externally).
    """
    if num_words == 1:
        # Degenerate: single row always selected — caller handles DC0 externally
        return SubcktDef(name="ROW_DEC_1", ports=["VDD", "VSS", "DC0"], components=[])

    addr_bits = int(math.log2(num_words))
    ports = (
        ["VDD", "VSS"]
        + [f"A{i}" for i in range(addr_bits)]
        + [f"DC{i}" for i in range(num_words)]
    )
    comps: list[SubcktInstance] = []

    # ---- Special cases for addr_bits 1 and 2 ----------------------------

    if addr_bits == 1:
        # DC0 = NOT(A0),  DC1 = NOT(DC0) = A0
        comps.append(_not("XN0", "A0", "DC0"))
        comps.append(_not("XN1", "DC0", "DC1"))
        return SubcktDef(name=f"ROW_DEC_{num_words}", ports=ports, components=comps)

    if addr_bits == 2:
        # Use DEC_2TO4 directly; its outputs are the final DC lines
        comps.append(_si("XDEC", "DEC_2TO4", {
            "VDD": "VDD", "VSS": "VSS",
            "A1": "A1", "A0": "A0",
            "Y0": "DC0", "Y1": "DC1", "Y2": "DC2", "Y3": "DC3",
        }))
        return SubcktDef(name=f"ROW_DEC_{num_words}", ports=ports, components=comps)

    # ---- General case (addr_bits 3..8) ----------------------------------
    # Split into groups of 2 bits (+ optional 1-bit remainder at the top).

    n_full = addr_bits // 2          # number of full 2-bit groups
    has_odd = (addr_bits % 2) == 1   # is there a leftover top bit?
    n_groups = n_full + (1 if has_odd else 0)

    if n_groups > 4:
        raise ValueError(
            f"addr_bits={addr_bits} requires {n_groups} NAND inputs; "
            "max supported is 4 (addr_bits <= 8)."
        )

    # Invert the top (odd) bit if needed
    if has_odd:
        odd_bit = addr_bits - 1
        comps.append(_not("XNB_ODD", f"A{odd_bit}", f"AB{odd_bit}"))

    # Instantiate DEC_2TO4 for each full 2-bit group
    # group_nets[g] = list of 4 (or 2) net names, active-high
    group_nets: dict[int, list[str]] = {}

    for g in range(n_full):
        lo, hi = 2 * g, 2 * g + 1
        y_nets = [f"PD{g}_{k}" for k in range(4)]
        comps.append(_si(f"XDEC{g}", "DEC_2TO4", {
            "VDD": "VDD", "VSS": "VSS",
            "A1": f"A{hi}", "A0": f"A{lo}",
            "Y0": y_nets[0], "Y1": y_nets[1], "Y2": y_nets[2], "Y3": y_nets[3],
        }))
        group_nets[g] = y_nets

    if has_odd:
        g = n_full
        odd_bit = addr_bits - 1
        # two signals: index-0 selected when top bit=0, index-1 when top bit=1
        group_nets[g] = [f"AB{odd_bit}", f"A{odd_bit}"]

    # Final stage: for each word i, NAND{n_groups} selected predecodes → NOT → DC{i}
    for i in range(num_words):
        inputs: list[str] = []
        for g in range(n_groups):
            g_nets = group_nets[g]
            mask = len(g_nets) - 1          # 3 for 2-bit group, 1 for 1-bit group
            sel = (i >> (2 * g)) & mask
            inputs.append(g_nets[sel])

        nd_net = f"nd{i}"
        if n_groups == 2:
            comps.append(_nand2(f"XNAND{i}", inputs[0], inputs[1], nd_net))
        elif n_groups == 3:
            comps.append(_nand3(f"XNAND{i}", inputs[0], inputs[1], inputs[2], nd_net))
        elif n_groups == 4:
            comps.append(_nand4(f"XNAND{i}", inputs[0], inputs[1], inputs[2], inputs[3], nd_net))

        comps.append(_not(f"XDC{i}", nd_net, f"DC{i}"))

    return SubcktDef(name=f"ROW_DEC_{num_words}", ports=ports, components=comps)
