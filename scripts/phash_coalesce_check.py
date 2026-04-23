"""Run pHash on every screenshot in a ScreenMap and list visual near-duplicates."""
import json, sys, itertools, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import imagehash
from PIL import Image

screenmap_path = sys.argv[1] if len(sys.argv) > 1 else 'workspace/cdtest/output/screen_map.json'
d = json.load(open(screenmap_path, encoding='utf-8'))
nodes = d.get('nodes', d.get('screen_map', {}).get('graph', {}).get('nodes', []))

items = []
for n in nodes:
    ss = n.get('screenshot_ref')
    if not ss:
        continue
    p = Path(ss)
    if not p.exists():
        continue
    try:
        img = Image.open(p)
        w, h = img.size
        img = img.crop((0, int(h * 0.05), w, int(h * 0.92)))
        ph = str(imagehash.phash(img, hash_size=8))
        items.append({
            'screen_id': n.get('screen_id', '')[:22],
            'label': (n.get('label') or '')[:40],
            'activity': (n.get('activity') or '').rsplit('.', 1)[-1][:25],
            'phash': ph,
            'filename': p.name,
        })
    except Exception as e:
        print(f'skip {p.name}: {e}')

print(f'Computed pHash for {len(items)} screenshots')
print()

pairs = []
for a, b in itertools.combinations(items, 2):
    dist = imagehash.hex_to_hash(a['phash']) - imagehash.hex_to_hash(b['phash'])
    if dist <= 12:
        pairs.append((dist, a, b))

pairs.sort(key=lambda t: t[0])
print(f'=== pHash pairs with distance <= 12 (candidates for visual duplicate): {len(pairs)}')
print()
for dist, a, b in pairs:
    mark = 'STRONG' if dist <= 4 else 'near  ' if dist <= 8 else 'loose '
    print(f'  [{mark}] dist={dist:2d}  {a["filename"]:28s} ({a["activity"]:25s} | {a["label"]})')
    print(f'                       {b["filename"]:28s} ({b["activity"]:25s} | {b["label"]})')
    print()
