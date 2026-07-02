# ZWCAD MCP Server

**35+ MCP tools for AI-assisted CAD layout queries, editing, rendering, and round-tripping via ZWCAD 2026 COM automation.**

Connect VS Code Copilot (or any MCP client) to your DXF plant/factory layouts.  
Instant indexed queries, batch edits, text search, spatial analysis, PNG renders, and DWG export — all from natural language.

---

## Quick Start

### Prerequisites
- **Python 3.12+** with `pip`
- **ZWCAD 2026** installed (`C:\Program Files\ZWSOFT\ZWCAD_2026_EN\ZWCAD.exe`)
- **VS Code** with GitHub Copilot Chat

### 1. Install dependencies

```bash
pip install ezdxf comtypes mcp matplotlib
```

If `pip install` fails with `ConnectionResetError` on PyPI (common in corporate networks), use the offline wheel workaround (see [WHEELS.md](WHEELS.md)).

### 2. Convert your DWG to DXF

```bash
python bridges/dwg_to_dxf.py "path/to/your/layout.dwg" "path/to/output.dxf"
```

This uses ZWCAD COM automation to open the DWG and save as DXF. ZWCAD must be installed and not already running.

> **Never modify your original DWG.** All edits happen on the DXF copy.

### 3. Build the index (one-time, ~3 minutes)

```bash
python -c "from src.layout_index import build_index; import json, gzip; idx = build_index('path/to/output.dxf'); gzip.open('path/to/output.dxf.index.json.gz', 'wt').write(json.dumps(idx))"
```

Or simpler:

```bash
python scripts/build_index.py path/to/output.dxf
```

### 4. Register with VS Code

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "plant-layout": {
      "type": "stdio",
      "command": "/path/to/venv/Scripts/python.exe",
      "args": ["/path/to/zwcad-mcp-server/src/mcp_server.py"],
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

Also add to `.vscode/settings.json`:

```json
{
  "github.copilot.chat.mcp.enabled": true,
  "chat.mcp.autoStart": true
}
```

Reload VS Code. Accept the trust prompt. 35 tools are now available in Copilot Chat.

### 5. Start exploring

In Copilot Chat:

> *"Open the layout and show me what's in it"*  
> *"List all layers"*  
> *"Show me the top 10 most-used block types"*  
> *"Find all text labels containing 'door'"*  
> *"What blocks are near coordinate (5000, 5000)?"*  
> *"Measure the distance between the RH and LH Rear Doors"*

---

## Architecture

```
zwcad-mcp-server/
├── src/
│   ├── mcp_server.py        # FastMCP server — 35+ @mcp.tool() endpoints
│   ├── layout_core.py       # LayoutSession — read/edit/render/save DXF via ezdxf
│   └── layout_index.py      # LayoutIndex — streaming v2 JSON/gzip index builder
├── bridges/
│   ├── dwg_to_dxf.py        # DWG → DXF via ZWCAD COM
│   └── dxf_to_dwg.py        # DXF → DWG via ZWCAD COM (round-trip export)
├── scripts/
│   └── build_index.py       # One-shot index builder
├── WHEELS.md                # Offline pip install workaround
├── mcp.json.example         # Example VS Code MCP config
└── README.md                # This file
```

---

## Tools Reference

### Session Management

| Tool | Description |
|---|---|
| `open_layout` | Load the working DXF (uses cached index, <1s) |
| `save_layout` | Persist edits to the working DXF |
| `backup_layout` | Create timestamped backup before edits |
| `undo_last_save` | Restore most recent backup |

### Read & Query (index-backed, <1s)

| Tool | Description |
|---|---|
| `layout_info` | Document metadata: layers, entity count, extents, units |
| `list_layers` | All 99 layers with on/off/frozen/locked/color/linetype |
| `list_blocks` | All INSERT block references (equipment, fixtures) |
| `list_entity_types` | Histogram: LINE, SPLINE, ARC, INSERT, TEXT, etc. |
| `find_entities` | Search by layer, type, bounding box, text substring |
| `find_text` | **v2** — Search all TEXT/MTEXT labels (2,204 indexed) |
| `get_entity` | One entity by DXF handle |
| `measure_distance` | Between two handles or (x1,y1)→(x2,y2) |
| `blocks_near` | **v2** — Blocks within radius of a point |
| `whats_at` | **v2** — Blocks + text around a point |
| `nearest_blocks` | **v2** — K-nearest neighbors to a block |
| `layer_contents` | **v2** — All blocks + text + counts on one layer |
| `block_stats` | **v2** — Aggregate counts by name or layer |
| `convert_units` | **v2** — Convert between in/ft/mm/cm/m |

### Edit (requires full DXF load ~180s for 590MB file)

| Tool | Description |
|---|---|
| `move_entity` / `move_entity_to` | Translate a block |
| `rotate_entity` / `scale_entity` | Transform a block |
| `copy_entity` / `delete_entity` | Duplicate or remove |
| `set_layer` | Move entity to different layer |
| `insert_block` | Add block reference at (x, y) |
| `add_line` / `add_circle` / `add_polyline` / `add_text` | Create new entities |
| `move_entities` | **v2** — Batch move (N edits per load) |
| `delete_entities` | **v2** — Batch delete |
| `set_layer_bulk` | **v2** — Batch relayer |

### Pipeline

| Tool | Description |
|---|---|
| `render_preview` | PNG render with optional layer/window filter (matplotlib) |
| `export_dwg` | DXF → DWG via ZWCAD COM (round-trip back to CAD) |

---

## Key Design Decisions

### Index-Backed Lazy Loading

The full DXF takes **~180 seconds** to load with ezdxf (590 MB, 657K entities).  
The v2 index is built once via **streaming** (`ezdxf.addons.iterdxf`) in ~178 seconds  
and cached as **gzipped JSON** (~63 KB for a 590 MB DXF).

All read queries hit the index in **<0.03 seconds**. The heavy document load  
only triggers on the **first mutation** (edit/save/render/export).

### Safety

- `backup_layout()` auto-creates timestamped DXF copies in `_backups/`
- `undo_last_save()` restores the most recent backup
- The original **DWG is NEVER modified** — all work is on a DXF copy
- Batch edits (`move_entities`, `delete_entities`, `set_layer_bulk`) are  
  atomic per operation

### Unit System

The drawing's `INSUNITS` header is detected automatically.  
`convert_units(value, from, to)` handles in/ft/mm/cm/m.  
All distances in tool responses are in the drawing's native units.

### ZWCAD COM Bridge

- `dwg_to_dxf.py`: Opens DWG in ZWCAD (invisible), saves as DXF
- `dxf_to_dwg.py`: Opens edited DXF, saves back to DWG
- ZWCAD 2026 `ZWCAD.Application` COM object, version 26.8 compatible
- Requires ZWCAD to be installed; 3+ minute processing for large files

---

## Platform Support

| Platform | Status |
|---|---|
| Windows + ZWCAD 2026 | ✅ Full (COM bridges, all tools) |
| Windows (no ZWCAD) | ✅ Read/query/edit/render (DWG bridges unavailable) |
| macOS / Linux | ✅ Read/query only (no COM, no ZWCAD, no DWG round-trip) |

---

## Known Limitations

- **590 MB DXF load time**: ~180 seconds (one-time per edit session)
- **No block attributes**: DWG→DXF conversion strips ATTRIB data; equipment tags/IDs not searchable
- **2D only**: DXF is a 2D format
- **ZWCAD modal dialogs**: May block `Documents.Open()` on DWG with recovery prompts
- **pip in corporate networks**: `ConnectionResetError(10054)` — use [WHEELS.md](WHEELS.md) workaround

---

## License

MIT — see [LICENSE](LICENSE)

## Author

Built for plant/factory layout automation with ZWCAD 2026.
