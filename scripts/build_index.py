"""Build the JSON index for the plant layout DXF and print stats."""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layout_index import build_index, index_path_for

DXF = os.path.join(os.path.dirname(__file__), "PA2_Master_Layout.dxf")

print("Building index (streaming, low memory)...", flush=True)
t0 = time.time()
idx = build_index(DXF)
dt = time.time() - t0

# Save
out = index_path_for(DXF)
with open(out, "w", encoding="utf-8") as f:
    json.dump(idx, f)
sz = os.path.getsize(out)

print(f"\nIndex saved: {out}")
print(f"  Index size: {sz:,} bytes")
print(f"  Build time: {dt:.1f}s")
print(f"  Streamed:   {idx['streamed']}")
print(f"  DXF version: {idx['dxfversion']}")
print(f"  INSUNITS:    {idx['insunits']}")
print(f"  Layers:      {len(idx['layers'])}")
print(f"  Entities:    {sum(idx['entity_type_counts'].values()):,}")
print(f"  Block instances: {sum(idx['block_counts'].values()):,}")
print(f"  Distinct block types: {len(idx['block_counts'])}")
print(f"  Attributes:  {idx['attribute_count']:,}")
print(f"  Attribute tags: {idx.get('attribute_tags', [])}")

print(f"\nTop 15 entity types:")
for t, c in list(idx["entity_type_counts"].items())[:15]:
    print(f"  {t:20s} {c:>8,}")

print(f"\nTop 15 block types:")
for n, c in list(idx["block_counts"].items())[:15]:
    print(f"  {n:40s} {c:>6,}")

if idx["attribute_tags"]:
    print(f"\nSample attributes (first 10):")
    for a in idx["attributes"][:10]:
        print(f"  owner={a['owner']:10s}  tag={a['tag']:20s}  value={a['value'][:60]}")
