"""
fabram-char — CLI for SRAM Liberty characterization.

Example::

    fabram-char -w 64 -b 8 -m 4 -o sram_64x8.lib
    fabram-char -w 8  -b 4 -m 1 --vdd 1.8 --temp 27 --stdout
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

import pathlib as _pathlib
import tempfile as _tempfile

from spice_gen.generator.ngspice import NgspiceGenerator
from fabram.generators.top import SRAMCompiler
from liberty_gen import CharConfig, CharCompiler


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fabram-char",
        description="Characterize a fabram SRAM macro and emit a Synopsys Liberty (.lib) file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── SRAM geometry ────────────────────────────────────────────────────────
    p.add_argument("-w", "--words",   type=int, required=True,
                   help="Number of words (must be power of 2, e.g. 64).")
    p.add_argument("-b", "--bits",    type=int, required=True,
                   help="Data width in bits (e.g. 8).")
    p.add_argument("-m", "--mux",     type=int, default=1,
                   help="Column mux ratio (power of 2).")

    # ── PDK / cell source overrides ──────────────────────────────────────────
    p.add_argument("--cells-dir",     type=pathlib.Path, default=None,
                   metavar="DIR",
                   help="Override path to cells/ directory.")
    p.add_argument("--pdk-yaml",      type=pathlib.Path, default=None,
                   metavar="FILE",
                   help="Override path to PDK YAML config.")
    p.add_argument("-c", "--corner",  default="tt",
                   help="PDK process corner (e.g. tt, ff, ss).")

    # ── Operating conditions ─────────────────────────────────────────────────
    p.add_argument("--vdd",    type=float, default=1.8,  help="Supply voltage (V).")
    p.add_argument("--temp",   type=float, default=27.0, help="Temperature (°C).")

    # ── Simulation tuning ────────────────────────────────────────────────────
    p.add_argument("--period",    type=float, default=10.0,
                   help="Test-clock period (ns).  Should be >> expected CLK-to-Q.")
    p.add_argument("--timestep",  type=float, default=0.001,
                   help="ngspice .tran timestep (ns).  1 ps default.")
    p.add_argument("--workers",   type=int,   default=4,
                   help="Parallel ngspice workers (ThreadPoolExecutor).")
    p.add_argument("--timeout",   type=int,   default=180,
                   help="Per-simulation ngspice timeout (seconds).")

    # ── Output ───────────────────────────────────────────────────────────────
    p.add_argument("-o", "--output", type=pathlib.Path, default=None,
                   metavar="FILE",
                   help="Write Liberty to FILE instead of the auto-named path.")
    p.add_argument("--stdout", action="store_true",
                   help="Print Liberty to stdout instead of a file.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable INFO-level logging.")
    return p


def main(argv: list[str] | None = None) -> None:
    p = _build_parser()
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    # ── Build CharConfig from CLI flags ──────────────────────────────────────
    cfg = CharConfig(
        vdd=args.vdd,
        temp=args.temp,
        clk_period=args.period,
        sim_timestep=args.timestep,
        max_workers=args.workers,
        sim_timeout=args.timeout,
    )

    # ── Compile SRAM netlist ─────────────────────────────────────────────────
    try:
        sram = SRAMCompiler(
            words=args.words,
            bits=args.bits,
            col_mux=args.mux,
            cells_dir=args.cells_dir,
            pdk_yaml=args.pdk_yaml,
        )
        netlist = sram.compile(pdk_corner=args.corner)
    except Exception as exc:
        print(f"fabram-char: error compiling SRAM netlist: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── Render netlist to a temp file, then characterize ─────────────────────
    _gen = NgspiceGenerator()
    _spice_text = _gen.generate(netlist)
    with _tempfile.NamedTemporaryFile(
        mode="w", suffix=".sp", prefix="fabram_netlist_", delete=False
    ) as _f:
        _f.write(_spice_text)
        _tmp_path = _f.name
    try:
        char = CharCompiler(
            _tmp_path, sram.geo.name, sram.geo.addr_bits, sram.geo.bits, cfg=cfg
        )
        lib_text = char.characterize()
    except Exception as exc:
        print(f"fabram-char: characterization failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        _pathlib.Path(_tmp_path).unlink(missing_ok=True)

    # ── Output ───────────────────────────────────────────────────────────────
    if args.stdout:
        sys.stdout.write(lib_text)
        return

    if args.output is not None:
        out_path = args.output
    else:
        macro_name = sram.geo.name
        vdd_str = f"{cfg.vdd:.2f}".replace(".", "p")
        temp_str = f"{int(cfg.temp):03d}C"
        out_path = pathlib.Path(f"{macro_name}_{temp_str}_{vdd_str}V.lib")

    out_path.write_text(lib_text)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
