"""
fabram.characterize.cells.write_driver — differential write-driver sizing spec.

Circuit topology comes from cells/sram/write_driver.yaml via spice_gen.
Logic inverters (X0–X5, W=0.42) are fixed; only output driver transistors
(X6–X13) are free parameters.

Free parameters
---------------
W_drv_p : X6, X7, X10, X11  PMOS output drivers
W_drv_n : X8, X9, X12, X13  NMOS output drivers

Objectives
----------
- Minimise t_drive_ns  max(DW rise, DW_ fall) from WREN (worst-case)
- Minimise w_total_um  W_drv_p + W_drv_n (area proxy)
"""
from __future__ import annotations

from fabram.characterize.optimizer import CellSpec, Param, Objective
from fabram.characterize.render    import (
    render_subckt, CELLS_DIR, PDK_YAML, PDK_LIB, PDK_PARAMS,
    gate_cap_ff, driver_w, parse_sim_ns,
)

PARAM_MAP: dict[str, list[str]] = {
    "W_drv_p": ["X6",  "X7",  "X10", "X11"],
    "W_drv_n": ["X8",  "X9",  "X12", "X13"],
}

_YAML    = CELLS_DIR / "write_driver.yaml"
_R_N_OHM = PDK_PARAMS.get("r_nmos_ohm", 5600)
_R_P_OHM = PDK_PARAMS.get("r_pmos_ohm", 9000)


# ── Testbench wrapper ─────────────────────────────────────────────────────────

def _testbench(subckt_text: str, ports: list[str],
               params: dict, corner: dict,
               lib_path: str, c_bl_ff: float) -> str:
    vdd  = corner["vdd"]
    temp = corner["temp"]
    lib_c = corner["lib_corner"]
    mid  = vdd / 2

    dut = f"Xdut {' '.join(ports)} WRITE_DRIVER"

    return (
        f"* Write driver  {lib_c}  BL_load={c_bl_ff:.1f}fF\n"
        f".lib \"{lib_path}\" {lib_c}\n"
        f".temp {temp}\n\n"
        f"VVDD VDD 0 DC {vdd}\n"
        f"VVSS VSS 0 DC 0\n\n"
        f"VDin  Din  0 DC {vdd}\n"
        f"VWREN WREN 0 PULSE(0 {vdd} 5n 0.1n 0.1n 15n 35n)\n\n"
        + subckt_text + "\n\n"
        + dut + "\n\n"
        f"CDW  DW  0 {c_bl_ff:.2f}f\n"
        f"CDW_ DW_ 0 {c_bl_ff:.2f}f\n\n"
        f"* Initial: DW=0, DW_=VDD (force with .ic uic — .nodeset fails due to leakage)\n"
        f".ic V(DW)=0 V(DW_)={vdd}\n\n"
        f".tran 0.01n 25n uic\n\n"
        f".control\n"
        f"  run\n"
        f"  meas tran t_dw_rise TRIG v(WREN) VAL={mid:.4f} RISE=1"
        f" TARG v(DW)  VAL={mid:.4f} RISE=1\n"
        f"  meas tran t_dw_fall TRIG v(WREN) VAL={mid:.4f} RISE=1"
        f" TARG v(DW_) VAL={mid:.4f} FALL=1\n"
        f"  echo \"$&t_dw_rise\"\n"
        f"  echo \"$&t_dw_fall\"\n"
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
    """Write-driver CellSpec.  num_rows sets BL load and initial search bounds."""
    c_bl   = num_rows * 2 * gate_cap_ff(bit_cell_w_pg)
    w_p0   = round(driver_w(c_bl, target_delay_ns, _R_P_OHM), 2)
    w_n0   = round(driver_w(c_bl, target_delay_ns, _R_N_OHM), 2)

    def build_decks(params: dict, corner: dict) -> list[str]:
        subckt_text, ports = render_subckt(_YAML, params, PARAM_MAP, PDK_YAML)
        return [_testbench(subckt_text, ports, params, corner, lib_path, c_bl)]

    def extract_metrics(results: list[dict], params: dict) -> dict[str, float]:
        res     = results[0] if results else {}
        t_drive = max(parse_sim_ns(res, "t_dw_rise"), parse_sim_ns(res, "t_dw_fall"))
        return {"t_drive_ns": t_drive, "w_total_um": params["W_drv_p"] + params["W_drv_n"]}

    return CellSpec(
        name="write_driver",
        params=[
            Param("W_drv_p", max(0.42, w_p0 / 4), min(16.0, w_p0 * 4)),
            Param("W_drv_n", max(0.42, w_n0 / 4), min(16.0, w_n0 * 4)),
        ],
        build_decks=build_decks,
        extract_metrics=extract_metrics,
        objectives=[
            Objective("t_drive_ns", "minimize"),
            Objective("w_total_um", "minimize"),
        ],
    )
