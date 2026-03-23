"""
DIDO column peripheral layout generation via ML synthesizer.

Generates a sky130A DIDO (Digital-In Digital-Out) cell from the topology
template.  The DIDO block combines precharge, column select, read/write
pass transistors, and write-enable logic (NAND2+NOT).

Usage::

    cd fabram/
    python run_dido.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vendor" / "layout_gen"))

from layout_gen import load_pdk, write_svg
from layout_gen.synth.loader     import load_template
from layout_gen.synth.synthesizer import Synthesizer
from layout_gen.synth.geo.agent  import RuleGeoAgent
from layout_gen.drc.klayout_runner import KLayoutDRCRunner

OUT_GDS = Path(__file__).parent / "dido.gds"
OUT_SVG = Path(__file__).parent / "dido.svg"


def main():
    print("=" * 60)
    print("  DIDO Column Peripheral Layout Generator  (sky130A)")
    print("=" * 60)

    rules = load_pdk()
    tmpl  = load_template("dido")

    print(f"  Template: {tmpl.name} — {tmpl.description}")
    print(f"  Devices:  {len(tmpl.devices)}")
    print(f"  Nets:     {len(tmpl.nets)}")

    # Default sizing: all transistors 0.42/0.15 (matches dido.yaml schematic)
    params = {"w": 0.42, "l": 0.15}

    # DRC runner + geo fix agent
    drc = KLayoutDRCRunner(rules)
    geo = RuleGeoAgent(search_radius=5.0)

    synth = Synthesizer(
        rules,
        drc_runner=drc,
        max_iter=5,
        geo_agent=geo,
        geo_max_iter=40,
    )

    print("\n  Synthesizing ...")
    result = synth.synthesize(tmpl, params)

    comp = result.component
    comp.write_gds(str(OUT_GDS))
    print(f"\n  GDS  -> {OUT_GDS}")

    write_svg(comp, str(OUT_SVG))
    print(f"  SVG  -> {OUT_SVG}")

    print(f"\n  Iterations: {result.iterations}")
    print(f"  Converged:  {result.converged}")
    if result.violations:
        print(f"  Violations:  {len(result.violations)}")
        for v in result.violations[:10]:
            print(f"    {v.rule}: {v.description}")

    print(f"\n  Ports:")
    for p in sorted(comp.ports, key=lambda p: p.name):
        print(f"    {p.name:<8s}  layer={p.layer}  "
              f"center=({p.center[0]:.3f}, {p.center[1]:.3f}) um")

    print("\n  Done.")


if __name__ == "__main__":
    main()
