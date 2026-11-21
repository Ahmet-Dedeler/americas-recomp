"""Carve Bink 2 videos out of an RPF9 archive (they are stored raw at 4 KiB aligned offsets; no table of contents needed).
usage: carve_bink.py <movies.rpf> <out_dir> [count=1]
A Bink 2 file starts with 'KB2' + a version letter, then a u32 = file size - 8. The scan reads the archive in 64 MiB chunks."""
import os, re, struct, sys
src, out = sys.argv[1], sys.argv[2]
count = int(sys.argv[3]) if len(sys.argv) > 3 else 1
os.makedirs(out, exist_ok=True)
pat = re.compile(rb'KB2[a-z]')
f = open(src, 'rb')
off, prev, n = 0, b'', 0
while n < count:
    d = f.read(1 << 26)
    if not d:
        break
    for m in pat.finditer(prev + d):
        pos = off - len(prev) + m.start()
        if pos % 4096:
            continue                      # an aligned header only: other hits are bytes inside other data
        here = f.tell()
        f.seek(pos)
        size = struct.unpack_from('<I', f.read(8), 4)[0] + 8
        f.seek(pos)
        open(os.path.join(out, 'movie%d.bk2' % n), 'wb').write(f.read(size))
        print('movie%d.bk2' % n, hex(pos), size)
        f.seek(here)
        n += 1
        if n >= count:
            break
    prev, off = d[-8:], off + len(d)
