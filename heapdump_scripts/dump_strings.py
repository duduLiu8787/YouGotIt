"""
dump_strings.py — Dump all unique Java String values from heap.

Walks every char[] (Java 8 String backing array, UTF-16BE) and writes
deduplicated, sorted strings to output.

Usage:
    python dump_strings.py <heapdump.hprof> [output_file]
        Default output: strings.txt next to script
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from hprof_walker import Walker


def main(fname, out_path='strings.txt'):
    strings = set()

    def on_char(aid, text):
        if text:
            # Normalize newlines so the file stays one-string-per-line
            strings.add(text.replace('\n', '\\n').replace('\r', '\\r'))

    w = Walker(fname)
    w.on_char_array = on_char
    w.on_progress = lambda n: print(f'  ...records {n}; unique strings: {len(strings)}', file=sys.stderr)
    w.run()

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f'# {len(strings)} unique strings from {fname}\n')
        for s in sorted(strings):
            f.write(s + '\n')

    print(f'Wrote {len(strings)} strings to {out_path}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.stderr.write('Usage: python dump_strings.py <heapdump.hprof> [output_file]\n')
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) >= 3 else 'strings.txt')
