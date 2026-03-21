"""
fabram.characterize.cells.row_driver — word-line drive inverter sizing spec.

Circuit topology comes from cells/sram/row_driver.yaml via spice_gen.
Only the two high-drive inverter transistors (X1 PMOS, X2 NMOS) are free;
the NAND2 gate uses minimum sizing from its own YAML.

Free parameters
---------------
W_buf_p : X1  PMOS high-drive inverter
W_buf_n : X2  NMOS high-drive inverter

Initial-guess strategy
----------------------
WL capacitive load is estimated from num_cols × 2 × C_gate(W_PG).
Initial widths are derived so that RC delay ≈ target_delay_ns, calibrated
against the existing 64-column design point (W_buf_p=8, W_buf_n=4, τ≈0.3 ns).
BO search bounds: [init/4, init×4].

Objectives
----------
- Minimise t_wl_rise_ns  50% WL propagation delay from WLEN rise (worst-case)
- Minimise w_total_um    W_buf_p + W_buf_n (drive-cell area proxy)
"""
from __future__ import annotations

from fabram.characterize.optimizer import CellSpec, Param, Objective
from fabram.characterize.render    import (
    render_subckt, CELLS_DIR, PDK_YAML, PDK_LIB, PDK_PARAMS,
    gate_cap_ff, driver_w, parse_sim_ns,
)

PARAM_MAP: dict[str, list[str]] = {
    "W_buf_p": ["X1"],
    "W_buf_n": ["X2"],
}

_YAML    = CELLS_DIR / "row_driver.yaml"
_R_N_OHM = PDK_PARAMS.get("r_nmos_ohm", 5600)   # NMOS Ω·µm
_R_P_OHM = PDK_PARAMS.get("r_pmos_ohm", 9000)   # PMOS Ω·µm


# ── Testbench wrapper ─────────────────────────────────────────────────────────

def _testbench(subckt_text: str, ports: list[str],
               params: dict, corner: dict,
               lib_path: str, c_load_ff: float) -> str:
    vdd  = corner["vdd"]
    temp = corner["temp"]
    lib_c = corner["lib_corner"]
    mid  = vdd / 2

    dut = f"Xdut {' '.join(ports)} ROW_DRIVER"

    return (
        f"* Row driver  {lib_c}  WL_load={c_load_ff:.1f}fF\n"
        f".lib \"{lib_path}\" {lib_c}\n"
        f".temp {temp}\n\n"
        f"VVDD VDD 0 DC {vdd}\n"
        f"VVSS VSS 0 DC 0\n\n"
        f"VA    A    0 DC {vdd}\n"
        f"VWLEN WLEN 0 PULSE(0 {vdd} 5n 0.1n 0.1n 15n 35n)\n\n"
        + subckt_text + "\n\n"
        + dut + "\n\n"
        f"CWL B 0 {c_load_ff:.2f}f\n\n"
        f".nodeset V(B)=0 V(Xdut.net1)={vdd}\n\n"
        f".tran 0.01n 25n\n\n"
        f".control\n"
        f"  run\n"
        f"  meas tran t_wl_rise TRIG v(WLEN) VAL={mid:.4f} RISE=1"
        f" TARG v(B) VAL={mid:.4f} RISE=1\n"
        f"  echo \"$&t_wl_rise\"\n"
        f"  exit\n"
        f".endc\n"
    )


# ── CellSpec factory ──────────────────────────────────────────────────────────

def make_spec(
    num_cols:        int   = 64,
    bit_cell_w_pg:   float = 0.60,
    target_delay_ns: float = 0.30,
    lib_path:        str   = PDK_LIB,
) -> CellSpec:
    """Row-driver CellSpec.  num_cols sets WL load and initial search bounds."""
    c_load = num_cols * 2 * gate_cap_ff(bit_cell_w_pg)
    w_p0   = round(driver_w(c_load, target_delay_ns, _R_P_OHM), 2)
    w_n0   = round(driver_w(c_load, target_delay_ns, _R_N_OHM), 2)

    def build_decks(params: dict, corner: dict) -> list[str]:
        subckt_text, ports = render_subckt(_YAML, params, PARAM_MAP, PDK_YAML)
        return [_testbench(subckt_text, ports, params, corner, lib_path, c_load)]

    def extract_metrics(results: list[dict], params: dict) -> dict[str, float]:
        t_ns = parse_sim_ns(results[0] if results else {}, "t_wl_rise")
        return {"t_wl_rise_ns": t_ns, "w_total_um": params["W_buf_p"] + params["W_buf_n"]}

    return CellSpec(
        name="row_driver",
        params=[
            Param("W_buf_p", max(0.42, w_p0 / 4), min(32.0, w_p0 * 4)),
            Param("W_buf_n", max(0.42, w_n0 / 4), min(32.0, w_n0 * 4)),
        ],
        build_decks=build_decks,
        extract_metrics=extract_metrics,
        objectives=[
            Objective("t_wl_rise_ns", "minimize"),
            Objective("w_total_um",   "minimize"),
        ],
    )
