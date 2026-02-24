"""
fabram — synchronous single-port SRAM memory compiler.

Basic usage (netlist only)::

    fabram -w 32 -b 4 -m 4

Full characterization (netlist + Liberty + waveforms)::

    fabram -w 32 -b 4 -m 4 --char

Output layout (relative to CWD, unless --out-dir is overridden)::

    out/<macro>/
    ├── netlist/<macro>.sp          compiled SPICE netlist
    ├── lib/<macro>_<cond>.lib      Liberty timing model  (--char only)
    ├── waveform/                   SVG waveform plots    (--char, skippable with --no-waveforms)
    │   ├── clkq_q1.{sp,dat,svg}
    │   ├── clkq_q0.{sp,dat,svg}
    │   ├── leakage.{sp,dat,svg}
    │   ├── power_write.{sp,dat,svg}
    │   └── power_read.{sp,dat,svg}
    └── logs/char.log               characterization log  (--char only)
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from spice_gen.generator import get_generator
from spice_gen.generator.ngspice import NgspiceGenerator
from fabram.generators.top import SRAMCompiler


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fabram",
        description="Synchronous single-port SRAM memory compiler.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── SRAM geometry ────────────────────────────────────────────────────────
    p.add_argument("-w", "--words",   type=int, required=True,
                   help="Number of words (depth); must be a power of 2.")
    p.add_argument("-b", "--bits",    type=int, required=True,
                   help="Word width in bits.")
    p.add_argument("-m", "--mux",     type=int, default=1,
                   help="Column mux factor (power of 2).")

    # ── PDK / cell source ────────────────────────────────────────────────────
    p.add_argument("--cells-dir", type=pathlib.Path, default=None, metavar="DIR",
                   help="Override path to cells/ directory.")
    p.add_argument("--pdk-yaml",  type=pathlib.Path, default=None, metavar="FILE",
                   help="Override path to PDK YAML config.")
    p.add_argument("-c", "--corner", default="tt",
                   help="PDK process corner (e.g. tt, ff, ss).")
    p.add_argument("-d", "--dialect",
                   choices=["ngspice", "hspice", "spice3"], default="ngspice",
                   help="SPICE dialect for the netlist output.")

    # ── Output ───────────────────────────────────────────────────────────────
    p.add_argument("--out-dir", type=pathlib.Path, default=pathlib.Path("out"),
                   metavar="DIR",
                   help="Root output directory.")
    p.add_argument("-o", "--output", type=pathlib.Path, default=None,
                   metavar="FILE",
                   help="Write netlist to FILE instead of out/<macro>/netlist/.")
    p.add_argument("--stdout", action="store_true",
                   help="Write netlist to stdout (skips all file outputs).")

    # ── Characterization ─────────────────────────────────────────────────────
    p.add_argument("--char", action="store_true",
                   help="Run Liberty characterization after compiling the netlist.")
    p.add_argument("--vdd",       type=float, default=1.8,  help="Supply voltage (V).")
    p.add_argument("--temp",      type=float, default=27.0, help="Temperature (°C).")
    p.add_argument("--period",    type=float, default=10.0,
                   help="Test-clock period (ns).")
    p.add_argument("--timestep",  type=float, default=0.001,
                   help="ngspice .tran timestep (ns).")
    p.add_argument("--workers",   type=int,   default=4,
                   help="Parallel ngspice workers.")
    p.add_argument("--timeout",   type=int,   default=180,
                   help="Per-simulation ngspice timeout (s).")
    p.add_argument("--no-waveforms", action="store_true",
                   help="Skip waveform SVG generation when --char is set.")

    # ── Logging ───────────────────────────────────────────────────────────────
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable INFO-level logging to console.")
    return p


def _make_dirs(out_dir: pathlib.Path, macro: str) -> dict[str, pathlib.Path]:
    root = out_dir / macro
    dirs = {
        "root":     root,
        "netlist":  root / "netlist",
        "lib":      root / "lib",
        "waveform": root / "waveform",
        "logs":     root / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # ── Compile SRAM netlist ─────────────────────────────────────────────────
    try:
        compiler = SRAMCompiler(
            args.words, args.bits, args.mux,
            cells_dir=args.cells_dir,
            pdk_yaml=args.pdk_yaml,
        )
        netlist = compiler.compile(pdk_corner=args.corner)
    except (ValueError, FileNotFoundError) as exc:
        print(f"fabram: error: {exc}", file=sys.stderr)
        return 1

    macro = compiler.geo.name

    # ── Stdout mode — render and exit immediately ────────────────────────────
    if args.stdout:
        generator = get_generator(args.dialect)
        sys.stdout.write(generator.generate(netlist))
        return 0

    # ── Set up output directory structure ─────────────────────────────────────
    dirs = _make_dirs(args.out_dir, macro)

    # ── Write compiled netlist ────────────────────────────────────────────────
    gen = NgspiceGenerator()
    spice_text = gen.generate(netlist)
    netlist_path = args.output if args.output else dirs["netlist"] / f"{macro}.sp"
    if args.output:
        netlist_path.parent.mkdir(parents=True, exist_ok=True)
    netlist_path.write_text(spice_text, encoding="utf-8")
    print(f"Netlist  {netlist_path}")

    # ── Characterization (--char) ─────────────────────────────────────────────
    if not args.char:
        return 0

    from liberty_gen import CharConfig, CharCompiler
    from fabram.waveforms import generate_waveforms

    # Configure logging: console + file
    log_path = dirs["logs"] / "char.log"
    root_log = logging.getLogger()
    root_log.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO if args.verbose else logging.WARNING)
    ch.setFormatter(logging.Formatter("%(message)s"))
    root_log.addHandler(ch)

    fh = logging.FileHandler(log_path, mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    root_log.addHandler(fh)

    cfg = CharConfig(
        vdd=args.vdd,
        temp=args.temp,
        clk_period=args.period,
        sim_timestep=args.timestep,
        max_workers=args.workers,
        sim_timeout=args.timeout,
    )

    try:
        char = CharCompiler(
            str(netlist_path), macro,
            compiler.geo.addr_bits, compiler.geo.bits,
            cfg=cfg,
        )
        lib_text = char.characterize()
    except Exception as exc:
        print(f"fabram: characterization failed: {exc}", file=sys.stderr)
        return 1

    vdd_str  = f"{cfg.vdd:.2f}".replace(".", "p")
    temp_str = f"{int(cfg.temp):03d}C"
    lib_path = dirs["lib"] / f"{macro}_{temp_str}_{vdd_str}V.lib"
    lib_path.write_text(lib_text)
    print(f"Liberty  {lib_path}")

    if not args.no_waveforms:
        generate_waveforms(
            str(netlist_path), cfg, macro,
            compiler.geo.addr_bits, compiler.geo.bits,
            out_dir=dirs["waveform"],
        )
        print(f"Waveform {dirs['waveform']}")

    print(f"Logs     {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
