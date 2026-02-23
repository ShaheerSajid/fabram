"""
Array SubcktDef builders.

Each function returns a single SubcktDef (not a full Netlist).
Dependencies (cell templates, DEC_2TO4, etc.) must already be present
in the Netlist when the generator emits SPICE.

Naming conventions (matching FabRAM reference)
-----------------------------------------------
BL{j} / BL_{j}  — physical bitline pair for column j
WL{i}           — word line for physical row i
DW{k} / DW_{k}  — write data differential for data bit k
DR{k} / DR_{k}  — read  data differential for data bit k
SEL{m}          — column select for mux group m  (active HIGH)
DC{i}           — decoded row-driver input        (active HIGH)
WL{i}           — row-driver output = word line   (active HIGH)
"""
from __future__ import annotations

from spice_gen.model.netlist import SubcktDef
from spice_gen.model.component import SubcktInstance

from fabram.cell_ports import CellPorts

_DEFAULT = CellPorts()


def _si(inst: str, cell: str, pm: dict[str, str]) -> SubcktInstance:
    return SubcktInstance(instance_name=inst, subckt_name=cell, port_map=pm)


# ---------------------------------------------------------------------------
# Bitcell array
# ---------------------------------------------------------------------------

def cell_row(num_cols: int, cfg: CellPorts = _DEFAULT) -> SubcktDef:
    """
    Single row of BIT_CELL instances sharing one WL.

    Ports: VDD VSS WL BL0 BL_0 BL1 BL_1 ... BL{n-1} BL_{n-1}
    """
    ports = ["VDD", "VSS", "WL"]
    for j in range(num_cols):
        ports += [f"BL{j}", f"BL_{j}"]

    comps = [
        _si(f"XCELL{j}", "BIT_CELL", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.cell_wl: "WL", cfg.cell_bl: f"BL{j}", cfg.cell_bl_: f"BL_{j}",
        })
        for j in range(num_cols)
    ]
    return SubcktDef(name=f"CELL_ROW_{num_cols}", ports=ports, components=comps)


def mat_array(num_rows: int, num_cols: int, cfg: CellPorts = _DEFAULT) -> SubcktDef:
    """
    Memory matrix: stacks num_rows CELL_ROW_{num_cols} instances.

    Ports: VDD VSS BL0 BL_0 ... BL{c-1} BL_{c-1} WL0 ... WL{r-1}
    All rows share the same bitlines; each row has its own WL.
    """
    ports = ["VDD", "VSS"]
    for j in range(num_cols):
        ports += [f"BL{j}", f"BL_{j}"]
    for i in range(num_rows):
        ports.append(f"WL{i}")

    comps = []
    for i in range(num_rows):
        pm: dict[str, str] = {"VDD": "VDD", "VSS": "VSS", "WL": f"WL{i}"}
        for j in range(num_cols):
            pm[f"BL{j}"] = f"BL{j}"
            pm[f"BL_{j}"] = f"BL_{j}"
        comps.append(_si(f"XROW{i}", f"CELL_ROW_{num_cols}", pm))

    return SubcktDef(name=f"MAT_{num_rows}x{num_cols}", ports=ports, components=comps)


# ---------------------------------------------------------------------------
# Dummy bitcell array
# ---------------------------------------------------------------------------

def dmy_array(num_rows: int, cfg: CellPorts = _DEFAULT) -> SubcktDef:
    """
    Dummy cells: same WL grid as the real array but all cells share DBL/DBL_.

    Ports: VDD VSS WL0 ... WL{r-1} DBL DBL_
    """
    ports = ["VDD", "VSS"] + [f"WL{i}" for i in range(num_rows)] + ["DBL", "DBL_"]
    comps = [
        _si(f"XDMY{i}", "DMY_CELL", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.cell_wl: f"WL{i}", cfg.cell_bl: "DBL", cfg.cell_bl_: "DBL_",
        })
        for i in range(num_rows)
    ]
    return SubcktDef(name=f"DMY_ARR_{num_rows}", ports=ports, components=comps)


# ---------------------------------------------------------------------------
# Sense amplifier array
# ---------------------------------------------------------------------------

def se_array(bits: int, cfg: CellPorts = _DEFAULT) -> SubcktDef:
    """
    One SENSE_AMP per data bit.

    Ports: VDD VSS SAEN DR0 DR_0 ... DR{b-1} DR_{b-1} Q0 ... Q{b-1}
    """
    ports = ["VDD", "VSS", "SAEN"]
    for k in range(bits):
        ports += [f"DR{k}", f"DR_{k}"]
    for k in range(bits):
        ports.append(f"Q{k}")

    comps = [
        _si(f"XSA{k}", "SENSE_AMP", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.sa_en:  "SAEN",
            cfg.sa_inp: f"DR{k}", cfg.sa_inn: f"DR_{k}", cfg.sa_out: f"Q{k}",
        })
        for k in range(bits)
    ]
    return SubcktDef(name=f"SE_ARR_{bits}", ports=ports, components=comps)


# ---------------------------------------------------------------------------
# DIDO array (precharge + col-select + R/W pass)
# ---------------------------------------------------------------------------

def dido_array(num_cols: int, col_mux: int, bits: int, cfg: CellPorts = _DEFAULT) -> SubcktDef:
    """
    One DIDO cell per physical column.

    Column mux routing (mirrors FabRAM _build_dido_bus):
      physical col j  →  SEL{j % col_mux},  DW/DR{j // col_mux}

    Ports: VDD VSS PCHG WREN
           SEL0 ... SEL{col_mux-1}
           BL0 BL_0 ... BL{num_cols-1} BL_{num_cols-1}
           DW0 DW_0 ... DW{bits-1} DW_{bits-1}
           DR0 DR_0 ... DR{bits-1} DR_{bits-1}
    """
    ports = ["VDD", "VSS", "PCHG", "WREN"]
    for m in range(col_mux):
        ports.append(f"SEL{m}")
    for j in range(num_cols):
        ports += [f"BL{j}", f"BL_{j}"]
    for k in range(bits):
        ports += [f"DW{k}", f"DW_{k}"]
    for k in range(bits):
        ports += [f"DR{k}", f"DR_{k}"]

    comps = []
    for j in range(num_cols):
        mux_grp = j % col_mux
        data_bit = j // col_mux
        comps.append(_si(f"XDIDO{j}", "DIDO", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.dido_pchg: "PCHG",         cfg.dido_wren: "WREN",
            cfg.dido_sel:  f"SEL{mux_grp}",
            cfg.dido_bl:   f"BL{j}",        cfg.dido_bl_:  f"BL_{j}",
            cfg.dido_dw:   f"DW{data_bit}", cfg.dido_dw_:  f"DW_{data_bit}",
            cfg.dido_dr:   f"DR{data_bit}", cfg.dido_dr_:  f"DR_{data_bit}",
        }))
    return SubcktDef(name=f"DIDO_ARR_{num_cols}", ports=ports, components=comps)


# ---------------------------------------------------------------------------
# Driver arrays (row drivers and column drivers share the same template)
# ---------------------------------------------------------------------------

def drv_arr(count: int, in_prefix: str, out_prefix: str, arr_name: str,
            cfg: CellPorts = _DEFAULT) -> SubcktDef:
    """
    Generic one-input driver bank (reused for row and column drivers).

    ROW_DRIVER ports: VDD VSS WLEN A B
      A = decoded enable (active-high), B = buffered output (active-high)

    Generated ports: VDD VSS WLEN {in_prefix}0 ... {out_prefix}0 ...
    """
    ports = ["VDD", "VSS", "WLEN"]
    for i in range(count):
        ports.append(f"{in_prefix}{i}")
    for i in range(count):
        ports.append(f"{out_prefix}{i}")

    comps = [
        _si(f"XRD{i}", "ROW_DRIVER", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.drv_en:  "WLEN",
            cfg.drv_in:  f"{in_prefix}{i}",
            cfg.drv_out: f"{out_prefix}{i}",
        })
        for i in range(count)
    ]
    return SubcktDef(name=arr_name, ports=ports, components=comps)


# ---------------------------------------------------------------------------
# Input register bank (address + write_en)
# ---------------------------------------------------------------------------

def input_reg_arr(n: int, cfg: CellPorts = _DEFAULT) -> SubcktDef:
    """
    n MS_REG instances clocked together.

    Ports: VDD VSS clk D0 ... D{n-1} Q0 ... Q{n-1}
    """
    ports = ["VDD", "VSS", "clk"]
    for i in range(n):
        ports.append(f"D{i}")
    for i in range(n):
        ports.append(f"Q{i}")

    comps = [
        _si(f"XREG{i}", "MS_REG", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.reg_clk: "clk",
            cfg.reg_d: f"D{i}", cfg.reg_q: f"Q{i}",
        })
        for i in range(n)
    ]
    return SubcktDef(name=f"INPUT_REG_{n}", ports=ports, components=comps)


# ---------------------------------------------------------------------------
# Data-input array (MS_REG bank + WRITE_DRIVER bank)
# ---------------------------------------------------------------------------

def datain_arr(bits: int, cfg: CellPorts = _DEFAULT) -> SubcktDef:
    """
    Per data bit: one MS_REG (captures din on CLK) + one WRITE_DRIVER (drives DW/DW_).

    Ports: VDD VSS clk WREN din0 ... din{b-1} DW0 DW_0 ... DW{b-1} DW_{b-1}
    """
    ports = ["VDD", "VSS", "clk", "WREN"]
    for k in range(bits):
        ports.append(f"din{k}")
    for k in range(bits):
        ports += [f"DW{k}", f"DW_{k}"]

    comps = []
    # Stage 1: input registers  din{k} → din_r{k}
    for k in range(bits):
        comps.append(_si(f"XREG{k}", "MS_REG", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.reg_clk: "clk",
            cfg.reg_d: f"din{k}", cfg.reg_q: f"din_r{k}",
        }))
    # Stage 2: write drivers  din_r{k} + WREN → DW{k}/DW_{k}
    for k in range(bits):
        comps.append(_si(f"XWD{k}", "WRITE_DRIVER", {
            "VDD": "VDD", "VSS": "VSS",
            cfg.wd_en:  "WREN",
            cfg.wd_in:  f"din_r{k}",
            cfg.wd_dw:  f"DW{k}", cfg.wd_dw_: f"DW_{k}",
        }))
    return SubcktDef(name=f"DATAIN_ARR_{bits}", ports=ports, components=comps)
