"""
fabram.characterize.cells.bit_cell — 6T SRAM bit-cell sizing spec.

Circuit topology comes from cells/sram/bit_cell.yaml via spice_gen.

Free parameters
---------------
W_PD : X0, X1  pull-down NMOS cross-coupled pair
W_PU : X2, X3  pull-up  PMOS cross-coupled pair
W_PG : X4, X5  access   NMOS pass transistors

Objectives
----------
- Maximise snm  read static-noise margin (N-curve method), worst-case across corners
- Maximise wm   write margin (BL sweep), worst-case across corners

Testbenches
-----------
1. SNM  : BL=BL_=WL=VDD (read mode); VFORCE sweeps V(Q) 0→VDD;
          SNM = V(Q) when V(Q_) first crosses VDD/2 (falling).
2. Write: BLb=VDD, BL swept 0→VDD, WL=VDD;
          WM = V(BL) when V(Q) first crosses VDD/2 (rising).
"""
from __future__ import annotations

from fabram.characterize.optimizer import CellSpec, Param, Objective
from fabram.characterize.render    import render_subckt, CELLS_DIR, PDK_YAML, PDK_LIB, parse_sim_ns

PARAM_MAP: dict[str, list[str]] = {
    "W_PD": ["X0", "X1"],
    "W_PU": ["X2", "X3"],
    "W_PG": ["X4", "X5"],
}

_YAML   = CELLS_DIR / "bit_cell.yaml"
DC_STEP = 0.001   # V — DC sweep resolution


# ── Testbench builders ────────────────────────────────────────────────────────

def _snm_deck(subckt_text: str, ports: list[str],
              params: dict, corner: dict, lib_path: str) -> str:
    """N-curve read-SNM testbench.

    Forces V(Xdut.Q) via DC sweep while BL=BL_=WL=VDD (read mode).
    SNM = V(Xdut.Q) when V(Xdut.Q_) first falls through VDD/2.
    """
    vdd   = corner["vdd"]
    temp  = corner["temp"]
    lib_c = corner["lib_corner"]
    mid   = vdd * 0.5
    dut   = f"Xdut {' '.join(ports)} BIT_CELL"

    return (
        f"* Bit-cell read SNM  {lib_c}\n"
        f".lib \"{lib_path}\" {lib_c}\n"
        f".temp {temp}\n\n"
        f"VVDD VDD 0 DC {vdd}\n"
        f"VVSS VSS 0 DC 0\n\n"
        f"VWL  WL  0 DC {vdd}\n"
        f"VBL  BL  0 DC {vdd}\n"
        f"VBL_ BL_ 0 DC {vdd}\n\n"
        + subckt_text + "\n\n"
        + dut + "\n\n"
        f"VFORCE Xdut.Q 0 0\n"
        f".nodeset V(Xdut.Q_)={vdd}\n\n"
        f".dc VFORCE 0 {vdd} {DC_STEP}\n\n"
        f".control\n"
        f"  run\n"
        f"  meas dc snm FIND v(Xdut.Q) WHEN v(Xdut.Q_)={mid:.4f} FALL=1\n"
        f"  echo \"$&snm\"\n"
        f"  exit\n"
        f".endc\n"
    )


def _wm_deck(subckt_text: str, ports: list[str],
             params: dict, corner: dict, lib_path: str) -> str:
    """Write-margin testbench (write 0 into Q=1).

    Cell starts at Q=1.  BL swept 0→VDD while BL_=VDD, WL=VDD.
    WM = V(BL) when V(Q) first rises through VDD/2 → larger WM = easier write.
    """
    vdd   = corner["vdd"]
    temp  = corner["temp"]
    lib_c = corner["lib_corner"]
    mid   = vdd * 0.5
    dut   = f"Xdut {' '.join(ports)} BIT_CELL"

    return (
        f"* Bit-cell write margin  {lib_c}\n"
        f".lib \"{lib_path}\" {lib_c}\n"
        f".temp {temp}\n\n"
        f"VVDD VDD 0 DC {vdd}\n"
        f"VVSS VSS 0 DC 0\n\n"
        f"VWL  WL  0 DC {vdd}\n"
        f"VBL_ BL_ 0 DC {vdd}\n"
        f"VBL  BL  0 0\n\n"
        + subckt_text + "\n\n"
        + dut + "\n\n"
        f".nodeset V(Xdut.Q)={vdd} V(Xdut.Q_)=0\n\n"
        f".dc VBL 0 {vdd} {DC_STEP}\n\n"
        f".control\n"
        f"  run\n"
        f"  meas dc wm FIND v(BL) WHEN v(Xdut.Q)={mid:.4f} RISE=1\n"
        f"  echo \"$&wm\"\n"
        f"  exit\n"
        f".endc\n"
    )


# ── CellSpec factory ──────────────────────────────────────────────────────────

def make_spec(lib_path: str = PDK_LIB) -> CellSpec:
    """Return a CellSpec for the 6T bit cell."""
    def build_decks(params: dict, corner: dict) -> list[str]:
        subckt_text, ports = render_subckt(_YAML, params, PARAM_MAP, PDK_YAML)
        return [
            _snm_deck(subckt_text, ports, params, corner, lib_path),
            _wm_deck(subckt_text, ports, params, corner, lib_path),
        ]

    def extract_metrics(results: list[dict], params: dict) -> dict[str, float]:
        # SNM and WM are voltages (V), not timing — use 0.0 as the failed sentinel
        # (a margin of zero is correctly worse than any positive margin).
        snm = parse_sim_ns(results[0] if results else {},          "snm", failed=0.0)
        wm  = parse_sim_ns(results[1] if len(results) > 1 else {}, "wm",  failed=0.0)
        return {"snm": snm, "wm": wm}

    return CellSpec(
        name="bit_cell",
        params=[
            Param("W_PD", 0.36, 1.50),
            Param("W_PU", 0.36, 0.84),
            Param("W_PG", 0.36, 1.00),
        ],
        build_decks=build_decks,
        extract_metrics=extract_metrics,
        objectives=[
            Objective("snm", "maximize"),
            Objective("wm",  "maximize"),
        ],
    )
