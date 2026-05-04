"""
extract_http_responses.py — Find cached HTTP request/response bytes in heap.

Looks for HTTP-shaped byte[] arrays (Tomcat input/output buffers,
gzip-compressed bodies, Set-Cookie headers, etc.) and dumps them.

Useful for:
  - Recovering recent HTTP requests (URI, headers, cookies, source IP)
  - Recovering cached response bodies (Spring Boot Actuator JSON, etc.)

Usage:
    python extract_http_responses.py <heapdump.hprof> [output_file]
        Default output: http_responses.txt
"""
import sys
import io
import gzip

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from hprof_walker import Walker


def is_http_shaped(raw):
    """Heuristic: does this byte[] look like HTTP request/response?"""
    head = raw[:300]
    return (b'HTTP/1.1' in head
            or b'GET /' in head or b'POST /' in head or b'PUT /' in head
            or b'Set-Cookie' in head
            or b'Content-Encoding: gzip' in raw[:1000])


def try_decompress(body):
    """Try direct gzip decompress, then chunked-then-gzip."""
    # Direct gzip
    for offset in range(min(50, len(body))):
        if body[offset:offset+2] == b'\x1f\x8b':
            for end in [len(body), 8000, 4000, 2000, 1000]:
                try:
                    return gzip.decompress(body[offset:end]).decode('utf-8', errors='replace')
                except: continue
    # Chunked then gzip
    try:
        buf = b''; i = 0
        while i < len(body):
            eol = body.find(b'\r\n', i)
            if eol < 0: break
            line = body[i:eol].strip()
            if not line: break
            try: size = int(line.split(b';')[0], 16)
            except: break
            if size == 0: break
            i = eol + 2
            buf += body[i:i+size]
            i += size + 2
        if buf and buf[:2] == b'\x1f\x8b':
            return gzip.decompress(buf).decode('utf-8', errors='replace')
    except: pass
    return None


def main(fname, out_path='http_responses.txt'):
    candidates = []   # (aid, raw)

    def on_byte(aid, raw):
        if len(raw) >= 200 and is_http_shaped(raw):
            candidates.append((aid, raw))

    w = Walker(fname)
    w.on_byte_array = on_byte
    w.on_progress = lambda n: print(f'  ...records {n}; candidates so far {len(candidates)}', file=sys.stderr)
    w.run()

    out = io.StringIO()
    print(f'# Found {len(candidates)} HTTP-shaped byte[] in {fname}\n', file=out)

    for aid, raw in candidates:
        head_end = raw.find(b'\r\n\r\n')
        if head_end < 0:
            head_end = raw.find(b'\n\n')
        if head_end < 0:
            head_end = min(2000, len(raw))
        headers = raw[:head_end].decode('utf-8', errors='replace')
        body = raw[head_end:]

        print(f'\n========== byte[{len(raw)}] @ 0x{aid:x} ==========', file=out)
        print('--- HEADERS ---', file=out)
        print(headers[:2000], file=out)

        # Try decompression
        decoded = try_decompress(body)
        if decoded:
            print('--- DECOMPRESSED BODY ---', file=out)
            print(decoded[:5000], file=out)
        else:
            # Show raw text portion (often Tomcat buffer leftovers from previous requests)
            text = body.decode('utf-8', errors='replace')
            # Strip control chars
            import re
            text = re.sub(r'[\x00-\x08\x0e-\x1f\x7f]', '·', text)
            print('--- BODY (raw text view) ---', file=out)
            print(text[:3000], file=out)

    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(out.getvalue())
        print(f'Wrote {len(candidates)} HTTP buffers to {out_path}')
    else:
        sys.stdout.write(out.getvalue())


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.stderr.write('Usage: python extract_http_responses.py <heapdump.hprof> [output_file]\n')
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) >= 3 else 'http_responses.txt')
