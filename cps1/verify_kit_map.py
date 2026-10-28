# -*- coding: utf-8 -*-
"""Verify SF2 kit: note -> sample name -> bytes, compare with WAV files."""
import struct, os, sys

SF2 = r"d:\works\cps-music-convertor\cps1\voices\sf2ce.sf2"
WAVDIR = r"d:\works\cps-music-convertor\cps1\wav\sf2ce"

with open(SF2, "rb") as f:
    d = f.read()

def find_list(name):
    off = 12
    while off + 8 <= len(d):
        tag = d[off:off+4]
        sz = struct.unpack("<I", d[off+4:off+8])[0]
        if tag == b"LIST" and d[off+8:off+12] == name:
            return off+12, off+8+sz
        off += 8 + sz + (sz & 1)
    raise KeyError(name)

def subtables(name):
    o, e = find_list(name)
    t = {}
    c = o
    while c + 8 <= e:
        tag = d[c:c+4].decode()
        sz = struct.unpack("<I", d[c+4:c+8])[0]
        t[tag] = d[c+8:c+8+sz]
        c += 8 + sz + (sz & 1)
    return t

pdta = subtables(b"pdta")
inst = pdta["inst"]
ibag = pdta["ibag"]
igen = pdta["igen"]
shdr = pdta["shdr"]
smpl = subtables(b"sdta")["smpl"]

# SF2 spec: inst = name(20)+nZones(2) = 22 bytes
INST_SZ = 22
assert len(inst) % INST_SZ == 0, f"inst len={len(inst)} not mult of {INST_SZ}"
inst_count = len(inst) // INST_SZ  # includes terminator
print(f"[debug] inst={len(inst)} ibag={len(ibag)} igen={len(igen)} shdr={len(shdr)} smpl={len(smpl)}")
# ibag/igen = 4-byte records (genndx, modndx)
IBAG_SZ = 4
assert len(ibag) % IBAG_SZ == 0
igen_count = len(igen) // 4
kit_terminator = struct.unpack("<H", inst[(inst_count-1)*INST_SZ+20 : (inst_count-1)*INST_SZ+22])[0]
kit_own = struct.unpack("<H", inst[(inst_count-2)*INST_SZ+20 : (inst_count-2)*INST_SZ+22])[0]
# fluidsynth allocates kit zones = terminator - kit_own (delta scheme)
kit_nzones = kit_terminator - kit_own
kit_first_zone = kit_own  # 127 FM insts x 1 zone = kit starts at ibag zone 127
print(f"inst records={inst_count-1} (term={inst_count}), kit inst idx={inst_count-2}, kit nZones={kit_nzones}, kit zones=[{kit_first_zone},{kit_first_zone+kit_nzones})")

def read_gens(gstart, gend):
    g = {}
    for j in range(gstart, gend):
        b = igen[j*4:j*4+4]
        genid = b[0] | (b[1] << 8)
        g[genid] = b[2:4]
    return g

pcm_notes = sorted(int(p.stem) for p in __import__("pathlib").Path(WAVDIR).glob("*.wav"))
print(f"PCM notes in WAV dir: {len(pcm_notes)}  ({min(pcm_notes)}..{max(pcm_notes)})")

print(f"\nnote | sample_name        | ms    | WAV match")
bad = 0
for zi in range(kit_first_zone, kit_first_zone + kit_nzones):
    gstart = struct.unpack("<H", ibag[zi*4:zi*4+2])[0]
    gend = struct.unpack("<H", ibag[(zi+1)*4:(zi+1)*4+2])[0]
    gens = read_gens(gstart, gend)
    if 46 not in gens or 53 not in gens:
        print(f"zone {zi}: no keynum/sampleid  gens={gens}")
        bad += 1
        continue
    key = struct.unpack("<H", gens[46])[0]
    sid = struct.unpack("<H", gens[53])[0]
    s = shdr[sid*46:sid*46+46]
    name = s[:20].decode("ascii", "ignore").rstrip("\x00")
    start, end = struct.unpack("<II", s[20:28])
    rate = struct.unpack("<I", s[36:40])[0]
    ms = (end - start) / rate * 1000
    wav_path = os.path.join(WAVDIR, f"{key:03d}.wav")
    match = "?"
    if os.path.exists(wav_path):
        with open(wav_path, "rb") as wf:
            wb = wf.read()
        # skip WAV header (44 bytes standard; use last N bytes to be safe)
        match = "OK" if wb[-(end-start)*2:] == smpl[start*2:end*2] else "MISMATCH"
    else:
        match = "NO WAV"
    flag = "" if match == "OK" else "   <<< PROBLEM"
    if match != "OK":
        bad += 1
    print(f"{key:4d} | {name:17s} | {ms:5.0f} | {match}{flag}")

print(f"\nproblems: {bad}")
