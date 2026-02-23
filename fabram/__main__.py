"""CLI entry point for fabram SRAM compiler."""
from __future__ import annotations

import argparse
import pathlib
import sys

from spice_gen.generator import get_generator

from fabram.generators.top import SRAMCompiler


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fabram",
        description="Generate a synchronous single-port SRAM macro netlist.",
    )
    p.add_argument("-w", "--words",   type=int, required=True,
                   help="Number of words (depth); must be a power of 2.")
    p.add_argument("-b", "--bits",    type=int, required=True,
                   help="Word width in bits.")
    p.add_argument("-m", "--mux",     type=int, default=1,
                   help="Column mux factor (default 1); must be a power of 2.")
    p.add_argument("-d", "--dialect", choices=["ngspice", "hspice", "spice3"],
                   default="ngspice",
                   help="SPICE dialect for output (default: ngspice).")
    p.add_argument("-c", "--corner",  default="tt",
                   help="PDK corner (default: tt).")
    p.add_argument("-o", "--output",  type=pathlib.Path, default=None,
                   help="Output file path. Defaults to <name>.sp in current directory.")
    p.add_argument("--stdout", action="store_true",
                   help="Write output to stdout instead of a file.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        compiler = SRAMCompiler(args.words, args.bits, args.mux)
        netlist  = compiler.compile(pdk_corner=args.corner)
    except (ValueError, FileNotFoundError) as exc:
        print(f"fabram: error: {exc}", file=sys.stderr)
        return 1

    generator = get_generator(args.dialect)
    text = generator.generate(netlist)

    if args.stdout:
        sys.stdout.write(text)
    else:
        out = args.output or pathlib.Path(f"{compiler.geo.name}.sp")
        out.write_text(text, encoding="utf-8")
        print(f"Written: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
