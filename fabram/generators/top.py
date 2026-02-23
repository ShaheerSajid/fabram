"""
SRAMCompiler — assembles a full SRAM macro Netlist from words/bits/col_mux.
"""
from __future__ import annotations

import pathlib
from typing import Sequence

from spice_gen.model.netlist import Netlist, SubcktDef, PdkInclude
from spice_gen.model.component import SubcktInstance
from spice_gen.parser.loader import load_file
from spice_gen.pdk.pdk_config import PdkConfig
from spice_gen.pdk.resolver import resolve

from fabram.cell_ports import CellPorts
from fabram.geometry import ArrayGeometry
from fabram.generators.decode import dec_2to4, nand_dec
from fabram.generators.arrays import (
    cell_row, mat_array, dmy_array, se_array,
    dido_array, drv_arr, input_reg_arr, datain_arr,
)

_CELLS_DIR = pathlib.Path(__file__).parent.parent.parent / "cells"
_PDK_YAML  = pathlib.Path(__file__).parent.parent.parent / "vendor" / "spice_gen" / "pdks" / "sky130A.yaml"


def _si(inst: str, cell: str, pm: dict[str, str]) -> SubcktInstance:
    return SubcktInstance(instance_name=inst, subckt_name=cell, port_map=pm)


class SRAMCompiler:
    """Compile a synchronous single-port SRAM macro."""

    _CELL_YAMLS = [
        "primitives/not.yaml",
        "primitives/nand2.yaml",
        "primitives/nand3.yaml",
        "primitives/nand4.yaml",
        "sram/bit_cell.yaml",
        "sram/dmy_cell.yaml",
        "sram/sense_amp.yaml",
        "sram/ms_reg.yaml",
        "sram/dido.yaml",
        "sram/row_driver.yaml",
        "sram/write_driver.yaml",
        "sram/self_timed_ctrl.yaml",
    ]

    def __init__(self, words: int, bits: int, col_mux: int = 1,
                 cell_ports: CellPorts | None = None) -> None:
        self.geo = ArrayGeometry(words, bits, col_mux)
        self.cfg = cell_ports if cell_ports is not None else CellPorts()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(self, pdk_corner: str = "tt") -> Netlist:
        """Return a fully resolved Netlist for this SRAM configuration."""
        geo = self.geo
        all_defs: list[SubcktDef] = []
        seen: set[str] = set()

        def add(defs: Sequence[SubcktDef]) -> None:
            for d in defs:
                if d.name not in seen:
                    all_defs.append(d)
                    seen.add(d.name)

        # ---- 1. Load cell templates -----------------------------------
        for rel in self._CELL_YAMLS:
            add(load_file(_CELLS_DIR / rel).subckt_defs)

        # ---- 2. Decoder subcircuits ----------------------------------
        # DEC_2TO4 is needed by nand_dec whenever addr_bits >= 2
        if geo.row_addr_bits >= 2 or geo.col_addr_bits >= 2:
            add([dec_2to4()])

        if geo.num_rows > 1:
            add([nand_dec(geo.num_rows)])

        if geo.col_mux > 1:
            # Column decoder (reuses the same nand_dec; different num if col_mux != num_rows)
            if geo.col_mux != geo.num_rows:
                # DEC_2TO4 may already be added; nand_dec produces a distinct name
                if geo.col_addr_bits >= 2 and "DEC_2TO4" not in seen:
                    add([dec_2to4()])
                add([nand_dec(geo.col_mux)])

        # ---- 3. Array / peripheral subcircuits -----------------------
        cfg = self.cfg
        add([cell_row(geo.num_cols, cfg)])
        add([mat_array(geo.num_rows, geo.num_cols, cfg)])
        add([dmy_array(geo.num_rows, cfg)])
        add([se_array(geo.bits, cfg)])
        add([dido_array(geo.num_cols, geo.col_mux, geo.bits, cfg)])

        # Row driver array: DC{i} → WL{i}
        add([drv_arr(geo.num_rows, "DC", "WL", f"ROW_DRV_ARR_{geo.num_rows}", cfg)])

        # Column driver array (only when col_mux > 1): CD{k} → SEL{k}
        if geo.col_mux > 1:
            col_drv_name = f"COL_DRV_ARR_{geo.col_mux}"
            if col_drv_name not in seen:
                add([drv_arr(geo.col_mux, "CD", "SEL", col_drv_name, cfg)])

        # Input register: addr[n-1:0] + write_en
        add([input_reg_arr(geo.addr_bits + 1, cfg)])

        # Data-in array: MS_REG + WRITE_DRIVER per bit
        add([datain_arr(geo.bits, cfg)])

        # ---- 4. Top-level subcircuit ---------------------------------
        add([self._build_top(geo, cfg)])

        # ---- 5. Resolve PDK (model name → sky130 subckt names) -------
        unresolved = Netlist(subckt_defs=all_defs, top_cell=geo.name)
        pdk = PdkConfig.model_validate(
            __import__("yaml").safe_load(_PDK_YAML.read_text())
        )
        pdk_inc = PdkInclude(lib_file=str(pdk.lib_path), corner=pdk_corner)
        resolved = resolve(unresolved, pdk, corner=pdk_corner)
        return resolved

    # ------------------------------------------------------------------
    # Internal: top-level subcircuit
    # ------------------------------------------------------------------

    def _build_top(self, geo: ArrayGeometry, cfg: CellPorts | None = None) -> SubcktDef:
        if cfg is None:
            cfg = self.cfg
        # Ports (MSB→LSB for buses, matching standard SRAM interface)
        ports = ["VDD", "VSS", "CLK", "CS", "WRITE"]
        ports += [f"addr{i}" for i in range(geo.addr_bits - 1, -1, -1)]
        ports += [f"din{k}"  for k in range(geo.bits - 1, -1, -1)]
        ports += [f"Q{k}"    for k in range(geo.bits - 1, -1, -1)]

        comps: list[SubcktInstance] = []

        # -- Input register -------------------------------------------
        # Captures addr[n-1:0] and WRITE on CLK edge.
        # INPUT_REG_{n+1} ports: VDD VSS clk D0..D{n} Q0..Q{n}
        #   D0..D{n-1} = addr0..addr{n-1}, D{n} = WRITE
        #   Q0..Q{n-1} = A0..A{n-1},       Q{n} = write_r
        n_inreg = geo.addr_bits + 1
        inreg_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS", "clk": "CLK"}
        for i in range(geo.addr_bits):
            inreg_pm[f"D{i}"] = f"addr{i}"
        inreg_pm[f"D{geo.addr_bits}"] = "WRITE"
        for i in range(geo.addr_bits):
            inreg_pm[f"Q{i}"] = f"A{i}"
        inreg_pm[f"Q{geo.addr_bits}"] = "write_r"
        comps.append(_si("XINREG", f"INPUT_REG_{n_inreg}", inreg_pm))

        # -- Control block --------------------------------------------
        # SELF_TIMED_CTRL: VDD VSS clk cs write DBL DBL_ WREN PCHG WLEN SAEN
        comps.append(_si("XCTRL", "SELF_TIMED_CTRL", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.ctrl_clk:   "CLK",     cfg.ctrl_cs:    "CS",
            cfg.ctrl_write: "write_r",
            cfg.ctrl_dbl:   "DBL",     cfg.ctrl_dbl_:  "DBL_",
            cfg.ctrl_wren:  "WREN",    cfg.ctrl_pchg:  "PCHG",
            cfg.ctrl_wlen:  "WLEN",    cfg.ctrl_saen:  "SAEN",
        }))

        # -- Row decoder + row driver array ---------------------------
        if geo.num_rows == 1:
            # Single row: WL0 is driven directly from WLEN via a ROW_DRIVER
            # Use the row driver array with DC0 tied to VDD (always decode)
            # ROW_DRV_ARR_1 ports: VDD VSS WLEN DC0 WL0
            comps.append(_si("XRDRVR", f"ROW_DRV_ARR_{geo.num_rows}", {
                "VDD": "VDD", "VSS": "VSS", "WLEN": "WLEN",
                "DC0": "VDD",   # always selected
                "WL0": "WL0",
            }))
        else:
            # ROW_DEC_{num_rows}: VDD VSS A0..A{row_addr_bits-1} DC0..DC{num_rows-1}
            rdec_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS"}
            for i in range(geo.row_addr_bits):
                rdec_pm[f"A{i}"] = f"A{i}"          # lower addr bits → row decode
            for i in range(geo.num_rows):
                rdec_pm[f"DC{i}"] = f"DC{i}"
            comps.append(_si("XRDEC", f"ROW_DEC_{geo.num_rows}", rdec_pm))

            # ROW_DRV_ARR_{num_rows}: VDD VSS WLEN DC0..DC{r-1} WL0..WL{r-1}
            rdrvr_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS", "WLEN": "WLEN"}
            for i in range(geo.num_rows):
                rdrvr_pm[f"DC{i}"] = f"DC{i}"
                rdrvr_pm[f"WL{i}"] = f"WL{i}"
            comps.append(_si("XRDRVR", f"ROW_DRV_ARR_{geo.num_rows}", rdrvr_pm))

        # -- Column decoder + column driver array ----------------------
        if geo.col_mux == 1:
            # Single column group: SEL0 = WLEN (active when word-line is enabled)
            # Handled in DIDO instantiation below (SEL0 = WLEN)
            pass
        else:
            # COL_DEC uses the upper col_addr_bits of the address.
            # A{row_addr_bits} .. A{addr_bits-1} are the column address bits.
            cdec_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS"}
            for i in range(geo.col_addr_bits):
                cdec_pm[f"A{i}"] = f"A{geo.row_addr_bits + i}"  # upper addr bits
            for k in range(geo.col_mux):
                cdec_pm[f"DC{k}"] = f"CD{k}"
            comps.append(_si("XCDEC", f"ROW_DEC_{geo.col_mux}", cdec_pm))

            # COL_DRV_ARR_{col_mux}: VDD VSS WLEN CD0..CD{m-1} SEL0..SEL{m-1}
            cdrvr_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS", "WLEN": "WLEN"}
            for k in range(geo.col_mux):
                cdrvr_pm[f"CD{k}"] = f"CD{k}"    # port=CD{k} (in_prefix="CD")
                cdrvr_pm[f"SEL{k}"] = f"SEL{k}"  # port=SEL{k} (out_prefix="SEL")
            comps.append(_si("XCDRVR", f"COL_DRV_ARR_{geo.col_mux}", cdrvr_pm))

        # -- Bitcell matrix -------------------------------------------
        # MAT_{r}x{c}: VDD VSS BL0 BL_0 ... WL0 ... WL{r-1}
        mat_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS"}
        for j in range(geo.num_cols):
            mat_pm[f"BL{j}"] = f"BL{j}"
            mat_pm[f"BL_{j}"] = f"BL_{j}"
        for i in range(geo.num_rows):
            mat_pm[f"WL{i}"] = f"WL{i}"
        comps.append(_si("XMAT", f"MAT_{geo.num_rows}x{geo.num_cols}", mat_pm))

        # -- Dummy row ------------------------------------------------
        # DMY_ARR_{r}: VDD VSS WL0... DBL DBL_
        dmy_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS", "DBL": "DBL", "DBL_": "DBL_"}
        for i in range(geo.num_rows):
            dmy_pm[f"WL{i}"] = f"WL{i}"
        comps.append(_si("XDMY", f"DMY_ARR_{geo.num_rows}", dmy_pm))

        # -- DIDO array -----------------------------------------------
        # DIDO_ARR_{c}: VDD VSS PCHG WREN SEL0... BL0 BL_0... DW0 DW_0... DR0 DR_0...
        dido_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS", "PCHG": "PCHG", "WREN": "WREN"}
        if geo.col_mux == 1:
            dido_pm["SEL0"] = "WLEN"    # always selected when word-line active
        else:
            for k in range(geo.col_mux):
                dido_pm[f"SEL{k}"] = f"SEL{k}"
        for j in range(geo.num_cols):
            dido_pm[f"BL{j}"] = f"BL{j}"
            dido_pm[f"BL_{j}"] = f"BL_{j}"
        for k in range(geo.bits):
            dido_pm[f"DW{k}"] = f"DW{k}"
            dido_pm[f"DW_{k}"] = f"DW_{k}"
        for k in range(geo.bits):
            dido_pm[f"DR{k}"] = f"DR{k}"
            dido_pm[f"DR_{k}"] = f"DR_{k}"
        comps.append(_si("XDIDO", f"DIDO_ARR_{geo.num_cols}", dido_pm))

        # -- Data-input array (MS_REG + WRITE_DRIVER per bit) ----------
        # DATAIN_ARR_{bits}: VDD VSS clk WREN din0... DW0 DW_0...
        datain_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS", "clk": "CLK", "WREN": "WREN"}
        for k in range(geo.bits):
            datain_pm[f"din{k}"] = f"din{k}"
        for k in range(geo.bits):
            datain_pm[f"DW{k}"] = f"DW{k}"
            datain_pm[f"DW_{k}"] = f"DW_{k}"
        comps.append(_si("XDATAIN", f"DATAIN_ARR_{geo.bits}", datain_pm))

        # -- Sense amp array ------------------------------------------
        # SE_ARR_{bits}: VDD VSS SAEN DR0 DR_0... Q0...
        se_pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS", "SAEN": "SAEN"}
        for k in range(geo.bits):
            se_pm[f"DR{k}"] = f"DR{k}"
            se_pm[f"DR_{k}"] = f"DR_{k}"
        for k in range(geo.bits):
            se_pm[f"Q{k}"] = f"Q{k}"
        comps.append(_si("XSE", f"SE_ARR_{geo.bits}", se_pm))

        return SubcktDef(name=geo.name, ports=ports, components=comps)
