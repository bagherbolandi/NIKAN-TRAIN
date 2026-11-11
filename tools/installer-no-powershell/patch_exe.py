# -*- coding: utf-8 -*-
"""Hotfix: neutralise the PowerShell preflight call in the 0.11.2 desktop installer.

Same-length UTF-16 string replacement inside the NSIS header, so no data-block
offsets change. The preflight command is replaced by a harmless cmd.exe call
that returns 0; nsExec still succeeds and the installer proceeds to
preflight_ok. Solid LZMA stream is recompressed and firstheader/CRC fixed.
"""
import lzma, zlib, struct, hashlib

SRC = '/tmp/nikan_new/NikanTraining-Desktop-0.11.2-Setup.exe'
OUT = '/tmp/nikan_new/fix/NikanTraining-Desktop-0.11.2-NoPowerShell-Setup.exe'
STUB, FH = 51712, 28
FILTERS = [{'id': lzma.FILTER_LZMA1, 'dict_size': 1 << 23, 'lc': 3, 'lp': 0, 'pb': 2,
            'mode': lzma.MODE_NORMAL, 'nice_len': 64, 'mf': lzma.MF_BT4}]

d = open(SRC, 'rb').read()
flags, sig, n1, n2, n3, header_size, total = struct.unpack_from('<7I', d, STUB)
assert sig == 0xDEADBEEF and total == len(d) - STUB
assert (zlib.crc32(d[512:-4]) & 0xffffffff) == struct.unpack('<I', d[-4:])[0]
ds = STUB + FH
LZMA_HDR = d[ds:ds+5]
assert LZMA_HDR == b'\x5d\x00\x00\x80\x00'
comp_orig = d[ds+5:-4]
dd = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=FILTERS)
dec = dd.decompress(comp_orig)
assert dd.eof and not dd.unused_data
hl = struct.unpack_from('<I', dec, 0)[0]
assert hl == header_size
print('decompressed', len(dec), 'header', hl)

hdr = dec[4:4+hl]
old_cmd = ('"\x02┥\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -NonInteractive '
           '-ExecutionPolicy Bypass -File "\x03肚\\installer-preflight.ps1" -InstallDir "\x03肕"')
old_b = old_cmd.encode('utf-16le')
print('old command utf16 bytes', len(old_b), 'occurrences', hdr.count(old_b))
assert hdr.count(old_b) == 1
off = hdr.find(old_b)
assert off == 86418, off
# Sanity: the string is null-terminated right after old_b.
assert hdr[off+len(old_b):off+len(old_b)+2] == b'\x00\x00'

# Build a same-length replacement: "<$SYSDIR>\cmd.exe" /c exit 0 + padding spaces.
prefix = b'\x22\x00' + bytes.fromhex('02002525') + b'\x5c\x00'   # '"' + $SYSDIR marker + '\'
core = 'cmd.exe" /c exit 0'.encode('utf-16le')
assert len(old_b) - len(prefix) - len(core) >= 0
pad_n = (len(old_b) - len(prefix) - len(core)) // 2
assert len(prefix) + len(core) + pad_n*2 == len(old_b)
new_b = prefix + core + b'\x20\x00' * pad_n
print('new command:', repr(new_b.decode('utf-16le')))
assert b'powershell' not in new_b.lower() and b'ExecutionPolicy' not in new_b

assert dec[4+off:4+off+len(old_b)] == old_b
dec = bytearray(dec)
dec[4+off:4+off+len(old_b)] = new_b
dec = bytes(dec)
assert 'powershell.exe'.encode('utf-16le') not in dec[4:4+hl]
assert 'ExecutionPolicy'.encode('utf-16le') not in dec[4:4+hl]

c = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=FILTERS)
new_comp = c.compress(dec) + c.flush()
print('compressed', len(new_comp), 'was', len(comp_orig))
new_fh = bytearray(d[STUB:STUB+FH])
struct.pack_into('<I', new_fh, 24, FH + 5 + len(new_comp) + 4)
out = bytearray(d[:STUB] + bytes(new_fh) + LZMA_HDR + new_comp + b'\x00'*4)
crc = zlib.crc32(out[512:-4]) & 0xffffffff
out[-4:] = struct.pack('<I', crc)
open(OUT, 'wb').write(out)
print('written', OUT, len(out), 'CRC %08X' % crc, 'sha256', hashlib.sha256(out).hexdigest())

# self-check
dd2 = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=FILTERS)
dec2 = dd2.decompress(bytes(out[ds+5:-4]))
assert dd2.eof and not dd2.unused_data and dec2 == dec
assert (zlib.crc32(out[512:-4]) & 0xffffffff) == struct.unpack('<I', out[-4:])[0]
print('self-check OK')
