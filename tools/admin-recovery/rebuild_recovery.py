# -*- coding: utf-8 -*-
"""Rebuild both NSIS installers with admin password-recovery changes.

Starts from /tmp/nikan/out/*.exe (the QA383940-protected builds), replaces the
modified payload files, refreshes build-manifest.json hashes, rewrites every
EW_EXTRACTFILE data offset and the EW_WRITEUNINSTALLER offset in the NSIS
header, then recompresses the solid LZMA stream and fixes firstheader + CRC.
"""
import lzma, zlib, struct, hashlib, re, ast, os, gc, json

STUB = 119296
FH = 28
FILTERS = [{'id': lzma.FILTER_LZMA1, 'dict_size': 1 << 23, 'lc': 3, 'lp': 0, 'pb': 2,
            'mode': lzma.MODE_NORMAL, 'nice_len': 64, 'mf': lzma.MF_BT4}]
MOD = '/tmp/nikan/mod'
OUT2 = '/tmp/nikan/out2'
os.makedirs(OUT2, exist_ok=True)

# Files whose bytes are replaced (relative paths as used by build-manifest.json)
CODE_FILES = ['README.md', 'app/__init__.py', 'app/static/index.html', 'app/static/app.js']
JOBS = [
    dict(kind='setup',  name='NikanTraining-Setup.exe',
         extra={}, manifest_extra=[]),
    dict(kind='server', name='NikanTraining-Network-Server-Setup.exe',
         extra={'NETWORK-README.md': 'NETWORK-README.md'}, manifest_extra=['NETWORK-README.md']),
]

def read(p): return open(p, 'rb').read()

def rebuild(job):
    kind = job['kind']
    print('\n====', kind, '====', flush=True)
    inp = '/tmp/nikan/out/' + job['name']
    outp = OUT2 + '/' + job['name']
    d = read(inp)
    flags, sig, n1, n2, n3, header_size, total = struct.unpack_from('<7I', d, STUB)
    assert sig == 0xDEADBEEF
    assert total == len(d) - STUB, (total, len(d) - STUB)
    assert (zlib.crc32(d[512:-4]) & 0xffffffff) == struct.unpack('<I', d[-4:])[0], 'original CRC mismatch'
    ds = STUB + FH
    LZMA_HDR = d[ds:ds+5]
    assert LZMA_HDR == b'\x5d\x00\x00\x80\x00', LZMA_HDR
    comp_orig = d[ds+5:-4]
    dd = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=FILTERS)
    dec = dd.decompress(comp_orig)
    assert dd.eof and not dd.unused_data, ('lzma stream issue', dd.eof, len(dd.unused_data))
    print(' uncompressed', len(dec), 'bytes', flush=True)

    hl = struct.unpack_from('<I', dec, 0)[0]
    assert hl == header_size, (hl, header_size)
    D = 4 + hl                       # start of data block (first 4-byte size marker)
    hdr = bytearray(dec[4:4+hl])
    ENT_OFF = struct.unpack_from('<I', hdr, 4 + 2*8)[0]
    ENT_NUM = struct.unpack_from('<I', hdr, 4 + 2*8 + 4)[0]
    print(' header', hl, 'entries', ENT_NUM, 'at', ENT_OFF, flush=True)

    files = ast.literal_eval(open(f'/tmp/nikan/file_list_{kind}.txt').read())
    nfiles = len(files)

    # Parse the data block into unique [size, bytes] blobs. 7zz lists one entry
    # per extraction, so several paths can share one blob (identical Offset).
    # The data block ends where EW_WRITEUNINSTALLER says the uninstaller blob
    # begins; we find that offset from the header first.
    op62_pre = []
    for i in range(ENT_NUM):
        w = struct.unpack_from('<I', hdr, ENT_OFF + i*28)[0]
        if w == 62:
            op62_pre.append(struct.unpack_from('<6I', hdr, ENT_OFF + i*28 + 4)[1])
    assert len(op62_pre) == 1, op62_pre
    trailing_off_old = op62_pre[0]
    trailing = dec[D + trailing_off_old:]
    print(' data block', trailing_off_old, 'bytes, trailing', len(trailing), 'bytes', flush=True)

    items = []
    blob_off = []
    pos = 0
    while pos < trailing_off_old:
        size = struct.unpack_from('<I', dec, D + pos)[0]
        assert 0 <= size <= len(dec) - (D + pos + 4), (size, pos)
        blob_off.append(pos)
        items.append([size, dec[D + pos + 4 : D + pos + 4 + size]])
        pos += 4 + size
    assert pos == trailing_off_old, (pos, trailing_off_old)
    nblobs = len(items)
    blob_idx_by_off = {o: j for j, o in enumerate(blob_off)}
    assert len(blob_idx_by_off) == nblobs
    # Cross-check every 7zz entry against its blob.
    for i, (path, sz, off) in enumerate(files):
        assert off in blob_idx_by_off, ('unknown 7zz offset', i, path, off)
        j = blob_idx_by_off[off]
        assert items[j][0] == sz, (i, path, items[j][0], sz)
    assert trailing_off_old == files[-1][2] + files[-1][1] + 4
    print(' parsed', nblobs, 'unique blobs for', nfiles, 'extractions; offsets verified', flush=True)

    idx_by_path = {p: blob_idx_by_off[o] for (p, _, o) in files}
    old_offsets = set(blob_off)

    # Verify the only data-block offset references in the header are
    # EW_EXTRACTFILE(20) param2 and EW_WRITEUNINSTALLER(62) param1.
    op20, op62 = [], []
    for i in range(ENT_NUM):
        w = struct.unpack_from('<I', hdr, ENT_OFF + i*28)[0]
        ps = struct.unpack_from('<6I', hdr, ENT_OFF + i*28 + 4)
        if w == 20:
            op20.append((i, ps[2]))
            assert ps[2] in old_offsets, ('op20 unknown offset', i, ps[2])
        if w == 62:
            op62.append((i, ps[1]))
    assert len(op62) == 1 and op62[0][1] == trailing_off_old, op62
    print(' opcode20 refs', len(op20), 'opcode62 ref', op62, flush=True)

    # Build replacement bytes.
    replacements = {}
    for rel in CODE_FILES:
        replacements[rel] = read(f'{MOD}/{kind}/{rel}')
    for rel, src in job['extra'].items():
        replacements[rel] = read(f'{MOD}/{kind}/{src}')
    for rel in replacements:
        assert rel in idx_by_path, ('missing payload path', rel)

    # Update build-manifest.json in place (same byte length).
    man_rel = 'build-manifest.json'
    mi = idx_by_path[man_rel]
    man_bytes = bytes(items[mi][1])
    assert man_bytes.startswith(b'{')
    man_text = man_bytes.decode('utf-8')
    assert b'595e1091cdbf171d725ae251e90187d1ec4389053df570e6f34f221af60a8a96' in man_bytes, 'workbook hash lost'
    manifest = json.loads(man_text)
    for rel, data in replacements.items():
        old = manifest['files'][rel]
        new = hashlib.sha256(data).hexdigest()
        assert re.fullmatch(r'[0-9a-f]{64}', old), (rel, old)
        pattern = r'("' + re.escape(rel) + r'"\s*:\s*")' + old + r'(")'
        man_text, n = re.subn(pattern, lambda m: m.group(1) + new + m.group(2), man_text)
        assert n == 1, ('manifest hash replace failed', rel, n)
        print(f'  manifest {rel:28s} {old[:12]} -> {new[:12]}', flush=True)
    new_man = man_text.encode('utf-8')
    assert len(new_man) == len(man_bytes), (len(new_man), len(man_bytes))
    json.loads(new_man.decode('utf-8'))
    replacements[man_rel] = new_man

    # Apply replacements to the data-block items.
    for rel, data in replacements.items():
        items[idx_by_path[rel]][1] = data
        print(f'  replace {rel:28s} {items[idx_by_path[rel]][0]:7d} -> {len(data):7d} bytes', flush=True)

    # New offsets and data block (per unique blob).
    new_off_by_old = {}
    running = 0
    for j in range(nblobs):
        new_off_by_old[blob_off[j]] = running
        running += 4 + len(items[j][1])
    new_trailing_off = running
    new_data = b''.join(struct.pack('<I', len(data)) + data for _, data in items)
    assert len(new_data) == new_trailing_off

    # Patch header: extract offsets, uninstaller offset, DisplayVersion.
    for i, old in op20:
        struct.pack_into('<I', hdr, ENT_OFF + i*28 + 4 + 2*4, new_off_by_old[old])
    struct.pack_into('<I', hdr, ENT_OFF + op62[0][0]*28 + 4 + 1*4, new_trailing_off)

    old_ver = '0.10.0'.encode('utf-16le')
    new_ver = '0.10.2'.encode('utf-16le')
    assert len(old_ver) == len(new_ver)
    vc = bytes(hdr).count(old_ver)
    assert vc == 1, ('unexpected DisplayVersion count', vc)
    hdr = bytearray(bytes(hdr).replace(old_ver, new_ver))
    print(' header patched: new trailing offset', new_trailing_off,
          'DisplayVersion 0.10.0 -> 0.10.2', flush=True)

    new_dec = dec[:4] + bytes(hdr) + new_data + trailing
    print(' new uncompressed', len(new_dec), 'bytes (was', len(dec), ')', flush=True)

    # Recompress.
    c = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=FILTERS)
    new_comp = c.compress(new_dec) + c.flush()
    print(' compressed', len(new_comp), 'bytes (was', len(comp_orig), ')', flush=True)

    new_fh = bytearray(d[STUB:STUB+FH])
    struct.pack_into('<I', new_fh, 24, FH + 5 + len(new_comp) + 4)
    out = bytearray(d[:STUB] + bytes(new_fh) + LZMA_HDR + new_comp + b'\x00'*4)
    crc = zlib.crc32(out[512:-4]) & 0xffffffff
    out[-4:] = struct.pack('<I', crc)
    open(outp, 'wb').write(out)
    print(' written', outp, 'size', len(out), 'CRC %08X' % crc, flush=True)

    # Self-check the rebuilt stream.
    dd2 = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=FILTERS)
    dec2 = dd2.decompress(bytes(out[ds+5:-4]))
    assert dd2.eof and not dd2.unused_data
    assert dec2 == new_dec, 'rebuilt stream differs from patched data'
    assert (zlib.crc32(out[512:-4]) & 0xffffffff) == struct.unpack('<I', out[-4:])[0]
    # Verify header offsets in the rebuilt stream.
    hl2 = struct.unpack_from('<I', dec2, 0)[0]
    assert hl2 == hl
    D2 = 4 + hl2
    h2 = dec2[4:4+hl2]
    eo2 = struct.unpack_from('<I', h2, 4 + 2*8)[0]
    en2 = struct.unpack_from('<I', h2, 4 + 2*8 + 4)[0]
    assert (eo2, en2) == (ENT_OFF, ENT_NUM)
    for i, old in op20:
        assert struct.unpack_from('<I', h2, eo2 + i*28 + 4 + 2*4)[0] == new_off_by_old[old]
    assert struct.unpack_from('<I', h2, eo2 + op62[0][0]*28 + 4 + 1*4)[0] == new_trailing_off
    assert '0.10.2'.encode('utf-16le') in h2 and '0.10.0'.encode('utf-16le') not in h2
    print(' self-check OK', flush=True)
    del d, dec, dec2, out, new_comp, new_dec, new_data
    gc.collect()
    return outp

for job in JOBS:
    rebuild(job)
print('\nALL DONE')
