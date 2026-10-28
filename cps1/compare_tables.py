# -*- coding: utf-8 -*-
"""Compare MAME's OKIM6295 sample-table interpretation with the converter's
MSM6295 interpretation for sf2ce. This tests whether the note->sample
assignment could be off."""
import sys
sys.path.insert(0, r"d:\works\cps-music-convertor\cps1")
from pathlib import Path
import convert_to_midi as c

game = c.read_game(Path(r"d:\works\cps-music-convertor\cps1\games\sf2ce.xml"))
rom = c.load_pcm_rom(game)
print(f"PCM ROM size = {len(rom)} bytes = 0x{len(rom):x}")

def mame_table(rom, count=128):
    """MAME okim6295: entry N at N*8; start=bytes0-2, stop=bytes3-5."""
    out = []
    for n in range(count):
        base = n * 8
        if base + 6 >= len(rom):
            break
        start = (rom[base+0] << 16) | (rom[base+1] << 8) | rom[base+2]
        start &= 0x3ffff
        stop = (rom[base+3] << 16) | (rom[base+4] << 8) | rom[base+5]
        stop &= 0x3ffff
        valid = 0 < start < stop
        out.append((n, start, stop, valid))
    return out

def converter_table(rom):
    """Converter decode_pcm_samples: offset starts at 8, step 8, idx=off//8-1.
    start=int.from_bytes(record[:4],'big')>>8 ; end=int.from_bytes(record[3:7],'big')>>8"""
    out = []
    for offset in range(8, min(0x400, len(rom)) - 7, 8):
        record = rom[offset:offset+8]
        if record == b"\xff"*8:
            break
        idx = offset // 8 - 1
        start = int.from_bytes(record[:4], "big") >> 8
        end = int.from_bytes(record[3:7], "big") >> 8
        valid = 0 < start and end > start and end <= len(rom)
        out.append((idx, start, end, valid))
    return out

print("\n=== MAME interpretation (entry N at N*8) ===")
mt = mame_table(rom)
valid_m = [t for t in mt if t[3]]
print(f"valid samples: {len(valid_m)}")
for n, s, e, v in mt[:12]:
    print(f"  sample {n:3d}: start=0x{s:x} stop=0x{e:x} len={e-s} valid={v}")
print("  ...")

print("\n=== Converter interpretation (offset 8, step 8) ===")
ct = converter_table(rom)
valid_c = [t for t in ct if t[3]]
print(f"valid samples: {len(valid_c)}")
for n, s, e, v in ct[:12]:
    print(f"  sample {n:3d}: start=0x{s:x} stop=0x{e:x} len={e-s} valid={v}")

# Compare: does MAME's sample N == converter's sample N?
print("\n=== Direct comparison (first 16) ===")
cm = {t[0]: t for t in ct}
mm = {t[0]: t for t in mt}
for n in range(16):
    mc = cm.get(n)
    mm_ = mm.get(n)
    same = mc is not None and mm_ is not None and mc[1]==mm_[1] and mc[2]==mm_[2]
    print(f"  sample {n:3d}: mame={(mm_[1],mm_[2]) if mm_ else None}  conv={(mc[1],mc[2]) if mc else None}  {'SAME' if same else 'DIFF'}")
