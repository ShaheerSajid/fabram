"""
Waveform generation for characterization verification.

Runs 5 quick ngspice sims (CLK-to-Q rise/fall, leakage, power write/read)
with ``wrdata`` output, then renders multi-row SVG plots with matplotlib.

Called automatically by ``fabram-char`` unless ``--no-waveforms`` is passed.
"""
from __future__ import annotations

import logging
import pathlib

import numpy as np
import matplotlib
matplotlib.use("Agg")   # headless — no X11 required
import matplotlib.pyplot as plt

from liberty_gen.config import CharConfig
from liberty_gen.testbench import (
    build_clkq_testbench,
    build_leakage_testbench,
    build_power_testbench,
)
from liberty_gen.runner import run_ngspice

log = logging.getLogger(__name__)

_CLKQ_NODES = ["v(CLK)", "v(Q0)", "v(CS)", "v(WRITE)", "v(din0)"]
_CLKQ_NAMES = ["CLK", "Q0", "CS", "WRITE", "din0"]


def generate_waveforms(
    netlist_path: str,
    cfg: CharConfig,
    macro: str,
    addr_bits: int,
    bits: int,
    out_dir: pathlib.Path,
) -> None:
    """Run 5 waveform sims and write SVGs + raw data files to ``out_dir``.

    Files written per sim (e.g. clkq_q1):
      clkq_q1.sp   — testbench deck (for manual re-run / inspection)
      clkq_q1.dat  — raw ngspice wrdata output (ASCII, time+value columns)
      clkq_q1.svg  — rendered waveform plot
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    clk_slew = cfg.input_slews[0]
    load_pf  = cfg.output_loads[0]

    sims = [
        (
            "clkq_q1",
            build_clkq_testbench(
                netlist_path, cfg, macro, addr_bits, bits,
                clk_slew=clk_slew, load_pf=load_pf, q_val=1,
                svg_path=str(out_dir / "clkq_q1.dat"),
                svg_nodes=_CLKQ_NODES,
            ),
            _CLKQ_NAMES,
            "CLK-to-Q (write 0→1, Q rises)",
        ),
        (
            "clkq_q0",
            build_clkq_testbench(
                netlist_path, cfg, macro, addr_bits, bits,
                clk_slew=clk_slew, load_pf=load_pf, q_val=0,
                svg_path=str(out_dir / "clkq_q0.dat"),
                svg_nodes=_CLKQ_NODES,
            ),
            _CLKQ_NAMES,
            "CLK-to-Q (write 0, Q falls)",
        ),
        (
            "leakage",
            build_leakage_testbench(
                netlist_path, cfg, macro, addr_bits, bits,
                svg_path=str(out_dir / "leakage.dat"),
                svg_nodes=["v(CLK)", "vvss#branch"],
            ),
            ["CLK", "vvss#branch (A)"],
            "Standby leakage (CS=0)",
        ),
        (
            "power_write",
            build_power_testbench(
                netlist_path, cfg, macro, addr_bits, bits, op="write",
                svg_path=str(out_dir / "power_write.dat"),
                svg_nodes=["v(CLK)", "v(Q0)", "vvss#branch"],
            ),
            ["CLK", "Q0", "vvss#branch (A)"],
            "Dynamic power — write cycle",
        ),
        (
            "power_read",
            build_power_testbench(
                netlist_path, cfg, macro, addr_bits, bits, op="read",
                svg_path=str(out_dir / "power_read.dat"),
                svg_nodes=["v(CLK)", "v(Q0)", "vvss#branch"],
            ),
            ["CLK", "Q0", "vvss#branch (A)"],
            "Dynamic power — read cycle",
        ),
    ]

    for name, deck, col_names, title in sims:
        log.info("[wave] %s …", name)
        (out_dir / f"{name}.sp").write_text(deck)
        run_ngspice(deck, cfg.sim_timeout)
        dat = out_dir / f"{name}.dat"
        if dat.exists():
            _plot_wrdata(dat, col_names, title, out_dir / f"{name}.svg")
        else:
            log.warning("[wave] %s: no wrdata output from ngspice", name)

    log.info("[wave] Waveforms written to %s", out_dir)


def _plot_wrdata(data_path: pathlib.Path, col_names: list[str],
                 title: str, svg_out: pathlib.Path) -> None:
    """Parse ngspice wrdata file and write a stacked SVG plot.

    wrdata format: interleaved (time, value) pairs per node.
    For N nodes each row has 2N columns: t0 v0 t1 v1 … t_{N-1} v_{N-1}.
    All time columns are identical; column 0 is used as the x-axis.
    """
    try:
        data = np.loadtxt(data_path)
    except Exception as exc:
        log.warning("[wave] Could not load %s: %s", data_path, exc)
        return
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[1] < 2:
        return

    t_ns = data[:, 0] * 1e9
    fig, axes = plt.subplots(len(col_names), 1,
                             figsize=(10, 2 * len(col_names)), sharex=True)
    if len(col_names) == 1:
        axes = [axes]
    for i, name in enumerate(col_names):
        val_col = 2 * i + 1
        if val_col < data.shape[1]:
            axes[i].plot(t_ns, data[:, val_col], linewidth=1)
        axes[i].set_ylabel(name, fontsize=8)
        axes[i].grid(True, linestyle="--", alpha=0.4)
    axes[-1].set_xlabel("Time (ns)")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(svg_out, format="svg")
    plt.close(fig)
