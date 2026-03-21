"""
fabram.characterize.cells.sense_amp — sense amplifier sizing spec.

Circuit topology comes from cells/sram/sense_amp.yaml via spice_gen.
This file only declares which transistors are free parameters and how to
stimulate / measure the cell.

Free parameters
---------------
W_tail    : X0  NMOS tail current source
W_diff    : X1, X2  differential NMOS input pair
W_inv     : X5–X8  output inverter chain
W_latch_n : X9, X10  cross-coupled NMOS latch
W_latch_p : X11, X12  cross-coupled PMOS latch
W_buf_p   : X15  PMOS output buffer
W_buf_n   : X16  NMOS output buffer

Objectives
----------
- Minimise t_sense_ns  worst-case sense delay (SAEN½ → SB½, FALL)
- Minimise w_total_um  sum of free-parameter widths (area / leakage proxy)

Testbench
---------
BL_ = VDD, BL = VDD − delta_bl_mv (reading '0' → SB falls to VSS).
SAEN steps 0 → VDD at 5 ns; measure time for SB to fall through VDD/2.
"""
from __future__ import annotations

from fabram.characterize.optimizer import CellSpec, Param, Objective
from fabram.characterize.render    import render_subckt, CELLS_DIR, PDK_YAML, PDK_LIB, parse_sim_ns

# ── Circuit wiring: which transistor IDs each free parameter controls ─────────

PARAM_MAP: dict[str, list[str]] = {
    "W_tail":    ["X0"],
    "W_diff":    ["X1", "X2"],
    "W_inv":     ["X5", "X6", "X7", "X8"],
    "W_latch_n": ["X9",  "X10"],
    "W_latch_p": ["X11", "X12"],
    "W_buf_p":   ["X15"],
    "W_buf_n":   ["X16"],
}

_YAML = CELLS_DIR / "sense_amp.yaml"


# ── Testbench wrapper (stimulus + measurements only — no circuit) ─────────────

def _testbench(subckt_text: str, ports: list[str],
               params: dict, corner: dict,
               lib_path: str, delta_bl_mv: float) -> str:
    vdd  = corner["vdd"]
    temp = corner["temp"]
    lib_c = corner["lib_corner"]
    mid  = vdd / 2
    bl_v = vdd - delta_bl_mv * 1e-3

    dut = f"Xdut {' '.join(ports)} SENSE_AMP"

    return (
        f"* Sense amp  {lib_c}  delta_bl={delta_bl_mv:.0f}mV\n"
        f".lib \"{lib_path}\" {lib_c}\n"
        f".temp {temp}\n\n"
        f"VVDD VDD 0 DC {vdd}\n"
        f"VVSS VSS 0 DC 0\n\n"
        f"* Bit lines: BL_=VDD (reference), BL=VDD−{delta_bl_mv:.0f}mV\n"
        f"VBL_  BL_  0 DC {vdd}\n"
        f"VBL   BL   0 DC {bl_v:.6f}\n\n"
        f"VSAEN SAEN 0 PULSE(0 {vdd} 5n 0.1n 0.1n 15n 35n)\n\n"
        + subckt_text + "\n\n"
        + dut + "\n\n"
        f"* Initial: precharged state before SAEN fires\n"
        f".nodeset V(Xdut.net1)=0 V(Xdut.diff1)={vdd} V(Xdut.diff2)={vdd}"
        f" V(Xdut.diff2_)=0 V(Xdut.SB_)={vdd}"
        f" V(Xdut.SB_w)=0 V(Xdut.Q_)={vdd} V(SB)={vdd}\n\n"
        f".tran 0.01n 25n\n\n"
        f".control\n"
        f"  run\n"
        f"  meas tran t_sense TRIG v(SAEN) VAL={mid:.4f} RISE=1"
        f" TARG v(SB) VAL={mid:.4f} FALL=1\n"
        f"  echo \"$&t_sense\"\n"
        f"  exit\n"
        f".endc\n"
    )


# ── CellSpec factory ──────────────────────────────────────────────────────────

def make_spec(
    num_rows:    int   = 64,
    delta_bl_mv: float = 50.0,
    lib_path:    str   = PDK_LIB,
) -> CellSpec:
    """Sense-amp CellSpec.  num_rows scales initial search bounds."""
    scale = max(0.5, num_rows / 64.0)

    def _rng(w0: float, lo: float = 0.25, hi: float = 4.0):
        return max(0.42, round(w0 * lo, 2)), round(min(w0 * hi, 12.0), 2)

    def build_decks(params: dict, corner: dict) -> list[str]:
        subckt_text, ports = render_subckt(_YAML, params, PARAM_MAP, PDK_YAML)
        return [_testbench(subckt_text, ports, params, corner, lib_path, delta_bl_mv)]

    def extract_metrics(results: list[dict], params: dict) -> dict[str, float]:
        t_ns = parse_sim_ns(results[0] if results else {}, "t_sense")
        return {"t_sense_ns": t_ns, "w_total_um": sum(params.values())}

    w0 = {"W_tail": 0.84*scale, "W_diff": 0.42, "W_inv": 2.0*scale,
          "W_latch_n": 0.80*scale, "W_latch_p": 0.42, "W_buf_p": 2.0*scale, "W_buf_n": 1.0*scale}

    return CellSpec(
        name="sense_amp",
        params=[Param(k, *_rng(v)) for k, v in w0.items()],
        build_decks=build_decks,
        extract_metrics=extract_metrics,
        objectives=[
            Objective("t_sense_ns", "minimize"),
            Objective("w_total_um", "minimize"),
        ],
    )
