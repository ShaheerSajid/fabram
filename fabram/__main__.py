"""
fabram — synchronous single-port SRAM memory compiler.

Basic usage (netlist only)::

    fabram -w 32 -b 4 -m 4

Full characterization (netlist + Liberty + waveforms)::

    fabram -w 32 -b 4 -m 4 --char

Output layout (relative to CWD, unless --out-dir is overridden)::

    out/<macro>/
    ├── netlist/<macro>.sp          compiled SPICE netlist
    ├── verilog/<macro>.v           Verilog behavioral model   (--verilog)
    ├── lib/<macro>_<cond>.lib      Liberty timing model       (--char only)
    ├── waveform/                   SVG waveform plots         (--char, skippable with --no-waveforms)
    │   ├── clkq_q1.{sp,dat,svg}
    │   ├── clkq_q0.{sp,dat,svg}
    │   ├── leakage.{sp,dat,svg}
    │   ├── power_write.{sp,dat,svg}
    │   └── power_read.{sp,dat,svg}
    └── logs/char.log               characterization log       (--char only)
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
    p.add_argument("-w", "--words",   type=int, default=None,
                   help="Number of words (depth); must be a power of 2.")
    p.add_argument("-b", "--bits",    type=int, default=None,
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
    p.add_argument("--timestep",  type=float, default=0.02,
                   help="ngspice .tran timestep (ns). Default gives ~2000 steps per sim at 10 ns clock.")
    p.add_argument("--workers",   type=int,   default=4,
                   help="Parallel ngspice workers.")
    p.add_argument("--timeout",   type=int,   default=180,
                   help="Per-simulation ngspice timeout (s).")
    p.add_argument("--table-size", type=int,  default=5, metavar="N",
                   help="LUT table dimension N×N (first N entries of default slew/load lists).")
    p.add_argument("--max-iters",  type=int,  default=60,
                   help="Max bisection iterations per setup/hold point.")
    p.add_argument("--no-waveforms", action="store_true",
                   help="Skip waveform SVG generation when --char is set.")
    p.add_argument("--verilog", action="store_true",
                   help="Generate Verilog behavioral model (functional; add --char for timing specify block).")

    # ── Cell optimizer ────────────────────────────────────────────────────────
    p.add_argument("--optimize-cell", action="store_true",
                   help="Optimise a cell template W/L across TT/SS/FF corners.")
    p.add_argument("--cell",
                   choices=["bit_cell", "sense_amp", "row_driver", "write_driver", "dido"],
                   default="bit_cell",
                   help="Which cell template to optimise (default: bit_cell).")
    p.add_argument("--opt-workers", type=int, default=4, metavar="N",
                   help="Parallel ngspice workers for cell optimizer (default: 4).")
    p.add_argument("--opt-evals", type=int, default=60, metavar="N",
                   help="Number of design-point evaluations for cell optimizer (default: 60).")
    p.add_argument("--opt-strategy", choices=["auto", "bo", "lhs"], default="auto",
                   help="Cell optimizer search strategy: bo=Bayesian OP (needs scikit-optimize), "
                        "lhs=Latin Hypercube, auto=bo if available else lhs (default: auto).")

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
        "verilog":  root / "verilog",
        "waveform": root / "waveform",
        "logs":     root / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def _sram_body(words: int, addr_bits: int, bits: int) -> str:
    """Return the Verilog behavioral body for a synchronous single-port SRAM."""
    return (
        f"  localparam NUM_WORDS  = {words};\n"
        f"  localparam DATA_WIDTH = {bits};\n"
        f"  localparam ADDR_WIDTH = {addr_bits};\n"
        f"\n"
        f"  reg [DATA_WIDTH-1:0] mem [0:NUM_WORDS-1];\n"
        f"\n"
        f"  always @(posedge CLK) begin\n"
        f"    if (CS) begin\n"
        f"      if (WRITE)\n"
        f"        mem[addr] <= din;\n"
        f"      else\n"
        f"        Q <= mem[addr];\n"
        f"    end\n"
        f"  end"
    )


def _make_cell_spec(args):
    """Return (CellSpec, out_dir) for the requested --cell, using -w/-b geometry."""
    import pathlib
    cell    = args.cell
    # Use -w/-b if given; otherwise fall back to sensible defaults
    words   = args.words or 64
    bits    = args.bits  or 8
    mux     = args.mux   or 1
    num_rows = words
    num_cols = bits * mux   # total columns = data-width × mux-factor

    if cell == "bit_cell":
        from fabram.characterize.cells.bit_cell import make_spec
        return make_spec(), pathlib.Path("out") / "bit_cell_opt"

    if cell == "sense_amp":
        from fabram.characterize.cells.sense_amp import make_spec
        return make_spec(num_rows=num_rows), pathlib.Path("out") / "sense_amp_opt"

    if cell == "row_driver":
        from fabram.characterize.cells.row_driver import make_spec
        return make_spec(num_cols=num_cols), pathlib.Path("out") / "row_driver_opt"

    if cell == "write_driver":
        from fabram.characterize.cells.write_driver import make_spec
        return make_spec(num_rows=num_rows), pathlib.Path("out") / "write_driver_opt"

    if cell == "dido":
        from fabram.characterize.cells.dido import make_spec
        return make_spec(num_rows=num_rows), pathlib.Path("out") / "dido_opt"

    raise ValueError(f"Unknown cell: {cell}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # ── Cell W/L optimizer (standalone; no SRAM geometry required) ───────────
    if args.optimize_cell:
        from fabram.characterize import run_optimizer
        spec, out = _make_cell_spec(args)
        rec = run_optimizer(
            spec, out,
            n_evals=args.opt_evals,
            max_workers=args.opt_workers,
            strategy=args.opt_strategy,
        )
        params_str = "  ".join(f"{k}={v:.3f}µm" for k, v in rec.params.items())
        worst_str  = "  ".join(f"{m}={v:.4f}" for m, v in rec.worst.items())
        print(f"Recommended: {params_str}  {worst_str}")
        return 0

    # ── Validate SRAM geometry (required unless --optimize-cell) ─────────────
    if args.words is None or args.bits is None:
        _build_parser().error("arguments -w/--words and -b/--bits are required")

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
    netlist_path = netlist_path.resolve()  # absolute path for testbench .include directives

    # ── Verilog model (with timing if an existing .lib is found) ─────────────
    if args.verilog and not args.char:
        from verilog_gen import Port, LibertyCell, generate_verilog, parse_cell
        existing_libs = sorted(dirs["lib"].glob(f"{macro}_*.lib"))
        if existing_libs:
            _cell = parse_cell(str(existing_libs[-1]))   # newest lib alphabetically
            for _p in _cell.ports:
                if _p.direction == "output":
                    _p.is_reg = True
            _timing_note = "  (timing)"
        else:
            _ports = [
                Port("CLK",   "input",  1, is_clock=True),
                Port("CS",    "input",  1),
                Port("WRITE", "input",  1),
                Port("addr",  "input",  compiler.geo.addr_bits),
                Port("din",   "input",  compiler.geo.bits),
                Port("Q",     "output", compiler.geo.bits, is_reg=True),
            ]
            _cell = LibertyCell(name=macro, ports=_ports)
            _timing_note = "  (functional only — run --char first for timing)"
        verilog_text = generate_verilog(
            _cell,
            behavioral_body=_sram_body(
                compiler.geo.words, compiler.geo.addr_bits, compiler.geo.bits),
        )
        verilog_path = dirs["verilog"] / f"{macro}.v"
        verilog_path.write_text(verilog_text, encoding="utf-8")
        print(f"Verilog  {verilog_path}{_timing_note}")

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

    # Suppress noisy DEBUG spam from matplotlib font-cache resolution.
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)

    _ALL_SLEWS = [0.02, 0.05, 0.1, 0.2, 0.5]
    _ALL_LOADS = [0.001, 0.005, 0.01, 0.05, 0.1]
    n = min(args.table_size, 5)
    cfg = CharConfig(
        vdd=args.vdd,
        temp=args.temp,
        input_slews=_ALL_SLEWS[:n],
        output_loads=_ALL_LOADS[:n],
        clk_period=args.period,
        sim_timestep=args.timestep,
        max_workers=args.workers,
        sim_timeout=args.timeout,
        max_iterations=args.max_iters,
    )

    try:
        char = CharCompiler(
            str(netlist_path), macro,
            compiler.geo.addr_bits, compiler.geo.bits,
            cfg=cfg,
            flop_subckt={
                "name":     "MS_REG",
                "clk_port": compiler.cfg.reg_clk,
                "d_port":   compiler.cfg.reg_d,
                "q_port":   compiler.cfg.reg_q,
            },
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

    # ── Verilog timing model ───────────────────────────────────────────────────
    if args.verilog:
        from verilog_gen import parse_cell, generate_verilog
        _cell = parse_cell(str(lib_path))
        for _p in _cell.ports:
            if _p.direction == "output":
                _p.is_reg = True
        verilog_text = generate_verilog(
            _cell,
            behavioral_body=_sram_body(
                compiler.geo.words, compiler.geo.addr_bits, compiler.geo.bits),
        )
        verilog_path = dirs["verilog"] / f"{macro}.v"
        verilog_path.write_text(verilog_text, encoding="utf-8")
        print(f"Verilog  {verilog_path}  (timing)")

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
