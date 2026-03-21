# fabram

A parametric SRAM compiler targeting the **sky130A** PDK (or any technology supported by `spice_gen`). Given three integers — **words**, **bits**, and **column mux factor** — it assembles a fully hierarchical SPICE netlist from hand-crafted cell templates, then optionally characterizes it to produce a Liberty timing model and a Verilog behavioral model with a `specify` block.

---

## Table of Contents

1. [How it works](#how-it-works)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Quick start](#quick-start)
5. [CLI reference](#cli-reference)
6. [Output layout](#output-layout)
7. [Cell optimizer](#cell-optimizer)
8. [Python API](#python-api)
9. [Array geometry](#array-geometry)
10. [Netlist hierarchy](#netlist-hierarchy)
11. [Custom cell port names](#custom-cell-port-names)
12. [Project layout](#project-layout)

---

## How it works

```
fabram -w 32 -b 4 -m 4 --char --verilog
```

```
┌─────────────────────────────────────────────────────────────────┐
│  1. Compile                                                     │
│     SRAMCompiler reads cell YAMLs (cells/sram/) + PDK YAML     │
│     and assembles a hierarchical SPICE netlist                  │
│                         │                                       │
│  2. Characterize  (--char)                                      │
│     CharCompiler runs ngspice simulations to measure:           │
│       • CLK-to-Q delay (3×3 LUT: slew × load)                  │
│       • Setup / hold times for every data input                 │
│       • Leakage and dynamic power                               │
│     Output: Liberty .lib file                                   │
│                         │                                       │
│  3. Verilog model  (--verilog)                                  │
│     generate_verilog() produces a behavioral model with a       │
│     specify block parsed from the Liberty file                  │
│     Output: .v file with $setuphold / $width checks             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Requirements

| Requirement | Notes |
|---|---|
| Python ≥ 3.10 | |
| ngspice | Required for `--char`; must be on `PATH` |
| sky130A PDK | Default location: `/usr/local/share/pdk/sky130A/` |
| `spice_gen` | Vendored submodule — SPICE netlist generation |
| `liberty_gen` | Vendored submodule — Liberty characterization |
| `verilog_gen` | Vendored submodule — Verilog model generation |

For the cell optimizer (`--optimize-cell`) with Bayesian search, also install:
```bash
pip install scikit-optimize
```

---

## Installation

```bash
git clone --recurse-submodules <repo-url>
cd fabram

# Install vendored submodules first, then fabram itself
pip install -e vendor/spice_gen
pip install -e vendor/liberty_gen
pip install -e vendor/verilog_gen
pip install -e .
```

> **Tip:** If you forgot `--recurse-submodules`, run `git submodule update --init --recursive` to pull them in.

---

## Quick start

```bash
# Minimal: compile a 64-word × 8-bit SRAM netlist (column mux = 4)
fabram -w 64 -b 8 -m 4

# Add a Verilog functional model
fabram -w 64 -b 8 -m 4 --verilog

# Full flow: netlist + Liberty + Verilog timing model + waveform SVGs
fabram -w 64 -b 8 -m 4 --char --verilog

# Characterize only (no waveforms, faster)
fabram -w 64 -b 8 -m 4 --char --no-waveforms
```

All outputs are written under `out/SRAM_64x8_CM4/`.

---

## CLI reference

### Geometry

| Flag | Default | Description |
|---|---|---|
| `-w / --words` | required | Memory depth — must be a power of 2 |
| `-b / --bits` | required | Word width in bits |
| `-m / --mux` | `1` | Column mux factor — must be a power of 2, ≤ words |

### PDK and cell library

| Flag | Default | Description |
|---|---|---|
| `-d / --dialect` | `ngspice` | SPICE output dialect: `ngspice`, `hspice`, `spice3` |
| `-c / --corner` | `tt` | PDK process corner for the netlist (e.g. `tt`, `ff`, `ss`) |
| `--cells-dir` | built-in `cells/` | Path to an alternative cell YAML library |
| `--pdk-yaml` | built-in `sky130A.yaml` | Path to a `spice_gen` PDK YAML for another technology |

### Output

| Flag | Default | Description |
|---|---|---|
| `--out-dir` | `out/` | Root directory for all generated files |
| `-o / --output` | `out/<name>/netlist/<name>.sp` | Override the netlist output path |
| `--stdout` | — | Print netlist to stdout (skips all other outputs) |
| `--verilog` | — | Generate a Verilog behavioral model (picks up timing from an existing `.lib` automatically) |
| `--char` | — | Run Liberty characterization (ngspice required) |
| `--no-waveforms` | — | Skip SVG waveform generation when using `--char` |

### Characterization (only used with `--char`)

| Flag | Default | Description |
|---|---|---|
| `--vdd` | `1.8` | Supply voltage (V) |
| `--temp` | `27.0` | Temperature (°C) |
| `--period` | `10.0` | Test-clock period (ns) |
| `--timestep` | `0.02` | ngspice `.tran` timestep (ns) |
| `--workers` | `4` | Number of parallel ngspice processes |
| `--timeout` | `180` | Per-simulation timeout (seconds) |
| `--table-size` | `5` | LUT dimension — N slew points × N load points (max 5) |
| `--max-iters` | `60` | Bisection iterations per setup/hold measurement |

### Examples

```bash
# Slow-slow corner, hspice dialect, custom output path
fabram -w 256 -b 32 -m 8 -d hspice -c ss -o sram_256x32.sp

# Different technology: bring your own cell YAMLs and PDK YAML
fabram -w 64 -b 8 -m 4 \
       --cells-dir cells_gf180/ \
       --pdk-yaml  pdks/gf180mcu.yaml

# Characterize at a non-default corner and temperature
fabram -w 32 -b 4 -m 4 --char --vdd 1.62 --temp 85 --corner ss
```

---

## Output layout

After a full `--char --verilog` run the output directory looks like:

```
out/SRAM_64x8_CM4/
├── netlist/
│   └── SRAM_64x8_CM4.sp              SPICE netlist
├── verilog/
│   └── SRAM_64x8_CM4.v               Verilog model with specify block
├── lib/
│   └── SRAM_64x8_CM4_027C_1p80V.lib  Liberty timing model
├── waveform/
│   ├── clkq_q1.{sp,dat,svg}          CLK-to-Q, Q rises (read 1)
│   ├── clkq_q0.{sp,dat,svg}          CLK-to-Q, Q falls (read 0)
│   ├── leakage.{sp,dat,svg}
│   ├── power_write.{sp,dat,svg}
│   └── power_read.{sp,dat,svg}
└── logs/
    └── char.log                       Full ngspice simulation log
```

**Verilog model** (`--verilog`):
- If a `.lib` already exists in `lib/`, timing is extracted from it automatically — no need to re-run `--char`.
- If no `.lib` exists yet, a functional-only model is written and a hint is printed.
- The `specify` block is guarded by `` `ifdef FUNCTIONAL `` so functional simulations can skip it with `+define+FUNCTIONAL`.

---

## Cell optimizer

The built-in optimizer sizes individual cell transistors by simulating the cell directly and minimizing a Pareto objective (speed vs. area). It is independent of the full SRAM flow.

```bash
# Optimize the DIDO precharge/write-driver for a 64-row array
fabram --optimize-cell --cell dido

# Optimize the row driver for a specific geometry
fabram --optimize-cell --cell row_driver -w 128 -b 8 -m 4

# Use more evaluations and parallel workers
fabram --optimize-cell --cell sense_amp --opt-evals 100 --opt-workers 8
```

**Available cells:**

| Cell | Free parameters | Objectives |
|---|---|---|
| `bit_cell` | W_PD, W_PU, W_PG | maximize SNM and write margin |
| `sense_amp` | W_tail, W_diff, W_inv, W_latch, W_out | minimize t_sense, w_total |
| `row_driver` | W_buf_p, W_buf_n | minimize t_wl_rise, w_total |
| `write_driver` | W_drv_p, W_drv_n | minimize t_drive, w_total |
| `dido` | W_pchg, W_wr | minimize t_cycle, w_total |

**Optimizer flags:**

| Flag | Default | Description |
|---|---|---|
| `--cell` | `bit_cell` | Which cell template to optimize |
| `--opt-evals` | `60` | Total design-point evaluations |
| `--opt-workers` | `4` | Parallel ngspice workers |
| `--opt-strategy` | `auto` | `bo` (Bayesian, needs scikit-optimize), `lhs` (Latin Hypercube), `auto` |

The initial search bounds are estimated analytically from the array geometry (BL/WL capacitive load, target delay) and then explored by the optimizer. Results are written to `out/<cell>_opt/`.

---

## Python API

```python
from fabram import SRAMCompiler

# Default sky130A
netlist = SRAMCompiler(words=64, bits=8, col_mux=4).compile(pdk_corner="tt")

# Different technology
from pathlib import Path
netlist = SRAMCompiler(
    words=64, bits=8, col_mux=4,
    cells_dir=Path("cells_gf180"),
    pdk_yaml=Path("pdks/gf180mcu.yaml"),
).compile(pdk_corner="tt")
```

`compile()` returns a `spice_gen` `Netlist` object. Render it with any dialect generator:

```python
from spice_gen.generator import get_generator
spice_text = get_generator("ngspice").generate(netlist)
```

---

## Array geometry

The compiler maps logical dimensions to a physical array as follows:

| Parameter | Formula | Example (64 × 8, mux=4) |
|---|---|---|
| `num_rows` | `words // col_mux` | 16 rows |
| `num_cols` | `bits × col_mux` | 32 physical columns |
| `row_addr_bits` | `log₂(num_rows)` | 4 bits |
| `col_addr_bits` | `log₂(col_mux)` | 2 bits |
| `addr_bits` (total) | `row_addr_bits + col_addr_bits` | 6 bits |

The column mux trades off row count against column count. A higher mux factor gives fewer, longer rows and more sharing of the sense amplifiers and write drivers across multiple bit-cell columns.

---

## Netlist hierarchy

Every compiled SRAM is a single top-level subcircuit that instantiates the following blocks:

```
SRAM_{words}x{bits}_CM{mux}
│
├── XINREG  INPUT_REG_{addr_bits+1}    Registers addr[N:0] + WRITE on CLK rising edge
├── XCTRL   SELF_TIMED_CTRL            Generates WREN, PCHG, WLEN, SAEN control pulses
│
├── XRDEC   ROW_DEC_{num_rows}         Row address → one-hot word-line select
├── XRDRVR  ROW_DRV_ARR_{num_rows}     Word-line buffers (high-drive inverter + NAND2 gate)
│
├── XCDEC   ROW_DEC_{col_mux}          Column address → one-hot column select  (mux > 1 only)
├── XCDRVR  COL_DRV_ARR_{col_mux}      Column-select drivers                   (mux > 1 only)
│
├── XMAT    MAT_{num_rows}x{num_cols}  6T bit-cell array (num_rows × num_cols instances)
├── XDMY    DMY_ARR_{num_rows}         Dummy bit-cell row (provides reference for sense amps)
│
├── XDIDO   DIDO_ARR_{num_cols}        Precharge PMOS + column select + read/write pass gates
├── XDATAIN DATAIN_ARR_{bits}          Write data input registers and write drivers
└── XSE     SE_ARR_{bits}             Sense amplifiers → Q[bits-1:0]
```

---

## Custom cell port names

If your cell library uses different port names from the defaults, pass a `CellPorts` config object:

```python
from fabram import SRAMCompiler, CellPorts

cfg = CellPorts(
    sa_inp="INP",    # SENSE_AMP BL+ input  (default: "BL")
    sa_inn="INN",    # SENSE_AMP BL- input  (default: "BL_")
    sa_out="Q_OUT",  # SENSE_AMP output     (default: "SB")
    reg_clk="CK",    # MS_REG clock         (default: "clk")
)
netlist = SRAMCompiler(64, 8, col_mux=4, cell_ports=cfg).compile()
```

All configurable port names:

| Field | Default | Cell |
|---|---|---|
| `cell_wl` | `WL` | BIT_CELL, DMY_CELL |
| `cell_bl` | `BL` | BIT_CELL, DMY_CELL |
| `cell_bl_` | `BL_` | BIT_CELL, DMY_CELL |
| `sa_en` | `SAEN` | SENSE_AMP |
| `sa_inp` | `BL` | SENSE_AMP |
| `sa_inn` | `BL_` | SENSE_AMP |
| `sa_out` | `SB` | SENSE_AMP |
| `reg_clk` | `clk` | MS_REG |
| `reg_d` | `D` | MS_REG |
| `reg_q` | `Q` | MS_REG |
| `dido_pchg` | `PCHG` | DIDO |
| `dido_wren` | `WREN` | DIDO |
| `dido_sel` | `SEL` | DIDO |
| `dido_bl` | `BL` | DIDO |
| `dido_bl_` | `BL_` | DIDO |
| `dido_dw` | `DW` | DIDO |
| `dido_dw_` | `DW_` | DIDO |
| `dido_dr` | `DR` | DIDO |
| `dido_dr_` | `DR_` | DIDO |
| `drv_en` | `WLEN` | ROW_DRIVER |
| `drv_in` | `A` | ROW_DRIVER |
| `drv_out` | `B` | ROW_DRIVER |
| `wd_en` | `WREN` | WRITE_DRIVER |
| `wd_in` | `Din` | WRITE_DRIVER |
| `wd_dw` | `DW` | WRITE_DRIVER |
| `wd_dw_` | `DW_` | WRITE_DRIVER |
| `ctrl_clk` | `clk` | SELF_TIMED_CTRL |
| `ctrl_cs` | `cs` | SELF_TIMED_CTRL |
| `ctrl_write` | `write` | SELF_TIMED_CTRL |
| `ctrl_dbl` | `DBL` | SELF_TIMED_CTRL |
| `ctrl_dbl_` | `DBL_` | SELF_TIMED_CTRL |
| `ctrl_wren` | `WREN` | SELF_TIMED_CTRL |
| `ctrl_pchg` | `PCHG` | SELF_TIMED_CTRL |
| `ctrl_wlen` | `WLEN` | SELF_TIMED_CTRL |
| `ctrl_saen` | `SAEN` | SELF_TIMED_CTRL |

---

## Project layout

```
fabram/
├── __main__.py            CLI entry point
├── cell_ports.py          CellPorts config dataclass
├── geometry.py            ArrayGeometry (rows, cols, addr bits)
├── generators/
│   ├── top.py             SRAMCompiler — assembles the full netlist
│   ├── arrays.py          SubcktDef builders for all array blocks
│   └── decode.py          Row/column decoder builders
└── characterize/
    ├── optimizer.py       CellSpec / Param / Objective dataclasses + BO/LHS search
    ├── render.py          YAML→subckt renderer; shared PDK helpers (gate_cap, driver_w)
    └── cells/
        ├── bit_cell.py    6T cell — SNM + write margin DC sweeps
        ├── sense_amp.py   Sense amp — t_sense transient
        ├── row_driver.py  Row driver — t_wl_rise transient
        ├── write_driver.py Write driver — t_drive transient
        └── dido.py        DIDO — precharge + write transient

cells/
├── primitives/            not.yaml  nand2.yaml  nand3.yaml  nand4.yaml
└── sram/                  bit_cell  dmy_cell  ms_reg  sense_amp
                           dido  row_driver  write_driver  self_timed_ctrl

vendor/
├── spice_gen/             SPICE netlist generation (git submodule)
├── liberty_gen/           Liberty characterization — timing, power, LUTs (git submodule)
└── verilog_gen/           Verilog model + specify block generation (git submodule)

tests/
└── yosys/
    ├── top.v              Minimal SRAM wrapper for Yosys import check
    └── check.ys           Yosys script: read_liberty → hierarchy -check → stat
```
