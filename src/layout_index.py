"""
Layout Index — fast, cached JSON sidecar for giant DXF files.

Problem: 590 MB DXF takes ~180 s to load with ezdxf.readfile() (~3.7 GB RAM).
Solution: build a compact index ONCE via streaming (ezdxf.addons.iterdxf),
persist it, and answer all read queries from the index in <1 s.

The index includes layer table, entity counts, every block INSERT with
position/rotation/layer/attributes, and extents.  Only edit/save/render
tools pay the cost of loading the full document.

Index cache is keyed by file mtime+size — auto-rebuilds on DXF change.
"""

from __future__ import annotations

import json, os, time
from typing import Optional, Any


def index_path_for(dxf_path: str) -> str:
    """Sidecar path: 'layout.dxf' → 'layout.dxf.index.json'."""
    return dxf_path + ".index.json"


INDEX_VERSION = 2


# ── Text-scan helpers (read DXF HEADER + TABLES without full parse) ──────────

def _scan_header(dxf_path: str, max_bytes: int = 3_000_000) -> dict:
    """Read $ACADVER and $INSUNITS from the HEADER section quickly."""
    acadver = insunits_code = 0
    try:
        with open(dxf_path, "r", encoding="utf-8", errors="ignore") as f:
            prev = want = None; read = 0
            while True:
                line = f.readline()
                if not line: break
                read += len(line)
                if read > max_bytes: break
                val = line.strip()
                if val == "$ACADVER": want = "acadver"
                elif val == "$INSUNITS": want = "insunits"
                elif prev == "1" and want == "acadver": acadver, want = val, None
                elif prev == "70" and want == "insunits":
                    try: insunits_code = int(val)
                    except ValueError: pass
                    want = None
                elif val == "ENDSEC": break
                prev = val
    except Exception: pass
    return {"dxfversion": acadver or "unknown", "insunits_code": insunits_code}


def _scan_layers(dxf_path: str, max_bytes: int = 6_000_000) -> list[dict]:
    """Parse LAYER table entries from the top of a text DXF.

    Returns [] for binary DXF or parse issues — caller falls back.
    """
    layers: list[dict] = []; cur: dict[str, Any] = {}
    in_layer_table = False; group_code: Optional[str] = None
    try:
        with open(dxf_path, "r", encoding="utf-8", errors="ignore") as f:
            read = 0
            while True:
                line = f.readline()
                if not line: break
                read += len(line)
                if read > max_bytes: break
                val = line.rstrip("\n").rstrip("\r").strip()
                if group_code == "2" and val == "LAYER": in_layer_table = True
                if group_code == "0" and val == "ENDTAB" and in_layer_table:
                    in_layer_table = False
                    if cur: layers.append(cur); cur = {}
                    break
                if group_code == "2" and val == "ENTITIES": break
                if in_layer_table:
                    if group_code == "0" and val == "LAYER":
                        if cur: layers.append(cur)
                        cur = {"name":"","color":7,"linetype":"Continuous","frozen":False,"locked":False,"on":True}
                    elif cur:
                        if group_code == "2": cur["name"] = val
                        elif group_code == "70":
                            try:
                                flags = int(val)
                                cur["frozen"] = bool(flags & 1)
                                cur["locked"] = bool(flags & 4)
                            except ValueError: pass
                        elif group_code == "62":
                            try:
                                c = int(val)
                                cur["on"] = c >= 0; cur["color"] = abs(c)
                            except ValueError: pass
                        elif group_code == "6": cur["linetype"] = val
                group_code = val if (val.isdigit() or (val.startswith("-") and val[1:].isdigit())) else None
        seen = set(); out = []
        for l in layers:
            n = l.get("name","")
            if n and n not in seen: seen.add(n); out.append(l)
        return out
    except Exception: return []


# ── Index builder ────────────────────────────────────────────────────────────

def build_index(dxf_path: str) -> dict:
    """Stream the DXF via iterdxf in a SINGLE pass to build a complete metadata index.

    v2: single-pass (blocks + TEXT/MTEXT + ATTRIB), per-layer counts, robust
    core extents (p1/p99), INDEX_VERSION marker.
    """
    from ezdxf import units as dxf_units
    from ezdxf.addons import iterdxf
    t0 = time.time()
    dxf_path = os.path.abspath(dxf_path); stat = os.stat(dxf_path)
    ec: dict[str, int] = {}; lc: dict[str, dict[str, int]] = {}
    block_counts: dict[str, int] = {}; blocks: list[dict] = []
    texts: list[dict] = []; attributes: list[dict] = []
    all_x: list[float] = []; all_y: list[float] = []
    xmin = ymin = float("inf"); xmax = ymax = float("-inf")
    header = _scan_header(dxf_path); layers = _scan_layers(dxf_path)
    try:
        doc = iterdxf.opendxf(dxf_path)
        for e in doc.modelspace():
            t = e.dxftype(); ec[t] = ec.get(t, 0) + 1
            ly = getattr(e.dxf, "layer", "0"); lc[ly] = lc.get(ly, {}); lc[ly][t] = lc[ly].get(t, 0) + 1
            try:
                ins = e.dxf.insert; x, y = float(ins[0]), float(ins[1])
                all_x.append(x); all_y.append(y)
                if x < xmin: xmin = x
                if y < ymin: ymin = y
                if x > xmax: xmax = x
                if y > ymax: ymax = y
            except (AttributeError, TypeError, IndexError): pass
            if t == "INSERT":
                name = getattr(e.dxf, "name", "?"); block_counts[name] = block_counts.get(name, 0) + 1
                try: bx, by = round(float(e.dxf.insert[0]), 4), round(float(e.dxf.insert[1]), 4)
                except: bx = by = 0.0
                blocks.append({"handle": e.dxf.handle, "name": name, "x": bx, "y": by,
                    "rotation": round(float(getattr(e.dxf, "rotation", 0) or 0), 4), "layer": ly})
            elif t in ("TEXT", "MTEXT"):
                txt = getattr(e.dxf, "text", "")
                try: tx, ty = round(float(e.dxf.insert[0]), 4), round(float(e.dxf.insert[1]), 4)
                except: tx = ty = 0.0
                texts.append({"handle": e.dxf.handle, "text": str(txt), "x": tx, "y": ty,
                    "layer": ly, "height": round(float(getattr(e.dxf, "height", 2.5) or 2.5), 4)})
            elif t == "ATTRIB":
                attributes.append({"owner": str(getattr(e.dxf, "owner", "")),
                    "tag": str(getattr(e.dxf, "tag", "?")), "value": str(getattr(e.dxf, "text", ""))})
        doc.close(); used = True
    except Exception:
        import ezdxf; doc_full = ezdxf.readfile(dxf_path); msp = doc_full.modelspace()
        for e in msp:
            t = e.dxftype(); ec[t] = ec.get(t, 0) + 1; ly = e.dxf.layer
            lc[ly] = lc.get(ly, {}); lc[ly][t] = lc[ly].get(t, 0) + 1
            try: ins = e.dxf.insert; x, y = float(ins[0]), float(ins[1])
            except: continue
            all_x.append(x); all_y.append(y)
            if x < xmin: xmin = x
            if y < ymin: ymin = y
            if x > xmax: xmax = x
            if y > ymax: ymax = y
            if t == "INSERT":
                name = e.dxf.name; block_counts[name] = block_counts.get(name, 0) + 1
                blocks.append({"handle": e.dxf.handle, "name": name, "x": round(x, 4), "y": round(y, 4),
                    "rotation": round(float(e.dxf.rotation or 0), 4), "layer": ly})
            elif t in ("TEXT", "MTEXT"):
                texts.append({"handle": e.dxf.handle, "text": str(e.dxf.text), "x": round(x, 4),
                    "y": round(y, 4), "layer": ly,
                    "height": round(float(getattr(e.dxf, "height", 2.5) or 2.5), 4)})
            elif t == "ATTRIB":
                attributes.append({"owner": str(e.dxf.owner), "tag": str(e.dxf.tag), "value": str(e.dxf.text)})
        used = False
    # INSUNITS
    try: un = dxf_units.unit_name(header["insunits_code"])
    except: un = str(header["insunits_code"])
    # extents
    if xmin == float("inf"): ext = {"min": None, "max": None, "width": 0, "height": 0}
    else: ext = {"min": [round(xmin, 4), round(ymin, 4)], "max": [round(xmax, 4), round(ymax, 4)],
        "width": round(xmax - xmin, 4), "height": round(ymax - ymin, 4)}
    # core extents p1/p99
    ce = {}
    if len(all_x) >= 100:
        sx = sorted(all_x); sy = sorted(all_y)
        p1 = lambda a: a[int(len(a)*0.01)]; p99 = lambda a: a[int(len(a)*0.99)]
        px1, px99 = p1(sx), p99(sx); py1, py99 = p1(sy), p99(sy)
        ce = {"min": [round(px1, 4), round(py1, 4)], "max": [round(px99, 4), round(py99, 4)],
            "width": round(px99 - px1, 4), "height": round(py99 - py1, 4)}
    if not layers:
        try:
            import ezdxf; dl = ezdxf.readfile(dxf_path)
            layers = [{"name": la.dxf.name, "color": la.dxf.color, "linetype": la.dxf.linetype,
                "frozen": bool(la.is_frozen()), "locked": bool(la.is_locked()), "on": la.is_on()} for la in dl.layers]
        except: pass
    idx = {
        "_version": 2, "source": dxf_path, "mtime": stat.st_mtime, "size": stat.st_size,
        "dxfversion": header["dxfversion"], "insunits": un, "insunits_code": header["insunits_code"],
        "layers": layers,
        "entity_type_counts": dict(sorted(ec.items(), key=lambda kv: -kv[1])),
        "block_counts": dict(sorted(block_counts.items(), key=lambda kv: -kv[1])),
        "extents": ext, "core_extents": ce,
        "blocks": blocks, "texts": texts, "text_count": len(texts),
        "attributes": attributes, "attribute_count": len(attributes),
        "attribute_tags": list(sorted(set(a["tag"] for a in attributes))),
        "layer_entity_counts": {ly: sum(v.values()) for ly, v in lc.items()},
        "build_seconds": round(time.time() - t0, 1), "streamed": used,
    }
    return idx
def load_or_build(dxf_path: str, force: bool = False) -> dict:
    """Load a cached index if fresh, else (re)build and persist it as gzipped JSON."""
    import gzip
    dxf_path = os.path.abspath(dxf_path)
    idx_path = index_path_for(dxf_path); gz_path = idx_path + ".gz"
    stat = os.stat(dxf_path)
    def _read(p):
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f: return json.load(f)
        with open(p, "r", encoding="utf-8") as f: return json.load(f)
    if not force:
        for p in (gz_path, idx_path):
            if os.path.exists(p):
                try:
                    idx = _read(p)
                    if (idx.get("_version") == 2 and abs(idx.get("mtime", 0) - stat.st_mtime) < 0.5
                            and idx.get("size") == stat.st_size):
                        idx["cached"] = True; return idx
                except: pass
    idx = build_index(dxf_path)
    try:
        with gzip.open(gz_path, "wt", encoding="utf-8") as f: json.dump(idx, f)
        if os.path.exists(idx_path): os.remove(idx_path)
    except:
        try:
            with open(idx_path, "w", encoding="utf-8") as f: json.dump(idx, f)
        except: pass
    idx["cached"] = False; return idx


# ── Fast query surface ───────────────────────────────────────────────────────

class LayoutIndex:
    """Instant read-only access to layout metadata.

    All query methods run in microseconds from the cached JSON.  Only
    methods that mutate the drawing need the full ezdxf document load.
    """

    def __init__(self, dxf_path: str, force: bool = False):
        self.path = os.path.abspath(dxf_path)
        self.data = load_or_build(self.path, force=force)

    def info(self) -> dict:
        d = self.data
        return {
            "path": d["source"],
            "dxfversion": d["dxfversion"],
            "insunits": d["insunits"],
            "layers": len(d["layers"]),
            "modelspace_entities": sum(d["entity_type_counts"].values()),
            "block_instances": sum(d["block_counts"].values()),
            "distinct_block_types": len(d["block_counts"]),
            "attribute_tags": d.get("attribute_tags", []),
            "attribute_count": d.get("attribute_count", 0),
            "text_labels": d.get("text_count", 0),
            "extents": d["extents"],
            "core_extents": d.get("core_extents", {}),
            "text_labels": d.get("text_count", 0),
            "index_cached": d.get("cached", False),
            "index_build_seconds": d.get("build_seconds"),
            "index_version": d.get("_version", 0),
        }

    def list_layers(self) -> list[dict]:
        return self.data["layers"]

    def list_entity_types(self) -> dict[str, int]:
        return self.data["entity_type_counts"]

    def block_type_counts(self) -> dict[str, int]:
        return self.data["block_counts"]

    def list_blocks(self, layer: Optional[str] = None,
                    block_name: Optional[str] = None,
                    limit: int = 500) -> list[dict]:
        out = []
        for b in self.data["blocks"]:
            if layer and b["layer"] != layer: continue
            if block_name and b["name"] != block_name: continue
            out.append(b)
            if len(out) >= limit: break
        return out

    def find_blocks(self, layer: Optional[str] = None,
                    name_contains: Optional[str] = None,
                    xmin: Optional[float] = None, ymin: Optional[float] = None,
                    xmax: Optional[float] = None, ymax: Optional[float] = None,
                    limit: int = 200) -> list[dict]:
        out = []
        nc = name_contains.lower() if name_contains else None
        for b in self.data["blocks"]:
            if layer and b["layer"] != layer: continue
            if nc and nc not in b["name"].lower(): continue
            if xmin is not None and b["x"] < xmin: continue
            if xmax is not None and b["x"] > xmax: continue
            if ymin is not None and b["y"] < ymin: continue
            if ymax is not None and b["y"] > ymax: continue
            out.append(b)
            if len(out) >= limit: break
        return out

    def find_attributes(self, tag: Optional[str] = None,
                        value_contains: Optional[str] = None,
                        owner: Optional[str] = None) -> list[dict]:
        out = []
        vc = value_contains.lower() if value_contains else None
        for a in self.data.get("attributes", []):
            if tag and a["tag"] != tag: continue
            if vc and vc not in a["value"].lower(): continue
            if owner and a["owner"] != owner: continue
            out.append(a)
        return out


    # ── v2 query methods ─────────────────────────────────────────────────

    def find_text(self, contains=None, layer=None, near_x=None, near_y=None,
                  radius=1000.0, limit=200):
        out = []; nc = contains.lower() if contains else None
        for t in self.data.get("texts", []):
            if nc and nc not in t["text"].lower(): continue
            if layer and t["layer"] != layer: continue
            if near_x is not None and near_y is not None:
                if math.hypot(t["x"] - near_x, t["y"] - near_y) > radius: continue
            out.append(t)
            if len(out) >= limit: break
        return out

    def blocks_near(self, x, y, radius=1000.0, name_contains=None, limit=100):
        out = []; nc = name_contains.lower() if name_contains else None
        for b in self.data["blocks"]:
            if math.hypot(b["x"] - x, b["y"] - y) > radius: continue
            if nc and nc not in b["name"].lower(): continue
            out.append(b)
            if len(out) >= limit: break
        out.sort(key=lambda b: math.hypot(b["x"] - x, b["y"] - y))
        return out

    def whats_at(self, x, y, radius=500.0):
        return {"blocks": self.blocks_near(x, y, radius),
                "texts": self.find_text(near_x=x, near_y=y, radius=radius)}

    def nearest_blocks(self, target, k=5):
        tb = None
        for b in self.data["blocks"]:
            if b["handle"] == target or b["name"].lower() == target.lower():
                tb = b; break
        if not tb: return []
        tx, ty = tb["x"], tb["y"]
        return sorted([b for b in self.data["blocks"] if b["handle"] != tb["handle"]],
                      key=lambda b: math.hypot(b["x"] - tx, b["y"] - ty))[:k]

    def layer_contents(self, layer):
        return {"layer": layer,
                "entity_counts": self.data.get("layer_entity_counts", {}).get(layer, {}),
                "blocks": self.list_blocks(layer=layer, limit=10000),
                "texts": self.find_text(layer=layer, limit=10000)}

    def block_stats(self, group_by="name"):
        if group_by == "layer":
            from collections import Counter
            return dict(Counter(b["layer"] for b in self.data["blocks"]).most_common())
        return self.block_type_counts()

    def export_inventory(self):
        return [{"handle": b["handle"], "name": b["name"], "x": b["x"], "y": b["y"],
                 "rotation": b.get("rotation", 0), "layer": b["layer"]}
                for b in self.data["blocks"]]


if __name__ == "__main__":
    import sys, pprint
    p = sys.argv[1] if len(sys.argv) > 1 else \
        r"c:\Users\umatp008\Downloads\Local VS\plant_layout_ai\PA2_Master_Layout.dxf"
    force = "--force" in sys.argv
    idx = LayoutIndex(p, force=force)
    pprint.pprint(idx.info())
    print("\nTop 15 entity types:")
    for t, c in list(idx.list_entity_types().items())[:15]:
        print(f"  {t}: {c:,}")
    print("\nTop 15 block types:")
    for n, c in list(idx.block_type_counts().items())[:15]:
        print(f"  {n}: {c:,}")
    if idx.data.get("attribute_tags"):
        print(f"\nAttribute tags found: {idx.data['attribute_tags']}")
        print(f"Total attribute values: {idx.data['attribute_count']:,}")
