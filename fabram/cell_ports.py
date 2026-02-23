"""
CellPorts — port-name configuration for SRAM primitive cells.

All defaults match the port names used in ``cells/sram/*.yaml``.
Override individual fields when your cell library uses different names.

Example::

    from fabram import SRAMCompiler, CellPorts

    # Sense amp with INP/INN/Q instead of BL/BL_/SB
    cfg = CellPorts(sa_inp="INP", sa_inn="INN", sa_out="Q")
    netlist = SRAMCompiler(64, 8, col_mux=4, cell_ports=cfg).compile()
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CellPorts:
    """Port-name mapping for each SRAM primitive cell.

    Each field is the name of a port *as declared in the cell YAML*.
    The generator uses these names as keys when building port_maps, so
    changing a field here is the only thing needed when a cell library
    uses different port names.
    """

    # ── BIT_CELL / DMY_CELL ──────────────────────────────────────────────────
    cell_wl:  str = "WL"
    cell_bl:  str = "BL"
    cell_bl_: str = "BL_"

    # ── SENSE_AMP ────────────────────────────────────────────────────────────
    sa_en:  str = "SAEN"   # enable (active high)
    sa_inp: str = "BL"     # non-inverting bitline input
    sa_inn: str = "BL_"    # inverting bitline input
    sa_out: str = "SB"     # latched output

    # ── MS_REG (master-slave flip-flop) ──────────────────────────────────────
    reg_clk: str = "clk"
    reg_d:   str = "D"
    reg_q:   str = "Q"

    # ── DIDO (precharge + col-select + R/W pass) ──────────────────────────────
    dido_pchg: str = "PCHG"
    dido_wren: str = "WREN"
    dido_sel:  str = "SEL"
    dido_bl:   str = "BL"
    dido_bl_:  str = "BL_"
    dido_dw:   str = "DW"
    dido_dw_:  str = "DW_"
    dido_dr:   str = "DR"
    dido_dr_:  str = "DR_"

    # ── ROW_DRIVER ───────────────────────────────────────────────────────────
    drv_en:  str = "WLEN"  # word-line enable gate
    drv_in:  str = "A"     # decoded address input
    drv_out: str = "B"     # buffered word-line / select output

    # ── WRITE_DRIVER ─────────────────────────────────────────────────────────
    wd_en:  str = "WREN"   # write enable
    wd_in:  str = "Din"    # data input
    wd_dw:  str = "DW"     # differential write output (true)
    wd_dw_: str = "DW_"    # differential write output (complement)

    # ── SELF_TIMED_CTRL ──────────────────────────────────────────────────────
    ctrl_clk:   str = "clk"
    ctrl_cs:    str = "cs"
    ctrl_write: str = "write"
    ctrl_dbl:   str = "DBL"    # dummy bitline (true)
    ctrl_dbl_:  str = "DBL_"   # dummy bitline (complement)
    ctrl_wren:  str = "WREN"
    ctrl_pchg:  str = "PCHG"
    ctrl_wlen:  str = "WLEN"
    ctrl_saen:  str = "SAEN"
