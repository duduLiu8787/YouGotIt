"""
hprof_walker.py — Reusable hprof binary walker.

Walks a Java heap dump (hprof) file and dispatches each record to user callbacks.
Pure stdlib (struct only). Single-pass O(n).

Usage:
    from hprof_walker import Walker

    w = Walker('heapdump.hprof')
    w.on_byte_array = lambda aid, raw: print(f'byte[{len(raw)}] @ 0x{aid:x}')
    w.on_class = lambda cid, name: print(f'class {name}')
    w.run()

Callback signatures (all optional, set to lambda or None):
    on_utf8(sid, text)          # constant pool string (class/field/method names)
    on_class(cid, name)         # LOAD_CLASS record  (cid = class object id, name = dot-form)
    on_byte_array(aid, raw)     # byte[] content
    on_char_array(aid, text)    # char[] content (already utf-16be decoded to str)
    on_int_array(aid, raw)      # int[] raw bytes
    on_other_prim_array(aid, type_tag, raw)  # short[]/long[]/float[]/...
    on_instance(oid, cid, raw)  # object instance (raw field bytes)
    on_obj_array(aid, cid, ids) # Object[] (list of element object ids)
    on_progress(record_count)   # called every 100k records
"""
import struct
import sys


# === hprof tags ===
HPROF_UTF8                  = 0x01
HPROF_LOAD_CLASS            = 0x02
HPROF_HEAP_DUMP             = 0x0C
HPROF_HEAP_DUMP_SEGMENT     = 0x1C

HPROF_GC_ROOT_UNKNOWN       = 0xFF
HPROF_GC_ROOT_JNI_GLOBAL    = 0x01
HPROF_GC_ROOT_JNI_LOCAL     = 0x02
HPROF_GC_ROOT_JAVA_FRAME    = 0x03
HPROF_GC_ROOT_NATIVE_STACK  = 0x04
HPROF_GC_ROOT_STICKY_CLASS  = 0x05
HPROF_GC_ROOT_THREAD_BLOCK  = 0x06
HPROF_GC_ROOT_MONITOR_USED  = 0x07
HPROF_GC_ROOT_THREAD_OBJ    = 0x08
HPROF_GC_CLASS_DUMP         = 0x20
HPROF_GC_INSTANCE_DUMP      = 0x21
HPROF_GC_OBJ_ARRAY_DUMP     = 0x22
HPROF_GC_PRIM_ARRAY_DUMP    = 0x23

# Primitive type tags (used in PRIM_ARRAY_DUMP)
T_BOOLEAN = 4
T_CHAR    = 5
T_FLOAT   = 6
T_DOUBLE  = 7
T_BYTE    = 8
T_SHORT   = 9
T_INT     = 10
T_LONG    = 11

TYPE_SIZE = {2: None, 4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
TYPE_NAME = {2: 'object', 4: 'boolean', 5: 'char', 6: 'float',
             7: 'double', 8: 'byte', 9: 'short', 10: 'int', 11: 'long'}


def _noop(*args, **kwargs):
    pass


class Walker:
    def __init__(self, path):
        self.path = path
        self.id_size = 8     # default 64-bit; overridden by header
        # callbacks (override after construction)
        self.on_utf8           = _noop
        self.on_class          = _noop
        self.on_byte_array     = _noop
        self.on_char_array     = _noop
        self.on_int_array      = _noop
        self.on_other_prim_array = _noop
        self.on_instance       = _noop
        self.on_obj_array      = _noop
        self.on_progress       = _noop
        # internal state
        self._utf8_pool = {}        # sid -> str
        self._class_to_name = {}    # class object id -> name string
        # pending LOAD_CLASS records waiting for UTF8 to resolve
        self._pending_classes = []

    def _read_id(self, f):
        return (struct.unpack('>Q', f.read(8))[0]
                if self.id_size == 8
                else struct.unpack('>I', f.read(4))[0])

    def run(self):
        """Parse the file and dispatch callbacks. Returns total record count."""
        record_count = 0
        with open(self.path, 'rb') as f:
            # === Header ===
            while f.read(1) != b'\0':
                pass
            self.id_size = struct.unpack('>I', f.read(4))[0]
            f.read(8)  # timestamp

            # === Records ===
            while True:
                tb = f.read(1)
                if not tb:
                    break
                tag = tb[0]
                f.read(4)
                length = struct.unpack('>I', f.read(4))[0]
                end = f.tell() + length

                if tag == HPROF_UTF8:
                    sid = self._read_id(f)
                    text = f.read(length - self.id_size).decode('utf-8', errors='replace')
                    self._utf8_pool[sid] = text
                    self.on_utf8(sid, text)

                elif tag == HPROF_LOAD_CLASS:
                    f.read(4)                 # serial number
                    cid = self._read_id(f)
                    f.read(4)                 # stack trace serial
                    nid = self._read_id(f)
                    name = self._utf8_pool.get(nid, f'<unresolved-{nid:x}>').replace('/', '.')
                    self._class_to_name[cid] = name
                    self.on_class(cid, name)

                elif tag in (HPROF_HEAP_DUMP, HPROF_HEAP_DUMP_SEGMENT):
                    self._parse_heap(f, end)

                else:
                    f.seek(end)

                record_count += 1
                if record_count % 100000 == 0:
                    self.on_progress(record_count)

        return record_count

    def _parse_heap(self, f, end):
        while f.tell() < end:
            t = f.read(1)[0]
            if t == HPROF_GC_ROOT_JNI_GLOBAL:
                f.read(self.id_size); f.read(self.id_size)
            elif t == HPROF_GC_ROOT_JNI_LOCAL:
                f.read(self.id_size); f.read(8)
            elif t == HPROF_GC_ROOT_JAVA_FRAME:
                f.read(self.id_size); f.read(8)
            elif t == HPROF_GC_ROOT_NATIVE_STACK:
                f.read(self.id_size); f.read(4)
            elif t in (HPROF_GC_ROOT_STICKY_CLASS,
                       HPROF_GC_ROOT_MONITOR_USED,
                       HPROF_GC_ROOT_UNKNOWN):
                f.read(self.id_size)
            elif t == HPROF_GC_ROOT_THREAD_BLOCK:
                f.read(self.id_size); f.read(4)
            elif t == HPROF_GC_ROOT_THREAD_OBJ:
                f.read(self.id_size); f.read(4); f.read(4)

            elif t == HPROF_GC_CLASS_DUMP:
                f.read(self.id_size); f.read(4)
                for _ in range(6):
                    f.read(self.id_size)
                f.read(4)
                cn = struct.unpack('>H', f.read(2))[0]
                for _ in range(cn):
                    f.read(2); tt = f.read(1)[0]
                    f.read(TYPE_SIZE.get(tt) or self.id_size)
                sn = struct.unpack('>H', f.read(2))[0]
                for _ in range(sn):
                    f.read(self.id_size); tt = f.read(1)[0]
                    f.read(TYPE_SIZE.get(tt) or self.id_size)
                fn = struct.unpack('>H', f.read(2))[0]
                for _ in range(fn):
                    f.read(self.id_size); f.read(1)

            elif t == HPROF_GC_INSTANCE_DUMP:
                oid = self._read_id(f); f.read(4); cid = self._read_id(f)
                ilen = struct.unpack('>I', f.read(4))[0]
                raw = f.read(ilen)
                self.on_instance(oid, cid, raw)

            elif t == HPROF_GC_OBJ_ARRAY_DUMP:
                aid = self._read_id(f); f.read(4)
                alen = struct.unpack('>I', f.read(4))[0]
                cid = self._read_id(f)
                element_bytes = f.read(alen * self.id_size)
                if self.on_obj_array is not _noop:
                    fmt = '>' + ('Q' if self.id_size == 8 else 'I') * alen
                    ids = list(struct.unpack(fmt, element_bytes)) if alen else []
                    self.on_obj_array(aid, cid, ids)

            elif t == HPROF_GC_PRIM_ARRAY_DUMP:
                aid = self._read_id(f); f.read(4)
                alen = struct.unpack('>I', f.read(4))[0]
                tt = f.read(1)[0]
                if tt == T_BYTE:
                    raw = f.read(alen)
                    self.on_byte_array(aid, raw)
                elif tt == T_CHAR:
                    raw = f.read(alen * 2)
                    text = raw.decode('utf-16be', errors='replace')
                    self.on_char_array(aid, text)
                elif tt == T_INT:
                    raw = f.read(alen * 4)
                    self.on_int_array(aid, raw)
                else:
                    sz = TYPE_SIZE.get(tt, 0)
                    raw = f.read(alen * sz)
                    self.on_other_prim_array(aid, tt, raw)

            else:
                # Unknown sub-tag — bail to avoid corrupt parse
                print(f'WARN: unknown heap dump sub-tag {t:#x} at {f.tell()}', file=sys.stderr)
                return

    # === convenience helpers ===
    def class_name(self, cid):
        """Look up class name by class object id (after walk)."""
        return self._class_to_name.get(cid)

    def all_classes(self):
        """Returns dict {class_id: class_name} after walk."""
        return dict(self._class_to_name)
