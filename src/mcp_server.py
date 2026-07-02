"""
MCP Server — Plant Layout CAD Tools

Exposes tools for VS Code Copilot to query, edit, render, and round-trip
a DXF plant layout.  Powered by layout_core.py (ezdxf) + ZWCAD COM for DWG.

Tools (read):
  open_layout       — Load the working DXF into a session
  layout_info       — Document metadata (layers, entity count, extents, units)
  list_layers       — All layer names + properties
  list_blocks       — All INSERT block references
  list_entity_types — Entity type histogram
  find_entities     — Search by layer, type, bbox, text
  get_entity        — Get one entity by handle
  measure_distance  — Distance between two entities or points

Tools (edit — all by handle):
  move_entity, move_entity_to, rotate_entity, scale_entity,
  copy_entity, delete_entity, set_layer,
  insert_block, add_line, add_circle, add_polyline, add_text

Tools (pipeline):
  save_layout       — Persist edits to the working DXF
  render_preview    — Render a PNG so AI can "see" the layout
  export_dwg        — Round-trip back to DWG (ZWCAD COM)

Usage:
  Install: pip install mcp (or the offline wheel workaround)
  Register in .vscode/mcp.json:
    {
      "servers": {
        "plant-layout": {
          "type": "stdio",
          "command": "c:/Users/umatp008/Downloads/Local VS/.venv/Scripts/python.exe",
          "args": ["c:/Users/umatp008/Downloads/Local VS/plant_layout_ai/mcp_server.py"]
        }
      }
    }
"""

from __future__ import annotations

import os
import sys
from typing import Optional

# Ensure plant_layout_ai is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from layout_core import LayoutSession, EntityInfo

# ── Paths ───────────────────────────────────────────────────────────────────

PROJ = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DXF = os.path.join(PROJ, "PA2_Master_Layout.dxf")

# ── Globals ─────────────────────────────────────────────────────────────────

mcp = FastMCP("Plant Layout CAD")
_session: Optional[LayoutSession] = None


def _ensure_session() -> LayoutSession:
    if _session is None:
        raise RuntimeError("No layout open. Call open_layout first.")
    return _session


# ── Info helpers ────────────────────────────────────────────────────────────

def _ei_to_dict(ei) -> dict:
    """Convert EntityInfo or index dict to JSON-safe dict."""
    if isinstance(ei, dict):
        d = dict(ei)
        if "x" in d:
            d["position"] = [d.pop("x"), d.pop("y")]
        if "rotation" in d:
            d["rotation_deg"] = d.pop("rotation")
        if "name" in d:
            d["block_name"] = d.pop("name")
        return d
    d = {
        "handle": ei.handle,
        "dxftype": ei.dxftype,
        "layer": ei.layer,
    }
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  TOOLS
# ══════════════════════════════════════════════════════════════════════════════

# ── Open / Save ─────────────────────────────────────────────────────────────

@mcp.tool()
def open_layout(path: Optional[str] = None) -> dict:
    """Open the working DXF file. Call this first before any other tool.
    If no path given, opens the default plant layout DXF."""
    global _session
    filepath = path or DEFAULT_DXF
    if not os.path.isfile(filepath):
        return {"error": f"File not found: {filepath}"}
    _session = LayoutSession.open(filepath)
    return _session.info()


@mcp.tool()
def save_layout() -> dict:
    """Save all pending edits to the working DXF file in-place."""
    s = _ensure_session()
    s.save()
    return {"saved": True, "path": s.path}


@mcp.tool()
def layout_info() -> dict:
    """Return document metadata: layers, entities, extents, units."""
    return _ensure_session().info()


# ── Query ───────────────────────────────────────────────────────────────────

@mcp.tool()
def list_layers() -> list[dict]:
    """List all layers with color, linetype, frozen/locked/on status."""
    return _ensure_session().list_layers()


@mcp.tool()
def list_blocks(layer: Optional[str] = None, block_name: Optional[str] = None) -> list[dict]:
    """List all INSERT block references in modelspace. Optionally filter by layer or block name."""
    return [_ei_to_dict(ei) for ei in _ensure_session().list_blocks(layer, block_name)]


@mcp.tool()
def list_entity_types() -> dict[str, int]:
    """Return a histogram of entity types in modelspace (e.g. {'INSERT': 450, 'LINE': 1200})."""
    return _ensure_session().list_entity_types()


@mcp.tool()
def find_entities(
    layer: Optional[str] = None,
    dxftype: Optional[str] = None,
    xmin: Optional[float] = None,
    ymin: Optional[float] = None,
    xmax: Optional[float] = None,
    ymax: Optional[float] = None,
    text_contains: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Find entities matching filters. Returns up to `limit` results.
    Bounding box filter uses insertion-point containment test.
    `text_contains` does case-insensitive substring match on TEXT/MTEXT entities."""
    return [_ei_to_dict(ei) for ei in _ensure_session().find_entities(
        layer=layer, dxftype=dxftype, xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax,
        text_contains=text_contains, limit=limit,
    )]


@mcp.tool()
def get_entity(handle: str) -> Optional[dict]:
    """Get full details for a single entity by its DXF handle."""
    ei = _ensure_session().get_entity(handle)
    return _ei_to_dict(ei) if ei else None


@mcp.tool()
def measure_distance(handle_a: Optional[str] = None, handle_b: Optional[str] = None,
                     x1: Optional[float] = None, y1: Optional[float] = None,
                     x2: Optional[float] = None, y2: Optional[float] = None) -> dict:
    """Measure distance between two entities (by handle) OR two arbitrary points.
    Provide EITHER (handle_a + handle_b) OR (x1, y1, x2, y2)."""
    s = _ensure_session()
    if handle_a and handle_b:
        result = s.measure_distance(handle_a, handle_b)
        if result is None:
            return {"error": "One or both handles not found or not point entities."}
        return result
    if all(v is not None for v in (x1, y1, x2, y2)):
        return s.measure_point_to_point(x1, y1, x2, y2)
    return {"error": "Provide (handle_a, handle_b) OR (x1,y1,x2,y2)."}


# ── Edit ────────────────────────────────────────────────────────────────────

@mcp.tool()
def move_entity(handle: str, dx: float, dy: float) -> dict:
    """Translate an entity by (dx, dy) in drawing units."""
    return _ei_to_dict(_ensure_session().move(handle, dx, dy))


@mcp.tool()
def move_entity_to(handle: str, x: float, y: float) -> dict:
    """Set an entity's absolute position."""
    return _ei_to_dict(_ensure_session().move_to(handle, x, y))


@mcp.tool()
def rotate_entity(handle: str, angle_deg: float) -> dict:
    """Set the rotation of an entity (degrees)."""
    return _ei_to_dict(_ensure_session().rotate(handle, angle_deg))


@mcp.tool()
def scale_entity(handle: str, sx: float, sy: Optional[float] = None, sz: Optional[float] = None) -> dict:
    """Set scale factors on an INSERT entity."""
    return _ei_to_dict(_ensure_session().scale(handle, sx, sy, sz))


@mcp.tool()
def copy_entity(handle: str, dx: float = 0, dy: float = 0) -> dict:
    """Copy an entity and optionally offset the copy. Returns the NEW entity info."""
    return _ei_to_dict(_ensure_session().copy(handle, dx, dy))


@mcp.tool()
def delete_entity(handle: str) -> dict:
    """Delete an entity from modelspace."""
    ok = _ensure_session().delete(handle)
    return {"deleted": ok, "handle": handle}


@mcp.tool()
def set_layer(handle: str, layer_name: str) -> dict:
    """Move an entity to a different layer."""
    return _ei_to_dict(_ensure_session().set_layer(handle, layer_name))


@mcp.tool()
def insert_block(block_name: str, x: float, y: float,
                 rotation: float = 0, layer: str = "0",
                 sx: float = 1.0, sy: float = 1.0) -> dict:
    """Insert a block reference at (x, y)."""
    return _ei_to_dict(_ensure_session().insert_block(block_name, x, y, rotation, layer, sx, sy))


@mcp.tool()
def add_line(x1: float, y1: float, x2: float, y2: float, layer: str = "0") -> dict:
    """Add a line entity."""
    return _ei_to_dict(_ensure_session().add_line(x1, y1, x2, y2, layer))


@mcp.tool()
def add_circle(x: float, y: float, radius: float, layer: str = "0") -> dict:
    """Add a circle."""
    return _ei_to_dict(_ensure_session().add_circle(x, y, radius, layer))


@mcp.tool()
def add_polyline(points: list[dict], layer: str = "0", closed: bool = False) -> dict:
    """Add a lightweight polyline. points = [{"x":0,"y":0}, {"x":10,"y":0}, ...]"""
    pts = [(p["x"], p["y"]) for p in points]
    return _ei_to_dict(_ensure_session().add_lwpolyline(pts, layer, closed))


@mcp.tool()
def add_text(text: str, x: float, y: float, height: float = 2.5,
             layer: str = "0", rotation: float = 0) -> dict:
    """Add a single-line TEXT entity."""
    return _ei_to_dict(_ensure_session().add_text(text, x, y, height, layer, rotation))


# ── v2 Read Tools (index-backed, instant) ─────────────────────────────────

@mcp.tool()
def find_text(contains: Optional[str] = None, layer: Optional[str] = None,
              near_x: Optional[float] = None, near_y: Optional[float] = None,
              radius: float = 1000.0, limit: int = 200) -> list[dict]:
    """Search TEXT/MTEXT labels. Filter by substring, layer, and proximity."""
    s = _ensure_session()
    if s._index is not None:
        return s._index.find_text(contains=contains, layer=layer,
                                  near_x=near_x, near_y=near_y, radius=radius, limit=limit)
    s._ensure_loaded()
    results = []
    nc = contains.lower() if contains else None
    for e in list(s._msp):
        if e.dxftype() not in ("TEXT", "MTEXT"): continue
        if nc and nc not in e.dxf.text.lower(): continue
        if layer and e.dxf.layer != layer: continue
        if near_x is not None:
            import math
            if math.hypot(e.dxf.insert.x - near_x, e.dxf.insert.y - near_y) > radius: continue
        results.append({"handle": e.dxf.handle, "text": e.dxf.text,
                        "x": e.dxf.insert.x, "y": e.dxf.insert.y, "layer": e.dxf.layer})
        if len(results) >= limit: break
    return results


@mcp.tool()
def blocks_near(x: float, y: float, radius: float = 1000.0,
                name_contains: Optional[str] = None, limit: int = 100) -> list[dict]:
    """Find blocks within a radius of a point."""
    s = _ensure_session()
    if s._index is not None:
        return s._index.blocks_near(x, y, radius, name_contains=name_contains, limit=limit)
    s._ensure_loaded()
    nc = name_contains.lower() if name_contains else None
    out = []
    import math
    for e in s._msp.query("INSERT"):
        if math.hypot(e.dxf.insert.x - x, e.dxf.insert.y - y) > radius: continue
        if nc and nc not in e.dxf.name.lower(): continue
        out.append(_ei_to_dict(EntityInfo.from_entity(e)))
        if len(out) >= limit: break
    return out


@mcp.tool()
def whats_at(x: float, y: float, radius: float = 500.0) -> dict:
    """Return blocks + text labels around a point."""
    s = _ensure_session()
    if s._index is not None:
        return s._index.whats_at(x, y, radius)
    return {"blocks": blocks_near(x, y, radius), "texts": find_text(near_x=x, near_y=y, radius=radius)}


@mcp.tool()
def nearest_blocks(target: str, k: int = 5) -> list[dict]:
    """Find k nearest blocks to a named block or handle."""
    s = _ensure_session()
    if s._index is not None:
        return s._index.nearest_blocks(target, k)
    return {"error": "nearest_blocks requires index — call open_layout first"}


@mcp.tool()
def layer_contents(layer: str) -> dict:
    """Blocks + text + entity counts on a single layer."""
    s = _ensure_session()
    if s._index is not None:
        return s._index.layer_contents(layer)
    return {"error": "layer_contents requires index — call open_layout first"}


@mcp.tool()
def block_stats(group_by: str = "name") -> dict:
    """Aggregate block counts grouped by name or layer."""
    s = _ensure_session()
    if s._index is not None:
        return s._index.block_stats(group_by)
    return {"error": "block_stats requires index — call open_layout first"}


@mcp.tool()
def convert_units(value: float, from_unit: str, to_unit: str) -> dict:
    """Convert a value between units (in, ft, mm, m, cm)."""
    result = LayoutSession.convert_units(value, from_unit, to_unit)
    return {"value": value, "from": from_unit, "to": to_unit, "result": round(result, 4)}


# ── Batch Edit Tools ───────────────────────────────────────────────────────

@mcp.tool()
def move_entities(handles: list[str], dx: float, dy: float) -> list[dict]:
    """Move multiple entities by (dx, dy) in one operation."""
    return _ensure_session().move_entities(handles, dx, dy)


@mcp.tool()
def delete_entities(handles: list[str]) -> list[dict]:
    """Delete multiple entities from modelspace in one operation."""
    return _ensure_session().delete_entities(handles)


@mcp.tool()
def set_layer_bulk(handles: list[str], layer_name: str) -> list[dict]:
    """Move multiple entities to a new layer in one operation."""
    return _ensure_session().set_layer_bulk(handles, layer_name)


# ── Safety ──────────────────────────────────────────────────────────────────

@mcp.tool()
def backup_layout() -> dict:
    """Create a timestamped backup of the current DXF before making edits."""
    bp = _ensure_session().backup()
    return {"backup_path": bp, "message": f"Backup saved: {os.path.basename(bp)}"}


@mcp.tool()
def undo_last_save() -> dict:
    """Restore the most recent backup, discarding changes since."""
    try:
        bp = _ensure_session().undo_last_save()
        return {"restored_from": bp, "message": "Layout restored from backup"}
    except FileNotFoundError:
        return {"error": "No backup found"}


# ── Render ─────────────────────────────────────────────────────────────────

@mcp.tool()
def render_preview(output_png: Optional[str] = None,
                   layers: Optional[list[str]] = None,
                   xmin: Optional[float] = None, ymin: Optional[float] = None,
                   xmax: Optional[float] = None, ymax: Optional[float] = None,
                   dpi: int = 150,
                   background: str = "#FFFFFF") -> dict:
    """Render the layout (or a window of it) to a PNG file so you can visually inspect.
    Returns the path to the saved PNG.  Use `layers` to show only specific layers."""
    if output_png is None:
        output_png = os.path.join(PROJ, "_preview.png")
    path = _ensure_session().render(
        output_png, layers=layers,
        xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax,
        dpi=dpi, background=background,
    )
    return {"png_path": path, "file_size": os.path.getsize(path)}


# ── Round-trip ──────────────────────────────────────────────────────────────

@mcp.tool()
def export_dwg(output_dwg: Optional[str] = None) -> dict:
    """Export the current DXF back to DWG format so it can be opened in ZWCAD.
    Requires ZWCAD to be installed."""
    if output_dwg is None:
        output_dwg = os.path.join(PROJ, "PA2_Master_Layout_exported.dwg")

    s = _ensure_session()
    # Save current state first
    s.save()

    import subprocess
    converter = os.path.join(PROJ, "dxf_to_dwg.py")
    result = subprocess.run(
        [sys.executable, converter, s.path, output_dwg],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "DWG export failed"}
    return {"dwg_path": output_dwg, "file_size": os.path.getsize(output_dwg)}


# ── Launch ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
