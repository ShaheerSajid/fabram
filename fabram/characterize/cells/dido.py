"""
fabram.characterize.cells.dido — DIDO peripheral cell sizing spec.

Circuit topology comes from cells/sram/dido.yaml via spice_gen.
Logic gates (NAND2, NOT) and column-select transistors use minimum sizing.
Only precharge PMOS (X0–X2) and write-pass NMOS (X10–X11) are free.

Free parameters
---------------
W_pchg : X0, X1, X2  precharge PMOS (three in parallel — BL, BL_, equaliser)
W_wr   : X10, X11    write-pass NMOS (BL ← DW, BL_ ← DW_)

Load estimate
-------------
Both precharge and write-pass drive one bitline, whose capacitance is
approximated from the column height:

    C_BL = num_rows × C_gate(W_access)
    C_gate(W) = COX_FF_UM2 × W × L  +  C_OV_FF_UM × W × 2

This follows the same analytical approach as row_driver (WL load) and
write_driver (BL load), calibrated against the same sky130A constants.

Objectives
----------
- Minimise t_cycle_ns  max(t_pchg_ns, t_wr_ns) worst-case across PVT
- Minimise w_total_um  W_pchg + W_wr (area proxy)

Testbenches
-----------
1. Precharge: BL/BL_ start at 0 V (worst case — just written 0);
              PCHG pulses low; measure PCHG 50%-fall → BL reaching 90 % VDD.
2. Write:     BL/BL_ start at VDD (precharged); WREN pulses high; Din = 0
              (DW = 0, DW_ = VDD); measure WREN 50%-rise → BL reaching 50 % VDD.
"""
from __future__ import annotations

from fabram.characterize.optimizer import CellSpec, Param, Objective
from fabram.characterize.render    import (
    render_subckt, CELLS_DIR, PDK_YAML, PDK_LIB, PDK_PARAMS,
    gate_cap_ff, driver_w, parse_sim_ns,
)

PARAM_MAP: dict[str, list[str]] = {
    "W_pchg": ["X0", "X1", "X2"],   # precharge PMOS — three in parallel
    "W_wr":   ["X10", "X11"],        # write-pass NMOS
}

_YAML    = CELLS_DIR / "dido.yaml"
_R_N_OHM = PDK_PARAMS.get("r_nmos_ohm", 5600)   # NMOS on-resistance × width (Ω·µm)
_R_P_OHM = PDK_PARAMS.get("r_pmos_ohm", 9000)   # PMOS on-resistance × width (Ω·µm)


# ── Testbench builders ────────────────────────────────────────────────────────

def _pchg_testbench(subckt_text: str, ports: list[str],
                    params: dict, corner: dict,
                    lib_path: str, c_bl_ff: float) -> str:
    """Measure precharge settle time: PCHG 50%-fall → BL reaches 90% VDD."""
    vdd   = corner["vdd"]
    temp  = corner["temp"]
    lib_c = corner["lib_corner"]
    mid   = vdd / 2
    thr90 = 0.9 * vdd
    dut   = f"Xdut {' '.join(ports)} DIDO"

    return (
        f"* DIDO precharge  {lib_c}  BL_load={c_bl_ff:.1f}fF\n"
        f".lib \"{lib_path}\" {lib_c}\n"
        f".temp {temp}\n\n"
        f"VVDD VDD 0 DC {vdd}\n"
        f"VVSS VSS 0 DC 0\n\n"
        f"* PCHG: 1→0 (active low) at 5 ns\n"
        f"VPCHG PCHG 0 PULSE({vdd} 0 5n 0.1n 0.1n 15n 35n)\n"
        f"VWREN WREN 0 DC 0\n"
        f"VSEL  SEL  0 DC 0\n"
        f"VDW   DW   0 DC 0\n"
        f"VDW_  DW_  0 DC 0\n\n"
        + subckt_text + "\n\n"
        + dut + "\n\n"
        f"CBL  BL  0 {c_bl_ff:.2f}f\n"
        f"CBL_ BL_ 0 {c_bl_ff:.2f}f\n\n"
        f"* BL/BL_ start discharged — worst case after writing 0\n"
        f".ic V(BL)=0 V(BL_)=0\n\n"
        f".tran 0.01n 25n uic\n\n"
        f".control\n"
        f"  run\n"
        f"  meas tran t_pchg TRIG v(PCHG) VAL={mid:.4f} FALL=1"
        f" TARG v(BL) VAL={thr90:.4f} RISE=1\n"
        f"  echo \"$&t_pchg\"\n"
        f"  exit\n"
        f".endc\n"
    )


def _write_testbench(subckt_text: str, ports: list[str],
                     params: dict, corner: dict,
                     lib_path: str, c_bl_ff: float) -> str:
    """Measure write drive time: WREN 50%-rise → BL reaches 50% VDD (fall)."""
    vdd   = corner["vdd"]
    temp  = corner["temp"]
    lib_c = corner["lib_corner"]
    mid   = vdd / 2
    dut   = f"Xdut {' '.join(ports)} DIDO"

    return (
        f"* DIDO write  {lib_c}  BL_load={c_bl_ff:.1f}fF\n"
        f".lib \"{lib_path}\" {lib_c}\n"
        f".temp {temp}\n\n"
        f"VVDD VDD 0 DC {vdd}\n"
        f"VVSS VSS 0 DC 0\n\n"
        f"* PCHG=VDD (precharge off during write)\n"
        f"VPCHG PCHG 0 DC {vdd}\n"
        f"* WREN: 0→1 (active high) at 5 ns\n"
        f"VWREN WREN 0 PULSE(0 {vdd} 5n 0.1n 0.1n 15n 35n)\n"
        f"* SEL=VDD — column selected; enables write-pass via internal NAND logic\n"
        f"VSEL  SEL  0 DC {vdd}\n"
        f"* Din=0: DW=0, DW_=VDD (write 0 — BL pulled low, BL_ stays high)\n"
        f"VDW   DW   0 DC 0\n"
        f"VDW_  DW_  0 DC {vdd}\n\n"
        + subckt_text + "\n\n"
        + dut + "\n\n"
        f"CBL  BL  0 {c_bl_ff:.2f}f\n"
        f"CBL_ BL_ 0 {c_bl_ff:.2f}f\n\n"
        f"* BL/BL_ start precharged\n"
        f".ic V(BL)={vdd} V(BL_)={vdd}\n\n"
        f".tran 0.01n 25n uic\n\n"
        f".control\n"
        f"  run\n"
        f"  meas tran t_wr TRIG v(WREN) VAL={mid:.4f} RISE=1"
        f" TARG v(BL) VAL={mid:.4f} FALL=1\n"
        f"  echo \"$&t_wr\"\n"
        f"  exit\n"
        f".endc\n"
    )


# ── CellSpec factory ──────────────────────────────────────────────────────────

def make_spec(
    num_rows:        int   = 64,
    bit_cell_w_pg:   float = 0.60,
    target_delay_ns: float = 0.30,
    lib_path:        str   = PDK_LIB,
) -> CellSpec:
    """DIDO CellSpec.  num_rows sets BL load and initial search bounds.

    BL capacitance = num_rows × C_gate(bit_cell_w_pg) — one bitline column.
    Three parallel precharge PMOS → each transistor needs 1/3 of total drive,
    so the initial guess divides by 3 before computing width.
    """
    c_bl    = num_rows * gate_cap_ff(bit_cell_w_pg)   # single BL capacitance (fF)
    # 3 parallel PMOS for precharge → each carries 1/3 of the drive current
    w_pchg0 = round(driver_w(c_bl / 3, target_delay_ns, _R_P_OHM), 2)
    w_wr0   = round(driver_w(c_bl,     target_delay_ns, _R_N_OHM), 2)

    def build_decks(params: dict, corner: dict) -> list[str]:
        subckt_text, ports = render_subckt(_YAML, params, PARAM_MAP, PDK_YAML)
        return [
            _pchg_testbench(subckt_text, ports, params, corner, lib_path, c_bl),
            _write_testbench(subckt_text, ports, params, corner, lib_path, c_bl),
        ]

    def extract_metrics(results: list[dict], params: dict) -> dict[str, float]:
        t_pchg  = parse_sim_ns(results[0] if results else {},          "t_pchg")
        t_wr    = parse_sim_ns(results[1] if len(results) > 1 else {}, "t_wr")
        t_cycle = max(t_pchg, t_wr)
        return {
            "t_cycle_ns": t_cycle,
            "w_total_um": params["W_pchg"] + params["W_wr"],
        }

    return CellSpec(
        name="dido",
        params=[
            Param("W_pchg", max(0.42, w_pchg0 / 4), min(16.0, w_pchg0 * 4)),
            Param("W_wr",   max(0.42, w_wr0   / 4), min(16.0, w_wr0   * 4)),
        ],
        build_decks=build_decks,
        extract_metrics=extract_metrics,
        objectives=[
            Objective("t_cycle_ns", "minimize"),
            Objective("w_total_um", "minimize"),
        ],
    )
