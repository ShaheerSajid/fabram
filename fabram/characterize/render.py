"""
fabram.characterize.render — YAML-driven subcircuit renderer for BO testbenches.

Loads a cell YAML, substitutes free-parameter transistor widths, resolves PDK
model names (nmos_1v8 → sky130_fd_pr__nfet_01v8, M → X prefix), and returns
the .subckt block(s) ready to embed in a transient testbench.

The .lib include is stripped — each testbench adds its own with the correct corner.

Typical use::

    from fabram.characterize.render import render_subckt, CELLS_DIR, PDK_YAML

    subckt_text, ports = render_subckt(
        CELLS_DIR / "sense_amp.yaml",
        param_values={"W_tail": 1.2, "W_diff": 0.5, ...},
        param_map={"W_tail": ["X0"], "W_diff": ["X1", "X2"], ...},
    )
    # subckt_text: .subckt SENSE_AMP ... .ends SENSE_AMP
    # ports: ["VDD", "VSS", "SAEN", "BL", "BL_", "SB"]
"""
from __future__ import annotations

import pathlib

# ── Repository-relative paths ─────────────────────────────────────────────────

_HERE      = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]          # .../fabram/fabram/characterize/ → .../fabram/

CELLS_DIR = _REPO_ROOT / "cells" / "sram"
PDK_YAML  = _REPO_ROOT / "vendor" / "spice_gen" / "pdks" / "sky130A.yaml"


def _load_pdk_yaml(pdk_yaml: pathlib.Path) -> dict:
    import yaml
    with open(pdk_yaml) as f:
        return yaml.safe_load(f)


def pdk_lib_path(pdk_yaml: pathlib.Path = PDK_YAML) -> str:
    """Return the absolute path to the PDK ngspice .lib file."""
    data = _load_pdk_yaml(pdk_yaml)
    return str(pathlib.Path(data["path"]) / data["lib_file"])


def pdk_transistor_params(pdk_yaml: pathlib.Path = PDK_YAML) -> dict:
    """Return empirical transistor parameters for load/driver estimation.

    Keys: cox_ff_um2, c_ov_ff_um, r_nmos_ohm, r_pmos_ohm.
    """
    data = _load_pdk_yaml(pdk_yaml)
    return data.get("transistor_params", {})


def pdk_model_names(pdk_yaml: pathlib.Path = PDK_YAML) -> dict[str, str]:
    """Return mapping of logical device name → resolved PDK model name.

    Example: ``{"nmos_1v8": "sky130_fd_pr__nfet_01v8", ...}``
    """
    data = _load_pdk_yaml(pdk_yaml)
    return {k: v["pdk_name"] for k, v in data.get("models", {}).items()}


# Convenience module-level values derived from the default PDK YAML.
# Cell specs import these; override by passing pdk_yaml= to make_spec().
PDK_LIB    = pdk_lib_path()
PDK_PARAMS = pdk_transistor_params()
PDK_MODELS = pdk_model_names()

# Backward-compatible alias (was hardcoded to sky130A path previously)
SKY130_LIB = PDK_LIB


# ── Shared analytical helpers ─────────────────────────────────────────────────

def gate_cap_ff(w_um: float, l_um: float | None = None) -> float:
    """Gate capacitance in fF for a transistor of width *w_um* µm.

    Uses Cox and Cov from the default PDK YAML.  *l_um* defaults to
    ``PDK_PARAMS["l_min_um"]`` (minimum channel length).
    """
    l = l_um if l_um is not None else PDK_PARAMS.get("l_min_um", 0.15)
    cox = PDK_PARAMS.get("cox_ff_um2", 12.0)
    cov = PDK_PARAMS.get("c_ov_ff_um",  0.5)
    return cox * w_um * l + cov * w_um * 2


def driver_w(c_ff: float, delay_ns: float, r_ohm: float) -> float:
    """Minimum transistor width (µm) to drive *c_ff* fF within *delay_ns* ns.

    Clamps to the PDK minimum width (``PDK_PARAMS["w_min_um"]``, default 0.42 µm).
    """
    w_min = PDK_PARAMS.get("w_min_um", 0.42)
    return max(w_min, (r_ohm * c_ff * 1e-15) / (delay_ns * 1e-9))


def parse_sim_ns(result: dict, key: str, failed: float = 99.0) -> float:
    """Extract a timing measurement (ns) from an ngspice result dict.

    Returns *failed* if the key is absent, None, negative, or the ngspice
    sentinel value −1.  Use this consistently across all cell extract_metrics
    callbacks to avoid divergent null-handling patterns.
    """
    v = result.get(key)
    if v is None:
        return failed
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return failed
    return fv if fv > 0 else failed


# ── Public API ────────────────────────────────────────────────────────────────

def render_subckt(
    yaml_path:   pathlib.Path | str,
    param_values: dict[str, float],
    param_map:    dict[str, list[str]],
    pdk_yaml:    pathlib.Path | str = PDK_YAML,
    corner:      str = "tt",
) -> tuple[str, list[str]]:
    """Load *yaml_path*, override transistor widths, return (subckt_text, ports).

    Parameters
    ----------
    yaml_path :
        Path to the cell YAML file.
    param_values :
        Mapping of parameter name → width (µm).  Only transistors listed in
        *param_map* are updated; others keep their YAML default widths.
    param_map :
        Mapping of parameter name → list of transistor instance IDs that share
        that width.  Example: ``{"W_diff": ["X1", "X2"]}``.
    pdk_yaml :
        Path to the spice_gen PDK YAML.  Defaults to the bundled sky130A config.
    corner :
        Corner used only to satisfy the resolver API (the .lib line produced
        here is stripped; the testbench adds its own).

    Returns
    -------
    subckt_text :
        One or more ``.subckt ... .ends`` blocks (all dependencies included),
        with resolved model names and X-prefixed instances.  No ``.lib`` line.
    ports :
        Port list of the top-level cell, in declaration order.
    """
    from spice_gen.parser.loader import load_file
    from spice_gen.pdk.resolver import load_pdk, resolve
    from spice_gen.generator.ngspice import NgspiceGenerator
    from spice_gen.model.component import PrimitiveComponent

    netlist = load_file(str(yaml_path))
    top_def = netlist.subckt_defs[-1]

    # ── Mutate widths (must happen before PDK resolution while components are
    #    still PrimitiveComponent objects; resolution converts them to
    #    SubcktInstance, carrying the parameters dict along).
    id_to_param = {
        cid: pname
        for pname, cids in param_map.items()
        for cid in cids
    }
    for comp in top_def.components:
        if isinstance(comp, PrimitiveComponent):
            pname = id_to_param.get(comp.instance_name)
            if pname and pname in param_values:
                comp.parameters["W"] = str(round(float(param_values[pname]), 4))

    # ── Resolve: nmos_1v8 → sky130_fd_pr__nfet_01v8, M → X prefix
    pdk      = load_pdk(str(pdk_yaml))
    resolved = resolve(netlist, pdk, corner=corner)

    # ── Generate and strip .lib line (testbench owns the corner-specific include)
    text  = NgspiceGenerator().generate(resolved)
    lines = [l for l in text.splitlines() if not l.strip().startswith(".lib")]

    return "\n".join(lines).strip(), list(top_def.ports)
