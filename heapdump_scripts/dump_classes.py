"""
dump_classes.py — Dump all loaded Java class names from heap.

Usage:
    python dump_classes.py <heapdump.hprof> [output_file]
        Default output: classes.txt
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from hprof_walker import Walker


def main(fname, out_path='classes.txt'):
    names = set()

    def on_class(cid, name):
        names.add(name)

    w = Walker(fname)
    w.on_class = on_class
    w.on_progress = lambda n: print(f'  ...records {n}', file=sys.stderr)
    w.run()

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f'# {len(names)} unique classes from {fname}\n')
        for n in sorted(names):
            f.write(n + '\n')

    print(f'Wrote {len(names)} class names to {out_path}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.stderr.write('Usage: python dump_classes.py <heapdump.hprof> [output_file]\n')
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) >= 3 else 'classes.txt')
