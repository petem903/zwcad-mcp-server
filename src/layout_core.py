"""
Layout Core — Read/Edit/Render a DXF plant layout via ezdxf.

Entity IDs: ezdxf handles (stable persistent hex strings). All edit operations
reference entities by handle. The session holds one open drawing; save() writes
in-place to the working DXF file.

Usage:
    session = LayoutSession("path/to/layout.dxf")
    handles = session.find_entities(layer="MACHINES")
    session.move(handles[0], dx=500, dy=0)
    session.save()
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Any, TYPE_CHECKING

import ezdxf
from ezdxf.document import Drawing
from ezdxf.layouts import Modelspace
from ezdxf.entities import DXFEntity
from ezdxf.math import Vec2, Vec3, BoundingBox2d
from ezdxf import units as dxf_units

if TYPE_CHECKING:
    from layout_index import LayoutIndex


# ── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class EntityInfo:
    """Lightweight summary of a DXF entity for AI consumption."""
    handle: str
    dxftype: str
    layer: str
    position: Optional[tuple[float, float]] = None        # insertion point or midpoint
    rotation: Optional[float] = None                       # degrees
    scale_x: Optional[float] = None
    scale_y: Optional[float] = None
    scale_z: Optional[float] = None
    block_name: Optional[str] = None                       # for INSERT
    text: Optional[str] = None                             # for TEXT/MTEXT
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_entity(cls, entity: DXFEntity, block_name: Optional[str] = None) -> "EntityInfo":
        """Extract common properties from any DXF entity."""
        info = cls(
            handle=entity.dxf.handle,
            dxftype=entity.dxftype(),
            layer=entity.dxf.layer,
            block_name=block_name,
        )
        try:
            pos = entity.dxf.insert
            info.position = (round(pos.x, 4), round(pos.y, 4))
        except AttributeError:
            pass
        try:
            info.rotation = round(math.degrees(float(entity.dxf.rotation or 0)), 4)
        except (AttributeError, ValueError):
            pass

        if entity.dxftype() == "INSERT":
            try:
                info.block_name = entity.dxf.name
            except AttributeError:
                pass
            try:
                info.scale_x = entity.dxf.xscale
                info.scale_y = entity.dxf.yscale
                info.scale_z = entity.dxf.zscale
            except AttributeError:
                pass
        elif entity.dxftype() in ("TEXT", "MTEXT"):
            try:
                info.text = entity.dxf.text
            except AttributeError:
                pass
        return info


# ── Layout Session ──────────────────────────────────────────────────────────

class LayoutSession:
    """Holds one open ezdxf Drawing; provides read/edit/render/save operations.

    All entity modifications happen via this object.  Entity references are
    stable DXF handles (hex strings).  The session writes to the working DXF
    in-place on save() — the original DWG is never touched.

    Two modes:
    1. Index-backed (.open() factory): read queries from <1s JSON index.
       The heavy ezdxf.readfile() only triggers on first mutation.
    2. Direct: __init__ loads the full document immediately.
    """

    def __init__(self, dxf_path: str, index=None):
        self._path: str = dxf_path
        self._index = index
        self._doc = None
        self._msp = None
        if index is None:
            self._ensure_loaded()

    @classmethod
    def open(cls, dxf_path: str):
        """Create a session with index-backed lazy loading (preferred for MCP)."""
        from layout_index import LayoutIndex
        idx = LayoutIndex(dxf_path)
        return cls(dxf_path, index=idx)

    def _ensure_loaded(self):
        """Lazily load the full ezdxf document. Called on first mutation."""
        if self._doc is not None:
            return
        t0 = time.time()
        self._doc = ezdxf.readfile(self._path)
        self._msp = self._doc.modelspace()
        dt = time.time() - t0
        if dt > 5:
            print(f"[LayoutSession] Full document loaded in {dt:.1f}s", flush=True)

    @property
    def has_document(self):
        return self._doc is not None

    # ── File I/O ────────────────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._path

    def save(self) -> None:
        """Persist in-place to the working DXF file."""
        self._ensure_loaded()
        self._doc.saveas(self._path)

    def backup(self) -> str:
        """Create a timestamped backup of the DXF. Returns the backup path."""
        d = os.path.join(os.path.dirname(self._path), "_backups")
        os.makedirs(d, exist_ok=True)
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = os.path.splitext(os.path.basename(self._path))[0]
        bp = os.path.join(d, f"{name}_{ts}.dxf")
        if self._doc is not None:
            self._doc.saveas(bp)
        else:
            shutil.copy2(self._path, bp)
        self._last_backup = bp
        return bp

    def undo_last_save(self) -> str:
        """Restore the most recent backup. Returns the restored path."""
        bp = getattr(self, "_last_backup", None)
        if not bp:
            d = os.path.join(os.path.dirname(self._path), "_backups")
            if os.path.isdir(d):
                backups = sorted(
                    [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".dxf")],
                    key=os.path.getmtime, reverse=True)
                if backups: bp = backups[0]
        if bp and os.path.exists(bp):
            shutil.copy2(bp, self._path)
            self._doc = None; self._msp = None  # force reload on next access
            return bp
        raise FileNotFoundError("No backup found")

    def reload(self) -> None:
        """Reload from disk (discards unsaved changes)."""
        self._doc = None
        self._msp = None
        self._ensure_loaded()

    # ── Document Info ───────────────────────────────────────────────────────

    def info(self) -> dict:
        """Return top-level document metadata. Uses index when available."""
        if self._index is not None:
            return self._index.info()
        self._ensure_loaded()
        insunits_val = self._doc.header.get("$INSUNITS", 0)
        try:
            units_name = dxf_units.unit_name(int(insunits_val))
        except (KeyError, ValueError, TypeError):
            units_name = f"unknown({insunits_val})"
        return {
            "path": self._path,
            "dxfversion": self._doc.dxfversion,
            "layers": len(self._doc.layers),
            "blocks": len(self._doc.blocks),
            "modelspace_entities": len(self._msp),
            "insunits": units_name,
            "extents": self.extents(),
        }

    def units(self) -> str:
        """Return the drawing's INSUNITS as a human-readable string."""
        return self.info()["insunits"]

    @staticmethod
    def convert_units(value: float, from_unit: str, to_unit: str) -> float:
        """Convert a value between units. Supported: in, ft, mm, m, cm."""
        _to_in = {"in": 1, "ft": 12, "mm": 25.4, "cm": 2.54, "m": 0.0254}
        return value / _to_in.get(from_unit, 1) * _to_in.get(to_unit, 1)

    def extents(self) -> dict:
        """Return modelspace bounding-box extent info."""
        bbox = BoundingBox2d()
        for e in self._msp:
            try:
                bbox.extend(e.dxf.insert)  # type: ignore[arg-type]
            except (AttributeError, TypeError):
                pass
        if not bbox.has_data:
            return {"min": None, "max": None, "width": 0, "height": 0}
        return {
            "min": (round(bbox.extmin.x, 4), round(bbox.extmin.y, 4)),
            "max": (round(bbox.extmax.x, 4), round(bbox.extmax.y, 4)),
            "width": round(bbox.size.x, 4),
            "height": round(bbox.size.y, 4),
        }

    # ── Query / Read ────────────────────────────────────────────────────────

    def list_layers(self) -> list[dict]:
        """Return all layers with status info."""
        if self._index is not None:
            return self._index.list_layers()
        self._ensure_loaded()
        return [
            {
                "name": layer.dxf.name,
                "color": layer.dxf.color,
                "linetype": layer.dxf.linetype,
                "frozen": bool(layer.is_frozen()),
                "locked": bool(layer.is_locked()),
                "on": layer.is_on(),
            }
            for layer in self._doc.layers
        ]

    def list_blocks(self, layer: Optional[str] = None, block_name: Optional[str] = None) -> list[dict]:
        """Return all INSERT entities (block references), filtered optionally."""
        if self._index is not None:
            return self._index.list_blocks(layer=layer, block_name=block_name)
        self._ensure_loaded()
        results = []
        for e in self._msp.query("INSERT"):
            if layer and e.dxf.layer != layer:
                continue
            if block_name and e.dxf.name != block_name:
                continue
            results.append(EntityInfo.from_entity(e))
        return results

    def list_entity_types(self) -> dict[str, int]:
        """Return histogram of entity types in modelspace."""
        if self._index is not None:
            return self._index.list_entity_types()
        self._ensure_loaded()
        counts: dict[str, int] = {}
        for e in self._msp:
            t = e.dxftype()
            counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def find_entities(
        self,
        layer: Optional[str] = None,
        dxftype: Optional[str] = None,
        xmin: Optional[float] = None,
        ymin: Optional[float] = None,
        xmax: Optional[float] = None,
        ymax: Optional[float] = None,
        text_contains: Optional[str] = None,
        limit: int = 200,
    ) -> list[EntityInfo]:
        """Find entities by filter. Bounding box uses insertion-point test."""
        results: list[EntityInfo] = []
        for e in self._msp:
            if layer and e.dxf.layer != layer:
                continue
            if dxftype and e.dxftype() != dxftype:
                continue
            if xmin is not None or xmax is not None:
                try:
                    pos = e.dxf.insert
                except AttributeError:
                    continue
                if xmin is not None and pos.x < xmin:
                    continue
                if xmax is not None and pos.x > xmax:
                    continue
                if ymin is not None and pos.y < ymin:
                    continue
                if ymax is not None and pos.y > ymax:
                    continue
            if text_contains and e.dxftype() in ("TEXT", "MTEXT"):
                try:
                    if text_contains.lower() not in e.dxf.text.lower():
                        continue
                except AttributeError:
                    continue
            results.append(EntityInfo.from_entity(e))
            if len(results) >= limit:
                break
        return results

    def get_entity(self, handle: str) -> Optional[EntityInfo]:
        """Return info for a single entity by handle."""
        try:
            e = self._doc.entitydb[handle]
        except KeyError:
            return None
        return EntityInfo.from_entity(e)

    def measure_distance(self, a: str, b: str) -> Optional[dict]:
        """Measure distance between two entities (by handle) or return None."""
        try:
            pos_a = self._doc.entitydb[a].dxf.insert
            pos_b = self._doc.entitydb[b].dxf.insert
        except (KeyError, AttributeError):
            return None
        dx = pos_b.x - pos_a.x
        dy = pos_b.y - pos_a.y
        dist = math.hypot(dx, dy)
        return {"dx": round(dx, 4), "dy": round(dy, 4), "distance": round(dist, 4)}

    def measure_point_to_point(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> dict:
        """Measure distance between two arbitrary points."""
        dx = x2 - x1
        dy = y2 - y1
        return {"dx": round(dx, 4), "dy": round(dy, 4), "distance": round(math.hypot(dx, dy), 4)}

    # ── Edit by Handle ──────────────────────────────────────────────────────

    def _get(self, handle: str) -> DXFEntity:
        """Get entity by handle; raises KeyError if not found."""
        return self._doc.entitydb[handle]

    def move(self, handle: str, dx: float, dy: float) -> EntityInfo:
        """Translate an entity by (dx, dy)."""
        e = self._get(handle)
        e.dxf.insert = Vec3(e.dxf.insert.x + dx, e.dxf.insert.y + dy, getattr(e.dxf.insert, "z", 0))
        return EntityInfo.from_entity(e)

    def move_to(self, handle: str, x: float, y: float) -> EntityInfo:
        """Set an entity's absolute position."""
        e = self._get(handle)
        e.dxf.insert = Vec3(x, y, getattr(e.dxf.insert, "z", 0))
        return EntityInfo.from_entity(e)

    def rotate(self, handle: str, angle_deg: float) -> EntityInfo:
        """Add rotation to an entity (degrees). Absolute set for INSERT blocks."""
        e = self._get(handle)
        e.dxf.rotation = angle_deg
        return EntityInfo.from_entity(e)

    def scale(self, handle: str, sx: float, sy: Optional[float] = None, sz: Optional[float] = None) -> EntityInfo:
        """Set scale factors on an INSERT entity."""
        e = self._get(handle)
        e.dxf.xscale = sx
        e.dxf.yscale = sy if sy is not None else sx
        e.dxf.zscale = sz if sz is not None else 1.0
        return EntityInfo.from_entity(e)

    def delete(self, handle: str) -> bool:
        """Delete an entity from modelspace. Returns True if deleted."""
        try:
            e = self._get(handle)
        except KeyError:
            return False
        self._msp.delete_entity(e)
        return True

    def set_layer(self, handle: str, layer_name: str) -> EntityInfo:
        """Move an entity to a different layer."""
        e = self._get(handle)
        e.dxf.layer = layer_name
        return EntityInfo.from_entity(e)

    def copy(self, handle: str, dx: float = 0, dy: float = 0) -> EntityInfo:
        """Copy an entity with an optional offset. Returns info for the NEW entity."""
        e = self._get(handle)
        new = e.copy()
        if dx or dy:
            new.dxf.insert = Vec3(
                e.dxf.insert.x + dx,
                e.dxf.insert.y + dy,
                getattr(e.dxf.insert, "z", 0),
            )
        self._msp.add_entity(new)
        return EntityInfo.from_entity(new)

    # ── Batch operations (one load, many edits) ─────────────────────────────

    def move_entities(self, handles: list[str], dx: float, dy: float) -> list[dict]:
        """Move multiple entities by (dx, dy). Returns new info for each."""
        results = []
        for h in handles:
            try:
                e = self._get(h)
                e.dxf.insert = Vec3(e.dxf.insert.x + dx, e.dxf.insert.y + dy,
                                    getattr(e.dxf.insert, "z", 0))
                results.append({"handle": h, "ok": True})
            except KeyError:
                results.append({"handle": h, "ok": False, "error": "not found"})
        return results

    def delete_entities(self, handles: list[str]) -> list[dict]:
        """Delete multiple entities from modelspace."""
        results = []
        for h in handles:
            try:
                e = self._get(h)
                self._msp.delete_entity(e)
                results.append({"handle": h, "deleted": True})
            except KeyError:
                results.append({"handle": h, "deleted": False, "error": "not found"})
        return results

    def set_layer_bulk(self, handles: list[str], layer_name: str) -> list[dict]:
        """Move multiple entities to a new layer."""
        results = []
        for h in handles:
            try:
                e = self._get(h)
                e.dxf.layer = layer_name
                results.append({"handle": h, "ok": True})
            except KeyError:
                results.append({"handle": h, "ok": False, "error": "not found"})
        return results

    # ── Create New Entities ─────────────────────────────────────────────────

    def insert_block(self, block_name: str, x: float, y: float,
                     rotation: float = 0, layer: str = "0",
                     sx: float = 1.0, sy: float = 1.0) -> EntityInfo:
        """Insert a block reference at (x, y)."""
        e = self._msp.add_blockref(block_name, insert=(x, y), dxfattribs={
            "layer": layer,
            "rotation": rotation,
            "xscale": sx,
            "yscale": sy,
        })
        return EntityInfo.from_entity(e)

    def add_line(self, x1: float, y1: float, x2: float, y2: float,
                 layer: str = "0") -> EntityInfo:
        """Add a line entity."""
        e = self._msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})
        pos = Vec2((x1 + x2) / 2, (y1 + y2) / 2)
        ei = EntityInfo.from_entity(e)
        ei.position = (round(pos.x, 4), round(pos.y, 4))
        return ei

    def add_circle(self, x: float, y: float, radius: float,
                   layer: str = "0") -> EntityInfo:
        """Add a circle."""
        e = self._msp.add_circle((x, y), radius, dxfattribs={"layer": layer})
        return EntityInfo.from_entity(e)

    def add_lwpolyline(self, points: list[tuple[float, float]],
                       layer: str = "0", closed: bool = False) -> EntityInfo:
        """Add a lightweight polyline from a list of (x, y) points."""
        e = self._msp.add_lwpolyline(points, dxfattribs={"layer": layer})
        e.closed = closed
        return EntityInfo.from_entity(e)

    def add_text(self, text: str, x: float, y: float, height: float = 2.5,
                 layer: str = "0", rotation: float = 0) -> EntityInfo:
        """Add a single-line TEXT entity."""
        e = self._msp.add_text(text, dxfattribs={
            "layer": layer,
            "height": height,
            "rotation": rotation,
            "insert": (x, y),
        })
        return EntityInfo.from_entity(e)

    # ── Render ──────────────────────────────────────────────────────────────

    def render(self, output_png: str,
               layers: Optional[list[str]] = None,
               xmin: Optional[float] = None, ymin: Optional[float] = None,
               xmax: Optional[float] = None, ymax: Optional[float] = None,
               dpi: int = 150, background: str = "#FFFFFF") -> str:
        """Render the layout (or a window) to PNG via ezdxf + matplotlib.

        Returns the absolute path to the saved PNG.
        """
        from ezdxf.addons.drawing import matplotlib as dxf_draw
        from ezdxf.addons.drawing import Properties, Frontend, RenderContext

        doc = self._doc

        # If layers specified, turn off everything else for the render
        restore_states: dict[str, bool] = {}
        if layers:
            for layer in doc.layers:
                restore_states[layer.dxf.name] = layer.is_on()
                layer.off()
            for name in layers:
                try:
                    layer_obj = doc.layers.get(name)
                except ezdxf.DXFTableEntryError:
                    continue
                layer_obj.on()

        try:
            if xmin is not None and xmax is not None:
                # Render a window region: use render_limited
                # ezdxf drawing addon provides a simple render path; for window
                # we just render and the caller can crop if needed, OR we pass
                # the viewport to the frontend.  For now: render full drawing
                # with layer filter.
                pass

            dxf_draw.qsave(
                doc.modelspace(),
                output_png,
                bg=background,
                dpi=dpi,
            )
        finally:
            # Restore layer states
            for name, was_on in restore_states.items():
                try:
                    layer_obj = doc.layers.get(name)
                except ezdxf.DXFTableEntryError:
                    continue
                if was_on:
                    layer_obj.on()
                else:
                    layer_obj.off()

        return os.path.abspath(output_png)


# ── Quick test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else r"c:\Users\umatp008\Downloads\Local VS\plant_layout_ai\PA2_Master_Layout.dxf"
    s = LayoutSession(path)
    info = s.info()
    print("=== DOCUMENT INFO ===")
    for k, v in info.items():
        print(f"  {k}: {v}")
    print("\n=== LAYERS ===")
    for l in s.list_layers()[:10]:
        print(f"  {l['name']}  color={l['color']}  frozen={l['frozen']}  locked={l['locked']}")
    if len(s.list_layers()) > 10:
        print(f"  ... ({len(s.list_layers())} total)")
    print("\n=== ENTITY TYPES ===")
    for t, c in s.list_entity_types().items():
        print(f"  {t}: {c}")
    print("\n=== FIRST 10 BLOCK INSERTIONS ===")
    for b in s.list_blocks()[:10]:
        print(f"  handle={b.handle}  block={b.block_name}  pos={b.position}  rot={b.rotation}deg  layer={b.layer}")
