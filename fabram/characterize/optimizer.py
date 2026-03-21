"""
fabram.characterize.optimizer — technology-portable cell sizing engine.

Finds Pareto-optimal transistor widths for any cell template by running SPICE
simulations across PVT corners and searching the parameter space with Bayesian
Optimisation (or quasi-random Latin Hypercube as a no-extra-dep fallback).

Search strategies
-----------------
``"bo"``
    Gaussian Process BO via scikit-optimize.  Sequential; each evaluation
    informs the next.  Requires ``pip install scikit-optimize``.
``"lhs"``
    Quasi-random Latin Hypercube Sampling via scipy.  Fully parallel.
    Falls back to uniform-random sampling if scipy is absent.
``"auto"`` (default)
    Uses BO when scikit-optimize is importable, otherwise LHS.

Usage::

    from fabram.characterize import run_optimizer
    from fabram.characterize.cells.bit_cell import make_spec

    rec = run_optimizer(make_spec(), pathlib.Path("out/bit_cell_opt"))
"""
from __future__ import annotations

import csv
import json
import logging
import math
import pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Literal

log = logging.getLogger(__name__)


# ── Public data structures ────────────────────────────────────────────────────

@dataclass
class Param:
    """A continuous free sizing parameter (e.g. transistor width in µm)."""
    name: str
    low:  float
    high: float


@dataclass
class Objective:
    """A scalar metric and its optimisation direction."""
    metric:    str
    direction: Literal["maximize", "minimize"]


@dataclass
class CellSpec:
    """Complete specification for optimising one cell template.

    Attributes
    ----------
    name :
        Human-readable cell name; used in log messages and output filenames.
    params :
        Free continuous parameters (widths, lengths …).
    build_decks :
        ``build_decks(param_values: dict, corner: dict) → list[str]``
        Returns one SPICE deck string per measurement type for this corner.
    extract_metrics :
        ``extract_metrics(results: list[dict], params: dict) → dict[str, float]``
        *results* is a list of dicts from run_ngspice — one per deck returned
        by *build_decks*; *params* is the same param_values dict passed to
        *build_decks*.  Returns a flat metric-name → value mapping.
    objectives :
        Objectives used for Pareto dominance and scalarisation.
    corners :
        Corner definitions passed verbatim to *build_decks*.  Defaults to
        sky130A TT / SS / FF.
    """
    name:            str
    params:          list[Param]
    build_decks:     Callable[[dict, dict], list[str]]
    extract_metrics: Callable[[list[dict], dict], dict[str, float]]
    objectives:      list[Objective]
    corners: dict[str, dict] = field(default_factory=lambda: {
        "tt": dict(vdd=1.8, temp=  27, lib_corner="tt"),
        "ss": dict(vdd=1.6, temp= 125, lib_corner="ss"),
        "ff": dict(vdd=2.0, temp= -40, lib_corner="ff"),
    })


@dataclass
class OptResult:
    """One evaluated design point with per-corner and worst-case metrics."""
    params:             dict[str, float]
    metrics_by_corner:  dict[str, dict[str, float]]
    worst:              dict[str, float]   # metric → worst-case across corners
    is_pareto:          bool = False


# ── Public entry point ────────────────────────────────────────────────────────

def run_optimizer(
    spec:        CellSpec,
    out_dir:     pathlib.Path,
    *,
    n_evals:     int = 60,
    max_workers: int = 4,
    timeout:     int = 120,
    strategy:    str = "auto",
) -> OptResult:
    """Optimise *spec* and write results under *out_dir*.

    Parameters
    ----------
    n_evals :
        Total design-point evaluations.  Each evaluation runs all corners,
        so total SPICE calls ≈ ``n_evals × len(spec.corners)``.
    max_workers :
        Thread-pool size.  For BO the corners for each point run in parallel;
        for LHS all points run in parallel.
    strategy :
        ``"bo"``, ``"lhs"``, or ``"auto"``.
    """
    out_dir = pathlib.Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    effective = _resolve_strategy(strategy)
    log.info("[opt:%s] %d params × %d corners × %d evals  strategy=%s",
             spec.name, len(spec.params), len(spec.corners), n_evals, effective)

    if effective == "bo":
        results = _run_bo(spec, n_evals, max_workers, timeout)
    else:
        results = _run_lhs(spec, n_evals, max_workers, timeout)

    results = _mark_pareto(results, spec)
    rec     = _recommend(results, spec)

    _save_results(results, rec, spec, out_dir)
    _plot(results, rec, spec, out_dir)
    _log_recommendation(rec, spec)
    return rec


# ── Strategy selection ────────────────────────────────────────────────────────

def _resolve_strategy(strategy: str) -> str:
    if strategy in ("bo", "lhs"):
        return strategy
    try:
        import skopt  # noqa: F401
        return "bo"
    except ImportError:
        log.warning(
            "[opt] scikit-optimize not found; falling back to LHS sampling.  "
            "Install with: pip install scikit-optimize"
        )
        return "lhs"


# ── Single-point evaluation ───────────────────────────────────────────────────

def _eval_point(
    spec:        CellSpec,
    param_values: dict[str, float],
    max_workers: int,
    timeout:     int,
) -> OptResult:
    """Evaluate one design point; corners run in parallel."""
    from liberty_gen.runner import run_ngspice

    def _run_corner(corner_key: str, corner: dict) -> tuple[str, dict]:
        decks   = spec.build_decks(param_values, corner)
        results = [run_ngspice(deck, timeout) for deck in decks]
        return corner_key, spec.extract_metrics(results, param_values)

    metrics_by_corner: dict[str, dict] = {}
    n_threads = min(max_workers, len(spec.corners))
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = {ex.submit(_run_corner, ck, c): ck
                for ck, c in spec.corners.items()}
        for fut in as_completed(futs):
            ck = futs[fut]
            try:
                ck, metrics = fut.result()
                metrics_by_corner[ck] = metrics
            except Exception as exc:
                log.warning("[opt:%s] corner %s failed (%s): %s",
                            spec.name, ck, param_values, exc)
                metrics_by_corner[ck] = {obj.metric: 0.0 for obj in spec.objectives}

    worst = _compute_worst(metrics_by_corner, spec)
    return OptResult(params=param_values, metrics_by_corner=metrics_by_corner, worst=worst)


def _compute_worst(metrics_by_corner: dict, spec: CellSpec) -> dict[str, float]:
    worst: dict[str, float] = {}
    for obj in spec.objectives:
        vals = [metrics_by_corner[ck].get(obj.metric, 0.0)
                for ck in metrics_by_corner]
        worst[obj.metric] = min(vals) if obj.direction == "maximize" else max(vals)
    return worst


# ── Scalarisation ─────────────────────────────────────────────────────────────

def _scalarize(
    result:  OptResult,
    spec:    CellSpec,
    history: list[OptResult],
) -> float:
    """Geometric mean of min-max-normalised worst-case objective values.

    Minimise objectives are inverted so that higher is always better.
    Normalisation uses the range seen across *history* + *result*.
    Returns a value in (0, 1].
    """
    all_results = history + [result]
    scores: list[float] = []
    for obj in spec.objectives:
        vals  = [r.worst.get(obj.metric, 0.0) for r in all_results]
        v_min = min(vals)
        v_max = max(vals)
        v     = result.worst.get(obj.metric, 0.0)
        norm  = (v - v_min) / (v_max - v_min) if v_max > v_min else 0.5
        if obj.direction == "minimize":
            norm = 1.0 - norm
        scores.append(max(1e-9, norm))
    n = len(scores)
    return math.exp(sum(math.log(s) for s in scores) / n)


# ── Bayesian Optimisation ─────────────────────────────────────────────────────

def _run_bo(
    spec:        CellSpec,
    n_evals:     int,
    max_workers: int,
    timeout:     int,
) -> list[OptResult]:
    """Sequential GP-BO via scikit-optimize; corners run in parallel per point."""
    from skopt import Optimizer
    from skopt.space import Real

    space     = [Real(p.low, p.high, name=p.name) for p in spec.params]
    n_initial = max(10, n_evals // 3)
    opt       = Optimizer(space, base_estimator="GP",
                          n_initial_points=n_initial,
                          acq_func="EI", random_state=42)

    results: list[OptResult] = []
    for i in range(n_evals):
        x  = opt.ask()
        pv = {p.name: float(v) for p, v in zip(spec.params, x)}
        r  = _eval_point(spec, pv, max_workers, timeout)
        results.append(r)

        valid_so_far = [p for p in results[:-1] if _is_valid(p, spec)]
        score = _scalarize(r, spec, valid_so_far)
        opt.tell(x, -score)   # skopt minimises

        log.info("[opt:%s] BO %d/%d  score=%.4f  %s",
                 spec.name, i + 1, n_evals, score,
                 "  ".join(f"{k}={v:.3f}" for k, v in pv.items()))

    return results


# ── Latin Hypercube Sampling ──────────────────────────────────────────────────

def _run_lhs(
    spec:        CellSpec,
    n_evals:     int,
    max_workers: int,
    timeout:     int,
) -> list[OptResult]:
    """Fully parallel LHS sampling; no ML deps required."""
    points = _lhs_points(spec.params, n_evals)
    total  = len(points)
    results: list[OptResult] = []
    done   = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_eval_point, spec, pv, 1, timeout): pv
                for pv in points}
        for fut in as_completed(futs):
            pv = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:
                log.warning("[opt:%s] eval failed %s: %s", spec.name, pv, exc)
                r = OptResult(
                    params=pv,
                    metrics_by_corner={},
                    worst={obj.metric: 0.0 for obj in spec.objectives},
                )
            results.append(r)
            done += 1
            if done % 10 == 0 or done == total:
                log.info("[opt:%s] LHS %d/%d", spec.name, done, total)

    return results


def _lhs_points(params: list[Param], n: int) -> list[dict[str, float]]:
    try:
        from scipy.stats import qmc
        sampler = qmc.LatinHypercube(d=len(params), seed=42)
        sample  = sampler.random(n=n)
        scaled  = qmc.scale(sample, [p.low for p in params], [p.high for p in params])
        return [{p.name: float(v) for p, v in zip(params, row)} for row in scaled]
    except ImportError:
        import numpy as np
        rng = np.random.default_rng(42)
        return [
            {p.name: float(rng.uniform(p.low, p.high)) for p in params}
            for _ in range(n)
        ]


# ── Pareto analysis ───────────────────────────────────────────────────────────

_FAILURE_SENTINEL = 90.0  # any minimize-objective worst ≥ this is a failed sim


def _is_valid(result: OptResult, spec: CellSpec) -> bool:
    """False if any minimize-direction objective hit the failure sentinel."""
    return all(
        result.worst.get(obj.metric, _FAILURE_SENTINEL) < _FAILURE_SENTINEL
        for obj in spec.objectives
        if obj.direction == "minimize"
    )


def _mark_pareto(results: list[OptResult], spec: CellSpec) -> list[OptResult]:
    valid = [r for r in results if _is_valid(r, spec)]
    for i, r in enumerate(results):
        if not _is_valid(r, spec):
            r.is_pareto = False
            continue
        r.is_pareto = not any(
            _dominates(s, r, spec) for j, s in enumerate(valid) if s is not r
        )
    return results


def _dominates(s: OptResult, r: OptResult, spec: CellSpec) -> bool:
    """True if *s* dominates *r* on all objectives with strict improvement on one."""
    at_least = True
    strictly  = False
    for obj in spec.objectives:
        sv = s.worst.get(obj.metric, 0.0)
        rv = r.worst.get(obj.metric, 0.0)
        if obj.direction == "maximize":
            if sv < rv:
                at_least = False; break
            if sv > rv:
                strictly = True
        else:
            if sv > rv:
                at_least = False; break
            if sv < rv:
                strictly = True
    return at_least and strictly


# ── Recommendation ────────────────────────────────────────────────────────────

def _recommend(results: list[OptResult], spec: CellSpec) -> OptResult:
    """Best Pareto member by geometric mean of normalised worst-case objectives."""
    valid = [r for r in results if _is_valid(r, spec)]
    pareto = [r for r in valid if r.is_pareto]
    pool = pareto or valid or results
    return max(pool, key=lambda r: _scalarize(r, spec, valid or results))


# ── Output ────────────────────────────────────────────────────────────────────

def _save_results(
    results: list[OptResult],
    rec:     OptResult,
    spec:    CellSpec,
    out_dir: pathlib.Path,
) -> None:
    metric_names  = [obj.metric for obj in spec.objectives]
    corner_keys   = list(spec.corners.keys())
    param_fields  = [p.name for p in spec.params]
    corner_fields = [f"{m}_{ck}" for m in metric_names for ck in corner_keys]
    worst_fields  = [f"{m}_worst" for m in metric_names]
    all_fields    = param_fields + corner_fields + worst_fields + ["is_pareto"]

    def _row(r: OptResult) -> dict:
        d: dict = {pn: round(r.params.get(pn, 0.0), 4) for pn in param_fields}
        for m in metric_names:
            for ck in corner_keys:
                d[f"{m}_{ck}"] = round(r.metrics_by_corner.get(ck, {}).get(m, 0.0), 6)
            d[f"{m}_worst"] = round(r.worst.get(m, 0.0), 6)
        d["is_pareto"] = r.is_pareto
        return d

    primary   = metric_names[0]
    sorted_r  = sorted(results, key=lambda r: -r.worst.get(primary, 0.0))
    pareto_r  = [r for r in sorted_r if r.is_pareto]

    with open(out_dir / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        w.writerows(_row(r) for r in sorted_r)

    with open(out_dir / "pareto.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        w.writerows(_row(r) for r in pareto_r)

    rec_d = {p: round(v, 4) for p, v in rec.params.items()}
    rec_d.update({f"{m}_worst": round(rec.worst.get(m, 0.0), 6) for m in metric_names})
    rec_d["note"] = (
        f"Recommended: best geometric mean of worst-case objectives "
        f"({', '.join(metric_names)}) on the Pareto front."
    )
    with open(out_dir / "recommended.json", "w") as f:
        json.dump(rec_d, f, indent=2)

    log.info("[opt:%s] results.csv (%d rows)  pareto.csv (%d rows)  recommended.json",
             spec.name, len(results), len(pareto_r))


def _plot(
    results: list[OptResult],
    rec:     OptResult,
    spec:    CellSpec,
    out_dir: pathlib.Path,
) -> None:
    """2-D Pareto scatter — only rendered when there are exactly two objectives."""
    if len(spec.objectives) != 2:
        return
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("[opt:%s] matplotlib not available — skipping plot", spec.name)
        return

    obj_x, obj_y = spec.objectives
    mx, my = obj_x.metric, obj_y.metric

    valid   = [r for r in results if _is_valid(r, spec)]
    failed  = [r for r in results if not _is_valid(r, spec)]
    pareto  = [r for r in valid if r.is_pareto]

    val_x  = [r.worst.get(mx, 0.0) for r in valid]
    val_y  = [r.worst.get(my, 0.0) for r in valid]
    fail_y = [r.worst.get(my, 0.0) for r in failed]
    par_x  = [r.worst.get(mx, 0.0) for r in pareto]
    par_y  = [r.worst.get(my, 0.0) for r in pareto]

    fig, ax = plt.subplots(figsize=(7, 5))
    if failed:
        # Show failed sims at the right edge as hollow X markers
        x_fail = max(val_x) * 1.05 if val_x else 1.0
        ax.scatter([x_fail] * len(failed), fail_y, s=30, marker="x",
                   color="lightgray", zorder=1, label=f"Failed ({len(failed)})")
    ax.scatter(val_x, val_y, s=30, alpha=0.6, zorder=2, label=f"Valid ({len(valid)})")
    ax.scatter(par_x, par_y, s=70, edgecolors="red", facecolors="none",
               linewidths=1.5, zorder=3, label="Pareto front")
    ax.scatter([rec.worst.get(mx, 0.0)], [rec.worst.get(my, 0.0)],
               s=160, marker="*", color="gold", edgecolors="black",
               linewidths=0.8, zorder=4, label="Recommended")
    ax.set_xlabel(f"{mx} worst-case", fontsize=10)
    ax.set_ylabel(f"{my} worst-case", fontsize=10)
    ax.set_title(f"{spec.name} — Pareto front", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_dir / "pareto.png", dpi=150)
    plt.close(fig)
    log.info("[opt:%s] pareto.png saved", spec.name)


def _log_recommendation(rec: OptResult, spec: CellSpec) -> None:
    params_str = "  ".join(f"{k}={v:.3f}µm" for k, v in rec.params.items())
    worst_str  = "  ".join(
        f"{obj.metric}_worst={rec.worst.get(obj.metric, 0.0):.4f}"
        for obj in spec.objectives
    )
    log.info("[opt:%s] Recommended: %s  %s", spec.name, params_str, worst_str)
