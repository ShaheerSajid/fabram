"""
CharCompiler — orchestrates all characterization sweeps and renders Liberty.

Usage::

    from fabram import SRAMCompiler, CharConfig
    from fabram.characterize import CharCompiler

    sram = SRAMCompiler(words=64, bits=8, col_mux=4)
    netlist = sram.compile()
    char = CharCompiler(netlist, sram.geo)
    lib_text = char.characterize()
    open("sram.lib", "w").write(lib_text)
"""
from __future__ import annotations

import pathlib
import tempfile
import logging

from spice_gen.generator.ngspice import NgspiceGenerator

from fabram.geometry import ArrayGeometry
from fabram.characterize.config import CharConfig
from fabram.characterize.timing import (
    measure_clkq,
    measure_all_setup_hold,
    measure_min_pulse_width,
    measure_leakage,
    measure_dynamic_power,
)
from fabram.characterize.liberty import render_liberty

log = logging.getLogger(__name__)


class CharCompiler:
    """Run all characterization sweeps for a compiled SRAM netlist.

    Parameters
    ----------
    netlist:
        Resolved ``Netlist`` object returned by ``SRAMCompiler.compile()``.
    geo:
        ``ArrayGeometry`` describing the SRAM dimensions.
    cfg:
        Characterization parameters.  Defaults to ``CharConfig()`` which uses
        sky130A-appropriate settings at TT / 27 °C / 1.8 V.
    """

    def __init__(
        self,
        netlist,
        geo: ArrayGeometry,
        cfg: CharConfig | None = None,
    ) -> None:
        self.netlist = netlist
        self.geo = geo
        self.cfg = cfg or CharConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def characterize(self) -> str:
        """Run all sweeps and return a complete Liberty (.lib) string.

        All ngspice sub-processes run against a temporary SPICE netlist file
        that is cleaned up in a ``finally`` block regardless of errors.

        Measurement order
        -----------------
        1. Write SPICE netlist to a named temp file.
        2. Leakage power (standby, quick sim).
        3. Dynamic power (write + read, two sims).
        4. CLK-to-Q grid (parallel across slew × load × q_val).
        5. Setup / hold for all constrained pins (parallel outer).
        6. Minimum CLK pulse width (serial bisection).
        7. Render Liberty string.
        """
        netlist_path = self._write_netlist()
        try:
            return self._run_all(netlist_path)
        finally:
            pathlib.Path(netlist_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_netlist(self) -> str:
        """Render the Netlist to SPICE and write to a named temp file."""
        gen = NgspiceGenerator()
        spice_text = gen.generate(self.netlist)

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".sp",
            prefix="fabram_netlist_",
            delete=False,
        ) as f:
            f.write(spice_text)
            return f.name

    def _run_all(self, netlist_path: str) -> str:
        geo = self.geo
        cfg = self.cfg
        macro = geo.name
        addr_bits = geo.addr_bits
        bits = geo.bits

        log.info("[char] Measuring leakage …")
        leakage_nw = measure_leakage(netlist_path, cfg, macro, addr_bits, bits)
        log.info("[char]   leakage = %.3f nW", leakage_nw)

        log.info("[char] Measuring dynamic power …")
        dyn = measure_dynamic_power(netlist_path, cfg, macro, addr_bits, bits)
        write_power_nw = dyn.get("write_power", 0.0)
        read_power_nw  = dyn.get("read_power",  0.0)
        log.info(
            "[char]   write_power = %.3f nW  read_power = %.3f nW",
            write_power_nw, read_power_nw,
        )

        log.info("[char] Measuring CLK-to-Q (%d slews × %d loads × 2) …",
                 len(cfg.input_slews), len(cfg.output_loads))
        clkq_data = measure_clkq(netlist_path, cfg, macro, addr_bits, bits)
        log.info("[char]   CLK-to-Q done.")

        log.info("[char] Measuring setup/hold for all constrained pins …")
        sh_data = measure_all_setup_hold(netlist_path, cfg, macro, addr_bits, bits)
        log.info("[char]   setup/hold done.")

        log.info("[char] Measuring minimum CLK pulse width …")
        min_pw = measure_min_pulse_width(netlist_path, cfg, macro, addr_bits, bits)
        log.info("[char]   min_pulse_width = %.4f ns", min_pw)

        log.info("[char] Rendering Liberty …")
        lib_str = render_liberty(
            macro_name=macro,
            cfg=cfg,
            addr_bits=addr_bits,
            bits=bits,
            clkq_data=clkq_data,
            setup_hold_data=sh_data,
            min_pw=min_pw,
            leakage_nw=leakage_nw,
            write_power_nw=write_power_nw,
            read_power_nw=read_power_nw,
        )
        log.info("[char] Done.")
        return lib_str
