# fabram

A parametric SRAM compiler for the sky130A PDK. Given three integers — **words**, **bits**, and **column mux factor** — it assembles a fully hierarchical SPICE netlist from hand-crafted cell templates.

---

## Requirements

- Python ≥ 3.10
- [`spice_gen`](https://github.com/ShaheerSajid/spice_gen) (vendored as a git submodule)
- sky130A PDK installed at `/usr/local/share/pdk/sky130A/`

## Installation

```bash
git clone --recurse-submodules <repo-url>
pip install -e vendor/spice_gen   # install spice_gen first
pip install -e .                  # install fabram
```

---

## CLI usage

```
fabram -w <words> -b <bits> [-m <mux>] [-d <dialect>] [-c <corner>] [-o <file>] [--stdout]
```

| Flag | Default | Description |
|---|---|---|
| `-w / --words` | required | Depth (power of 2) |
| `-b / --bits` | required | Word width |
| `-m / --mux` | `1` | Column mux factor (power of 2, ≤ words) |
| `-d / --dialect` | `ngspice` | SPICE dialect: `ngspice`, `hspice`, `spice3` |
| `-c / --corner` | `tt` | PDK corner (e.g. `tt`, `ff`, `ss`) |
| `-o / --output` | `<name>.sp` | Output file path |
| `--stdout` | — | Print to stdout instead of a file |

### Examples

```bash
# 64-word × 8-bit, column mux = 4 → writes SRAM_64x8_CM4.sp
fabram -w 64 -b 8 -m 4

# 256 × 32, slow-slow corner, hspice dialect
fabram -w 256 -b 32 -m 8 -d hspice -c ss -o sram_256x32.sp

# Print to stdout
fabram -w 16 -b 4 --stdout
```

---

## Python API

```python
from fabram import SRAMCompiler

netlist = SRAMCompiler(words=64, bits=8, col_mux=4).compile(pdk_corner="tt")
```

`compile()` returns a `spice_gen` `Netlist` object. Pass it to any dialect generator:

```python
from spice_gen.generator import get_generator

spice_text = get_generator("ngspice").generate(netlist)
```

---

## Array geometry

| Parameter | Formula |
|---|---|
| `num_rows` | `words // col_mux` |
| `num_cols` | `bits × col_mux` |
| `row_addr_bits` | `log₂(num_rows)` |
| `col_addr_bits` | `log₂(col_mux)` |

**Example — 64 × 8, col_mux = 4:**
- 16 rows × 32 physical columns
- 4-bit row address, 2-bit column address

---

## Hierarchical netlist structure

```
SRAM_{words}x{bits}_CM{col_mux}
├── XINREG  : INPUT_REG_{addr_bits+1}   — registers addr + write_en on CLK
├── XCTRL   : SELF_TIMED_CTRL           — generates WREN, PCHG, WLEN, SAEN
├── XRDEC   : ROW_DEC_{num_rows}        — row address decoder
├── XRDRVR  : ROW_DRV_ARR_{num_rows}   — word-line drivers
├── XCDEC   : ROW_DEC_{col_mux}        — column address decoder  (col_mux > 1)
├── XCDRVR  : COL_DRV_ARR_{col_mux}   — column select drivers    (col_mux > 1)
├── XMAT    : MAT_{num_rows}x{num_cols} — 6T bitcell matrix
├── XDMY    : DMY_ARR_{num_rows}        — dummy bitcell row
├── XDIDO   : DIDO_ARR_{num_cols}       — precharge + col-select + R/W pass
├── XDATAIN : DATAIN_ARR_{bits}         — input registers + write drivers
└── XSE     : SE_ARR_{bits}            — sense amplifiers → Q[bits-1:0]
```

---

## Custom cell port names

If your cell library uses different port names, pass a `CellPorts` config:

```python
from fabram import SRAMCompiler, CellPorts

cfg = CellPorts(
    sa_inp="INP",   # SENSE_AMP bitline+ (default "BL")
    sa_inn="INN",   # SENSE_AMP bitline- (default "BL_")
    sa_out="Q_OUT", # SENSE_AMP output   (default "SB")
    reg_clk="CLK",  # MS_REG clock       (default "clk")
)
netlist = SRAMCompiler(64, 8, col_mux=4, cell_ports=cfg).compile()
```

All `CellPorts` fields and their defaults:

| Field | Default | Cell |
|---|---|---|
| `cell_wl` | `WL` | BIT_CELL / DMY_CELL |
| `cell_bl` | `BL` | BIT_CELL / DMY_CELL |
| `cell_bl_` | `BL_` | BIT_CELL / DMY_CELL |
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
├── cell_ports.py          # CellPorts config dataclass
├── geometry.py            # ArrayGeometry (rows, cols, addr bits)
├── __main__.py            # CLI entry point
└── generators/
    ├── arrays.py          # SubcktDef builders for all array blocks
    ├── decode.py          # Row/column decoder builders
    └── top.py             # SRAMCompiler — assembles the full netlist
cells/
├── primitives/            # not.yaml  nand2.yaml  nand3.yaml  nand4.yaml
└── sram/                  # bit_cell  dmy_cell  sense_amp  ms_reg
                           # dido  row_driver  write_driver  self_timed_ctrl
vendor/
└── spice_gen/             # git submodule
```
