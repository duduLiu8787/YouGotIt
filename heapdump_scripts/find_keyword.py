"""
find_keyword.py — Search byte[] and char[] for keyword(s).

Use cases:
  - Find PHI / credentials / specific strings hiding in HTTP buffers
  - Find SQL fragments, error messages, anything plain-text in heap

Usage:
    python find_keyword.py <heapdump.hprof> KEYWORD [KEYWORD2 ...] [-o output_file] [--dedup] [--max-per-array N]

Examples:
    python find_keyword.py heap.hprof password
    python find_keyword.py heap.hprof ReportId IDNO CHART_NO -o phi.txt
    python find_keyword.py heap.hprof "鉤端螺旋體病" --dedup
    python find_keyword.py heap.hprof ReportId --max-per-array 50

Each keyword is searched in BOTH byte[] (UTF-8) and char[] (UTF-16).
For each hit, prints the array address and surrounding context.

Flags:
  -o <file>          Write to file instead of stdout (recommended for many hits)
  --dedup            Suppress duplicate snippets (same context text)
  --max-per-array N  Stop after N hits in a single array (default 100)
  --context N        Bytes/chars of context to show on each side (default 100)
"""
import sys
import io
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from hprof_walker import Walker


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.stderr.write(__doc__)
        sys.exit(1)

    # Parse flags
    out_path = None
    dedup = False
    max_per_array = 100
    context_size = 100

    i = 0
    cleaned = []
    while i < len(args):
        a = args[i]
        if a == '-o':
            out_path = args[i+1]; i += 2
        elif a == '--dedup':
            dedup = True; i += 1
        elif a == '--max-per-array':
            max_per_array = int(args[i+1]); i += 2
        elif a == '--context':
            context_size = int(args[i+1]); i += 2
        else:
            cleaned.append(a); i += 1
    args = cleaned

    fname = args[0]
    keywords_str = args[1:]
    keywords_bytes = [k.encode('utf-8') for k in keywords_str]

    print(f'Searching {len(keywords_str)} keyword(s) in {fname}', file=sys.stderr)
    print(f'  dedup={dedup}, max_per_array={max_per_array}, context={context_size}', file=sys.stderr)

    hits = []  # (aid, source_type, keyword, snippet)

    def on_byte(aid, raw):
        for kw_b, kw_s in zip(keywords_bytes, keywords_str):
            pos = raw.find(kw_b)
            count = 0
            while pos >= 0:
                if count >= max_per_array:
                    hits.append((aid, 'byte[]', kw_s, f'...({max_per_array}+ more hits in this array)...'))
                    break
                ctx_start = max(0, pos - context_size)
                ctx_end = min(len(raw), pos + len(kw_b) + context_size)
                snippet = raw[ctx_start:ctx_end].decode('utf-8', errors='replace')
                hits.append((aid, 'byte[]', kw_s, snippet))
                pos = raw.find(kw_b, pos + len(kw_b))
                count += 1

    def on_char(aid, text):
        for kw_s in keywords_str:
            pos = text.find(kw_s)
            count = 0
            while pos >= 0:
                if count >= max_per_array:
                    hits.append((aid, 'char[]', kw_s, f'...({max_per_array}+ more hits in this array)...'))
                    break
                ctx_start = max(0, pos - context_size)
                ctx_end = min(len(text), pos + len(kw_s) + context_size)
                snippet = text[ctx_start:ctx_end]
                hits.append((aid, 'char[]', kw_s, snippet))
                pos = text.find(kw_s, pos + len(kw_s))
                count += 1

    w = Walker(fname)
    w.on_byte_array = on_byte
    w.on_char_array = on_char
    w.on_progress = lambda n: print(f'  ...records {n}; raw hits {len(hits)}', file=sys.stderr)
    w.run()

    # Build output
    out = io.StringIO()

    # Group by keyword
    by_kw = {}
    for aid, src, kw, snip in hits:
        by_kw.setdefault(kw, []).append((aid, src, snip))

    # Stats
    total_raw = len(hits)
    print(f'# Total raw hits: {total_raw}', file=out)
    if dedup:
        total_dedup = sum(len(set((s,) for _,_,s in v)) for v in by_kw.values())
        print(f'# After --dedup:  {total_dedup}', file=out)
    print(f'# Source: {fname}\n', file=out)

    for kw in keywords_str:
        kh = by_kw.get(kw, [])
        print(f'\n========== Keyword: "{kw}"  (raw: {len(kh)}) ==========', file=out)
        seen = set()
        shown = 0
        for aid, src, snip in kh:
            snip_clean = re.sub(r'[\x00-\x08\x0e-\x1f\x7f]', '·', snip)
            if dedup:
                # Dedup by FULL snippet (not just prefix)
                if snip_clean in seen:
                    continue
                seen.add(snip_clean)
            shown += 1
            print(f'\n  [{shown}] {src} @ 0x{aid:x}:', file=out)
            # Print whole snippet (no truncation in display)
            print(f'    ...{snip_clean}...', file=out)

    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(out.getvalue())
        sys.stdout.write(f'Wrote {len(out.getvalue())} bytes ({total_raw} raw hits) to {out_path}\n')
    else:
        sys.stdout.write(out.getvalue())


if __name__ == '__main__':
    main()
