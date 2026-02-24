"""
Generate SVG waveform snapshots for the five non-setup/hold testbenches
to visually confirm the SRAM is toggling correctly.

Uses ngspice ``wrdata`` to dump ASCII waveform data, then matplotlib to plot.
Works in headless (no X11) batch mode.

Sims:
  1. clkq_q1   — write 1 then read  (expect Q0 rising after CLK3)
  2. clkq_q0   — write 0 then read  (expect Q0 falling after CLK3)
  3. leakage   — standby (CS=0), check VSS current
  4. power_write — write cycle power
  5. power_read  — read cycle power
"""
import sys, pathlib, tempfile

import numpy as np
import matplotlib
matplotlib.use("Agg")   # headless backend
import matplotlib.pyplot as plt

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from fabram import SRAMCompiler
from liberty_gen.config import CharConfig
from liberty_gen.testbench import (
    build_clkq_testbench,
    build_leakage_testbench,
    build_power_testbench,
)
from liberty_gen.runner import run_ngspice
from spice_gen.generator.ngspice import NgspiceGenerator

OUT = pathlib.Path("/home/shaheer/Desktop/fabram/svgs")
OUT.mkdir(exist_ok=True)

# ── compile the SRAM ──────────────────────────────────────────────────────────
print("Compiling 32×4 CM4 SRAM …")
sram = SRAMCompiler(words=32, bits=4, col_mux=4)
netlist = sram.compile()
geo = sram.geo
macro = geo.name
addr_bits = geo.addr_bits
bits = geo.bits
print(f"  macro={macro}  addr_bits={addr_bits}  bits={bits}")

cfg = CharConfig()

# ── write netlist to a persistent temp file ───────────────────────────────────
gen = NgspiceGenerator()
spice_text = gen.generate(netlist)
with tempfile.NamedTemporaryFile(
    mode="w", suffix=".sp", prefix="fabram_netlist_", delete=False
) as f:
    f.write(spice_text)
    netlist_path = f.name
print(f"  netlist → {netlist_path}")

clk_slew = cfg.input_slews[0]   # 0.02 ns
load_pf  = cfg.output_loads[0]  # 0.001 pF


def plot_wrdata(data_path: pathlib.Path, col_names: list[str],
                title: str, svg_out: pathlib.Path) -> None:
    """Read wrdata ASCII file and plot with matplotlib."""
    try:
        data = np.loadtxt(data_path)
    except Exception as e:
        print(f"  [plot] could not load {data_path}: {e}")
        return
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[1] < 2:
        print(f"  [plot] not enough columns in {data_path}")
        return
    # wrdata writes interleaved (time, value) pairs per node:
    #   col 0=t0, col 1=val0, col 2=t1, col 3=val1, ...
    # All time columns are identical; use col 0 for the x-axis.
    t_ns = data[:, 0] * 1e9   # seconds → ns
    fig, axes = plt.subplots(len(col_names), 1, figsize=(10, 2 * len(col_names)),
                             sharex=True)
    if len(col_names) == 1:
        axes = [axes]
    for i, name in enumerate(col_names):
        val_col = 2 * i + 1   # interleaved: value for node i is at column 2*i+1
        if val_col < data.shape[1]:
            axes[i].plot(t_ns, data[:, val_col], linewidth=1)
        axes[i].set_ylabel(name, fontsize=8)
        axes[i].grid(True, linestyle="--", alpha=0.4)
    axes[-1].set_xlabel("Time (ns)")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(svg_out, format="svg")
    plt.close(fig)
    print(f"  svg  → {svg_out}  ({svg_out.stat().st_size} bytes)")


# ── define sims ───────────────────────────────────────────────────────────────
wave_nodes_clkq = ["v(CLK)", "v(Q0)", "v(CS)", "v(WRITE)", "v(din0)"]
wave_col_names  = ["CLK", "Q0", "CS", "WRITE", "din0"]

sims = [
    (
        "clkq_q1",
        build_clkq_testbench(
            netlist_path, cfg, macro, addr_bits, bits,
            clk_slew=clk_slew, load_pf=load_pf, q_val=1,
            svg_path=str(OUT / "clkq_q1.dat"),
            svg_nodes=wave_nodes_clkq,
        ),
        wave_col_names,
        "CLK-to-Q (write 1 → read)",
    ),
    (
        "clkq_q0",
        build_clkq_testbench(
            netlist_path, cfg, macro, addr_bits, bits,
            clk_slew=clk_slew, load_pf=load_pf, q_val=0,
            svg_path=str(OUT / "clkq_q0.dat"),
            svg_nodes=wave_nodes_clkq,
        ),
        wave_col_names,
        "CLK-to-Q (write 0 → read)",
    ),
    (
        "leakage",
        build_leakage_testbench(
            netlist_path, cfg, macro, addr_bits, bits,
            svg_path=str(OUT / "leakage.dat"),
            svg_nodes=["v(CLK)", "vvss#branch"],
        ),
        ["CLK", "vvss#branch (A)"],
        "Standby leakage (CS=0)",
    ),
    (
        "power_write",
        build_power_testbench(
            netlist_path, cfg, macro, addr_bits, bits, op="write",
            svg_path=str(OUT / "power_write.dat"),
            svg_nodes=["v(CLK)", "v(Q0)", "vvss#branch"],
        ),
        ["CLK", "Q0", "vvss#branch (A)"],
        "Dynamic power — write cycle",
    ),
    (
        "power_read",
        build_power_testbench(
            netlist_path, cfg, macro, addr_bits, bits, op="read",
            svg_path=str(OUT / "power_read.dat"),
            svg_nodes=["v(CLK)", "v(Q0)", "vvss#branch"],
        ),
        ["CLK", "Q0", "vvss#branch (A)"],
        "Dynamic power — read cycle",
    ),
]

for name, deck, col_names, title in sims:
    print(f"\n── {name} ──")
    deck_path = OUT / f"{name}.sp"
    deck_path.write_text(deck)
    meas = run_ngspice(deck, cfg.sim_timeout)
    print(f"  meas → {meas}")
    dat_file = OUT / f"{name}.dat"
    if dat_file.exists():
        plot_wrdata(dat_file, col_names, title, OUT / f"{name}.svg")
    else:
        print(f"  [warn] {dat_file} not written by ngspice")

print(f"\nAll done. SVGs in {OUT}/")
