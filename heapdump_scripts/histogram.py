"""
histogram.py — Object instance count by class.

Counts instances of every class (and primitive arrays) in the heap.
Output is sorted by count descending.

Usage:
    python histogram.py <heapdump.hprof> [output_file]
        Default output: histogram.txt
"""
import sys
import io
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from hprof_walker import Walker, TYPE_NAME


def main(fname, out_path='histogram.txt'):
    counts = Counter()

    def on_instance(oid, cid, raw):
        counts[('class', cid)] += 1

    def on_obj_array(aid, cid, ids):
        counts[('object_array',)] += 1

    def on_byte(aid, raw):       counts[('prim', 8)] += 1
    def on_char(aid, text):      counts[('prim', 5)] += 1
    def on_int(aid, raw):        counts[('prim', 10)] += 1
    def on_other(aid, tt, raw):  counts[('prim', tt)] += 1

    w = Walker(fname)
    w.on_instance = on_instance
    w.on_obj_array = on_obj_array
    w.on_byte_array = on_byte
    w.on_char_array = on_char
    w.on_int_array = on_int
    w.on_other_prim_array = on_other
    w.on_progress = lambda n: print(f'  ...records {n}', file=sys.stderr)
    w.run()

    # Resolve class ids to names
    class_names = w.all_classes()
    rows = []
    for key, n in counts.items():
        if key[0] == 'class':
            name = class_names.get(key[1], f'<unknown class id 0x{key[1]:x}>')
        elif key[0] == 'object_array':
            name = 'Object[]'
        elif key[0] == 'prim':
            name = TYPE_NAME.get(key[1], f'<type {key[1]}>') + '[]'
        rows.append((n, name))
    rows.sort(reverse=True)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f'# {len(rows)} classes with instances; total {sum(c for c,_ in rows):,} objects\n')
        f.write(f'{"COUNT":>10s}  CLASS\n')
        for n, name in rows:
            f.write(f'{n:>10d}  {name}\n')

    print(f'Wrote histogram ({len(rows)} entries) to {out_path}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.stderr.write('Usage: python histogram.py <heapdump.hprof> [output_file]\n')
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) >= 3 else 'histogram.txt')
