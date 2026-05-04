"""
extract_cases.py — Extract case data + fragments from byte[] buffers.

Strategies (in order):
  1. Full <jsonString>{...}</jsonString>
  2. <jsonString>{... (closer truncated, balance forward)
  3. ...}</jsonString> (opener truncated, balance backward)
  4. Inner objects: {...AntibodyId/PathogenId/AntibodyName/PathogenName...}
     — captures lab result fragments left in buffer reuse
  5. Loose key-value fragments: any "ResultDay":"...", standalone IDs

Outputs grouped per byte[] buffer so you can see what each Tomcat
buffer accumulated over time.

Usage:
    python extract_cases.py <heapdump.hprof> [output_file] [--no-fragments]

If --no-fragments given, only strategies 1-3 (full envelopes) are run.
"""
import sys
import re
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from hprof_walker import Walker

# === Tag/anchor config ===
JSON_ANCHOR = 'ReportId'             # case envelope anchor
LAB_ANCHORS = ['AntibodyId', 'PathogenId', 'AntibodyName', 'PathogenName']
LOOSE_FIELDS = ['ResultDay', 'ResultInformDay', 'PHB_RECEIVED_DAY',
                'CDC_RECEIVED_DATE', 'DetermineDate', 'IDNO',
                'SampleId', 'BARCODE', 'CDC_SAMPLE_ID', 'CHART_NO']

JSON_FULL  = re.compile(rb'<jsonString>(.*?)</jsonString>', re.S)
JSON_OPEN  = re.compile(rb'<jsonString>(\{.*)', re.S)


def extract_balanced_json(text):
    depth = 0; in_str = False; esc = False
    for i, c in enumerate(text):
        if esc: esc = False; continue
        if c == '\\': esc = True; continue
        if c == '"': in_str = not in_str; continue
        if in_str: continue
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[:i + 1]
    return None


def extract_balanced_json_backward(text, end_idx):
    candidates = [j for j, c in enumerate(text[:end_idx]) if c == '{']
    for start in candidates:
        depth = 0; in_str = False; esc = False
        for k, c in enumerate(text[start:end_idx], start=start):
            if esc: esc = False; continue
            if c == '\\': esc = True; continue
            if c == '"': in_str = not in_str; continue
            if in_str: continue
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    if k == end_idx - 1:
                        return text[start:end_idx]
                    break
    return None


def find_inner_objects(text, must_contain):
    """Find all balanced {...} substrings that contain `must_contain`."""
    results = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != '{':
            i += 1; continue
        # Try to balance from here
        depth = 0; in_str = False; esc = False; end = -1
        for k in range(i, n):
            c = text[k]
            if esc: esc = False; continue
            if c == '\\': esc = True; continue
            if c == '"': in_str = not in_str; continue
            if in_str: continue
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = k; break
        if end > i:
            obj = text[i:end + 1]
            if must_contain in obj:
                results.append((i, obj))
            i = end + 1
        else:
            i += 1
    return results


def find_loose_field_fragments(text, fields, context=80):
    """Find each "field":"value" or "field":null occurrence with context."""
    results = []
    for field in fields:
        for m in re.finditer(rf'"{field}"\s*:\s*("[^"]*"|null|\d+)', text):
            ctx_start = max(0, m.start() - context)
            ctx_end = min(len(text), m.end() + context)
            results.append((m.start(), field, m.group(1), text[ctx_start:ctx_end]))
    return results


def main(fname, out_path=None, include_fragments=True):
    # buffer_data[aid] = {'envelopes':[], 'lab_objects':[], 'loose':[]}
    buffer_data = {}

    def get(aid):
        if aid not in buffer_data:
            buffer_data[aid] = {'envelopes': [], 'lab_objects': [], 'loose': []}
        return buffer_data[aid]

    def on_byte(aid, raw):
        # Need text for inner-object scans
        text = raw.decode('utf-8', errors='replace')

        # Strategy 1: full <jsonString>...</jsonString>
        full_seen = set()
        envelopes = []
        for m in JSON_FULL.finditer(raw):
            body = m.group(1).decode('utf-8', errors='replace').strip()
            if JSON_ANCHOR in body:
                envelopes.append(body)
                full_seen.add(m.span())

        # Strategy 2: opener present, closer truncated
        for m in JSON_OPEN.finditer(raw):
            if m.span() in full_seen: continue
            after = m.group(1).decode('utf-8', errors='replace')
            body = extract_balanced_json(after)
            if body and JSON_ANCHOR in body:
                if body.strip() not in (e.strip() for e in envelopes):
                    envelopes.append(body.strip())

        # Strategy 3: closer present, opener truncated
        for m in re.finditer(r'</jsonString>', text):
            end_idx = m.start()
            while end_idx > 0 and text[end_idx - 1] in ' \t\r\n':
                end_idx -= 1
            if end_idx == 0 or text[end_idx - 1] != '}':
                continue
            body = extract_balanced_json_backward(text, end_idx)
            if body and JSON_ANCHOR in body:
                if body.strip() not in (e.strip() for e in envelopes):
                    envelopes.append(body.strip())

        if envelopes:
            get(aid)['envelopes'].extend(envelopes)

        if not include_fragments:
            return

        # Strategy 4: inner lab objects
        lab_objs = []
        seen_lab = set()
        for anchor in LAB_ANCHORS:
            for pos, obj in find_inner_objects(text, anchor):
                # Dedup by content
                if obj in seen_lab: continue
                # Skip if obj is already inside an envelope
                if any(obj in env for env in envelopes):
                    continue
                seen_lab.add(obj)
                lab_objs.append((pos, obj))
        if lab_objs:
            get(aid)['lab_objects'].extend(lab_objs)

        # Strategy 5: loose field fragments not inside envelope or lab obj
        loose = find_loose_field_fragments(text, LOOSE_FIELDS)
        loose_filtered = []
        for pos, field, value, ctx in loose:
            # Skip if inside envelope or lab obj
            inside = False
            for env in envelopes:
                if env in text and text.find(env) <= pos < text.find(env) + len(env):
                    inside = True; break
            if inside: continue
            for _, obj in lab_objs:
                if text.find(obj) <= pos < text.find(obj) + len(obj):
                    inside = True; break
            if inside: continue
            loose_filtered.append((pos, field, value, ctx))
        if loose_filtered:
            get(aid)['loose'].extend(loose_filtered)

    w = Walker(fname)
    w.on_byte_array = on_byte
    w.on_progress = lambda n: print(f'  ...records {n}', file=sys.stderr)
    w.run()

    # === Output ===
    out = io.StringIO()
    total_envelopes = sum(len(d['envelopes']) for d in buffer_data.values())
    total_labs = sum(len(d['lab_objects']) for d in buffer_data.values())
    total_loose = sum(len(d['loose']) for d in buffer_data.values())
    n_buffers = len(buffer_data)

    print(f'# Extracted from {fname}', file=out)
    print(f'#   {n_buffers} byte[] buffer(s) with case data', file=out)
    print(f'#   {total_envelopes} full SOAP envelope(s) (strategies 1-3)', file=out)
    if include_fragments:
        print(f'#   {total_labs} inner lab result object(s) (strategy 4)', file=out)
        print(f'#   {total_loose} loose field fragment(s) (strategy 5)', file=out)
    print(file=out)

    # Group by buffer address
    for buf_idx, (aid, data) in enumerate(sorted(buffer_data.items()), 1):
        print(f'\n{"=" * 70}', file=out)
        print(f'## Buffer {buf_idx}: byte[] @ 0x{aid:x}', file=out)
        print(f'{"=" * 70}', file=out)

        if data['envelopes']:
            for i, env in enumerate(data['envelopes'], 1):
                print(f'\n--- Envelope {i} ---', file=out)
                print(env, file=out)

        if include_fragments and data['lab_objects']:
            print(f'\n--- Inner lab objects ({len(data["lab_objects"])}) ---', file=out)
            for i, (pos, obj) in enumerate(data['lab_objects'], 1):
                print(f'\n  [Lab {i}] @offset={pos}:', file=out)
                if len(obj) > 800:
                    obj = obj[:800] + '...[TRUNC]'
                print(f'  {obj}', file=out)

        if include_fragments and data['loose']:
            print(f'\n--- Loose field fragments ({len(data["loose"])}) ---', file=out)
            seen_loose = set()
            for pos, field, value, ctx in data['loose']:
                key = (field, value)
                if key in seen_loose: continue
                seen_loose.add(key)
                print(f'\n  {field} = {value}', file=out)
                print(f'    context: ...{ctx}...', file=out)

    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(out.getvalue())
        sys.stdout.write(f'Wrote {n_buffers} buffer(s), {total_envelopes} envelope(s), '
                         f'{total_labs} lab obj(s), {total_loose} loose frag(s) to {out_path}\n')
    else:
        sys.stdout.write(out.getvalue())


if __name__ == '__main__':
    args = sys.argv[1:]
    include_fragments = True
    if '--no-fragments' in args:
        include_fragments = False
        args.remove('--no-fragments')
    if len(args) < 1:
        sys.stderr.write('Usage: python extract_cases.py <heapdump.hprof> [output_file] [--no-fragments]\n')
        sys.exit(1)
    main(args[0], args[1] if len(args) >= 2 else None, include_fragments)
