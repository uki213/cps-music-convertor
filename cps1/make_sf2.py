#!/usr/bin/env python3
"""Generate a SoundFont 2 (.sf2) for CPS-1 MIDI conversions.

Two voice sets:

* **FM voices** (programs 0-126, user bank 0/1): each instrument from
  ``voices/<archive>.opm`` is rendered at C4 (MIDI 60) with a YM2151
  synthesis model (MAME-compatible rate clock, sine table, algorithm/
  feedback interconnection).  The rendered waveform becomes an SF2 sample
  rooted at C4; every MIDI key retriggers it pitch-scaled.

* **PCM voices** (GM percussion, bank 0/0, preset 8): every
  ``wav/<archive>/NNN.wav`` (NNN = the MIDI note the decoder emits) is
  registered as a single-key region so drum-channel notes select the
  matching sample directly.

Only the Python standard library is used; the SF2 binary is written
directly with ``struct`` following the Creative Labs SoundFont 2.01 spec
(top-level order sfbk -> INFO -> sdta{smpl} -> pdta{phdr,pmod,pbag,pgen,
inst,ibag,imod,igen,shdr}; there is no hdrl chunk).
"""

from __future__ import annotations

import argparse
import array
import math
import struct
import wave
from pathlib import Path

import convert_to_midi as conv

FM_SAMPLE_RATE = 44_100
FM_DURATION = 1.5  # seconds of the C4 reference rendering
SINE_TABLE = [math.sin(2.0 * math.pi * i / 512.0) for i in range(512)]

# OPL/YM2151 rate time constants (MAME/OPL3 documentation), in milliseconds.
AR_TIMES_MS = (
    3.090, 6.180, 12.361, 24.721, 49.442, 98.883, 197.767, 395.533,
    791.067, 1582.133, 3164.267, 6328.533, 12657.066, 25314.133,
    50628.266, 101256.533,
)

# YM2151 algorithm trees: (modulator, carrier) pairs, index = algorithm.
ALGORITHM_TREES = (
    ((0, 3),),
    ((0, 3), (1, 3)),
    ((0, 3), (1, 2), (2, 3)),
    ((0, 3), (1, 3), (2, 3)),
    ((0, 3), (1, 2), (2, 3)),
    ((0, 3), (1, 3), (2, 1), (3, 2)),
    ((0, 1), (1, 3), (2, 3)),
    ((0, 1), (1, 3), (2, 3)),
)

C4_FREQUENCY = 261.625626


# ---------------------------------------------------------------------------
# OPM file parsing
# ---------------------------------------------------------------------------

def parse_opm(path: Path) -> list[dict]:
    """Parse an OPM instrument file into a list of instrument dicts."""
    programs: list[dict] = []
    current: dict | None = None
    op_labels = ("M1", "M2", "C1", "C2")

    for raw_line in path.read_text(encoding="ascii").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("@:"):
            parts = line.split()
            number = int(parts[0][2:])
            name = " ".join(parts[2:]) or f"CPS1 {number:03d}"
            current = {
                "number": number,
                "name": name,
                "feedback": 0,
                "algorithm": 0,
                # AR DR SR RL SL TL KS DT DT2 AM
                "operators": [[15, 0, 15, 15, 0, 0, 0, 0, 0, 0] for _ in range(4)],
            }
            programs.append(current)
        elif current is not None and line.startswith("CH:"):
            parts = line.split()[1:]
            current["feedback"] = int(parts[1])
            current["algorithm"] = int(parts[2])
        elif current is not None:
            tag, sep, rest = line.partition(":")
            if sep and tag.strip() in op_labels:
                values = rest.split()
                if len(values) >= 10:
                    current["operators"][op_labels.index(tag.strip())] = (
                        [int(v) for v in values[:10]]
                    )
    return programs


# ---------------------------------------------------------------------------
# YM2151 synthesis (C4 reference rendering)
# ---------------------------------------------------------------------------

def _operator_gain(
    t: float,
    ar: int, dr: int, sr: int, sl: int,
    tl: int, dt2: int,
) -> float:
    """Return linear gain (0.0-1.0) for one YM2151 operator at time t.

    YM2151 rate encoding: 0 = no rate (instantaneous), 1-15 = rate
    (higher rate = faster = shorter time).  The AR_TIMES_MS table is
    indexed low-to-high time, so the rate index must be inverted:
    rate 15 (fastest) → AR_TIMES_MS[0] (shortest time), etc.

    Uses the OPL/YM2151 7-bit attenuation model:
      0   = 0 dB attenuation (full signal)
      127 = 150 dB attenuation (silence)
      1 unit = 150/127 dB ≈ 1.181 dB
    """
    ar = min(15, max(0, ar))
    dr = min(15, max(0, dr))
    sr = min(15, max(0, sr))
    sl = sl & 0x0F          # 4-bit sustain level (0=full, 15=silence)

    # Static attenuation: TL (7-bit) + DT2 (2-bit, 1 step = 2 dB = 16 units)
    static_att = (tl & 0x7F) + (dt2 & 0x03) * 16

    # Sustain level attenuation in 0-127 units.
    # SL=0 → 0 units (full), SL=15 → 120 units (near silence).
    sl_att = float(sl * 8)

    # Attack phase duration (0 if ar==0, meaning instantaneous).
    if ar == 0:
        t_ar = 0.0
    else:
        t_ar = AR_TIMES_MS[15 - ar] / 1000.0  # inverted: rate 15 → index 0

    if t < t_ar:
        # In attack phase: linear ramp from silence (127) to full (0).
        eg_att = 127.0 * (1.0 - t / t_ar) if t_ar > 0 else 0.0
    else:
        # After attack: decay → sustain.
        t_d = t - t_ar
        if dr == 0:
            eg_att = sl_att  # instantaneous decay to sustain level
        else:
            t_dr = AR_TIMES_MS[15 - dr] / 1000.0
            if t_d < t_dr:
                # Exponential approach from 0 toward sl_att.
                eg_att = sl_att * (1.0 - math.exp(-t_d / t_dr))
            else:
                if sr == 0:
                    eg_att = sl_att  # hold at sustain level
                else:
                    t_sr = AR_TIMES_MS[15 - sr] / 1000.0
                    t_s = t_d - t_dr
                    # Exponential decay from sl_att toward silence (127).
                    eg_att = 127.0 - (127.0 - sl_att) * math.exp(-t_s / t_sr)

    total_att = min(127.0, eg_att + static_att)
    if total_att >= 127.0:
        return 0.0
    # 127 units = 150 dB silence; gain = 10^(-att_dB / 20)
    att_db = total_att * 150.0 / 127.0
    return 10.0 ** (-att_db / 20.0)


def render_fm(instrument: dict, duration: float, sample_rate: int) -> array.array:
    """Render one YM2151 instrument at C4.  Returns signed 16-bit mono."""
    operators = instrument["operators"]
    feedback = instrument["feedback"]
    tree = ALGORITHM_TREES[instrument["algorithm"] & 0x07]

    n_samples = max(1, int(duration * sample_rate))
    dt = 1.0 / sample_rate
    phase_inc = C4_FREQUENCY * dt  # normalized cycles per sample

    # DT multiplier: 2^(DT/2) for DT = 0-15.
    dt_multipliers = [2.0 ** ((op[7] & 0x0F) / 2.0) for op in operators]

    # Pre-extract per-operator static params (index 5=TL, 8=DT2).
    static_params = [
        (op[5] & 0x7F, op[8] & 0x03) for op in operators  # (tl, dt2)
    ]

    tree_fb = list(tree)
    fb_coeff = (feedback / 7.0) if feedback else 0.0

    phases = [0.0] * 4
    out = array.array("h")
    out_append = out.append
    table = SINE_TABLE

    for i in range(n_samples):
        t = i * dt
        gains = [
            _operator_gain(t, operators[op][0], operators[op][1],
                           operators[op][2], operators[op][4],
                           *static_params[op])
            for op in range(4)
        ]

        # Modulation inputs.
        mod = [0.0] * 4
        if fb_coeff:
            mod[0] += table[int((phases[0] % 1.0) * 512) & 511] * fb_coeff
        for src, dst in tree_fb:
            if gains[src] == 0.0:
                continue
            mod[dst] += table[int((phases[src] % 1.0) * 512) & 511] * gains[src]

        # Advance phases (modulation shifts phase by up to 1/8 cycle).
        for op in range(4):
            phases[op] += phase_inc * dt_multipliers[op] * (1.0 + mod[op] * 0.125)

        # Carrier output.
        value = table[int((phases[3] % 1.0) * 512) & 511] * gains[3]
        value = max(-1.0, min(1.0, value))
        out_append(int(value * 30000))

    # Normalise to near full scale.
    peak = 0
    for v in out:
        a = -v if v < 0 else v
        if a > peak:
            peak = a
    if peak > 0:
        scale = 30000.0 / peak
        for i in range(len(out)):
            out[i] = int(out[i] * scale)
    return out


# ---------------------------------------------------------------------------
# PCM sample loading
# ---------------------------------------------------------------------------

def load_pcm_wavs(wav_dir: Path) -> list[dict]:
    """Load NNN.wav files (NNN = the MIDI note for that sample)."""
    results = []
    if not wav_dir.is_dir():
        return results
    for path in sorted(wav_dir.glob("*.wav")):
        try:
            note = int(path.stem)
        except ValueError:
            continue
        if not 0 <= note <= 127:
            continue
        with wave.open(str(path), "rb") as wav_file:
            if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
                continue
            rate = wav_file.getframerate()
            data = wav_file.readframes(wav_file.getnframes())
        samples = array.array("h")
        samples.frombytes(data)
        results.append({"note": note, "data": samples, "rate": rate})
    return results


# ---------------------------------------------------------------------------
# SF2 binary writer (SoundFont 2.01 spec, standard chunk tags)
# ---------------------------------------------------------------------------

GEN_INSTRUMENT = 41
GEN_KEY_RANGE = 43
GEN_KEYNUM = 46
GEN_SAMPLE_ID = 53
GEN_OVERRIDING_ROOT_KEY = 58


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def write_sf2(
    path: Path,
    fm_samples: list[dict],      # {name, data: array of h, rate, root_key}
    fm_names: list[str],         # preset names per FM program
    pcm_samples: list[dict],     # {note, data, rate}
    archive: str,
) -> None:
    fm_count = len(fm_samples)
    pcm_count = len(pcm_samples)
    preset_count = fm_count + (1 if pcm_count else 0)
    instrument_count = fm_count + (1 if pcm_count else 0)
    sample_count = fm_count + pcm_count
    # FM: one preset zone + one instrument zone each.
    # PCM kit: one preset zone; one instrument zone per sample note.
    preset_zone_count = preset_count
    instrument_zone_count = fm_count + pcm_count
    # Generators per instrument zone: keyRange, overridingRootKey, sampleID.
    igen_count = instrument_zone_count * 3

    # ---- smpl: sample data, each sample 4-byte aligned (2 bytes per sample)
    smpl = bytearray()
    sample_offsets = []  # offsets in samples
    for sample in list(fm_samples) + list(pcm_samples):
        length = len(sample["data"])
        sample_offsets.append(len(smpl) // 2)
        smpl.extend(sample["data"].tobytes())
        # Pad to 4-byte alignment.
        if length % 2:
            smpl.append(0)
        pad = (4 - len(smpl) % 4) % 4
        smpl.extend(b"\x00" * pad)

    # ---- shdr ----
    shdr = b""
    for idx, sample in enumerate(list(fm_samples) + list(pcm_samples)):
        end = len(sample["data"])
        root = sample.get("root_key", sample.get("note", 60))
        # SHDR record is 46 bytes (fluidsynth SF_SHDR_SIZE = 46).
        # Layout per spec: name(20) + start(4) + end(4) + loopstart(4)
        # + loopend(4) + samplerate(4) + origpitch(1) + pitchfraction(1)
        # + samplelink(2) + sampletype(2) = 20+4*5+2+4 = 46 bytes.
        shdr += struct.pack(
            "<20sIIIIIBbhH",
            sample["name"][:20].encode("ascii").ljust(20, b"\x00"),
            sample_offsets[idx],        # start (samples)
            sample_offsets[idx] + end,  # end (samples)
            sample_offsets[idx],        # loopstart
            sample_offsets[idx] + end,  # loopend
            sample["rate"],             # samplerate
            root & 0x7F,                # origpitch (MIDI note)
            0,                          # pitchfraction (cents/10)
            0,                          # samplelink
            0x01,                       # sampletype: 0x01 = mono
        )

    # ---- phdr ----
    phdr = b""
    for idx, name in enumerate(fm_names[:fm_count]):
        phdr += struct.pack(
            "<20sHHHIii",
            name[:20].encode("ascii").ljust(20, b"\x00"),
            idx,       # preset number = program (0-126)
            0,         # bank 0 (GM MSB=0; the MIDI's CC32=1 is a sub-bank LSB)
            idx,       # preset bag index (cumulative zone offset, 0-based)
            0, 0, 0,   # library / genre / morph
        )
    if pcm_count:
        phdr += struct.pack(
            "<20sHHHIii",
            b"CPS1 PCM".ljust(20, b"\x00"),
            7,         # program 7 (0-based) = GM preset 8 percussion
            128,       # bank 128: GM percussion bank (MIDI bank-select MSB default)
            fm_count,  # preset bag index = fm_count (cumulative zone offset)
            0, 0, 0,
        )
    # Mandatory phdr terminator (last record); pbag_idx = total preset zones.
    phdr += struct.pack(
        "<20sHHHIii",
        b"EOP".ljust(20, b"\x00"),
        255,
        255,
        preset_zone_count,
        0, 0, 0,
    )

    # ---- pmod / pbag ----
    # No preset modulators (pmod is empty), so every pbag entry's modndx
    # must be 0.  genndx is cumulative: each zone owns exactly one pgen
    # entry (GEN_INSTRUMENT), so zone i's genndx = i+1.
    pmod = b""
    pbag = b""
    for idx in range(preset_zone_count):
        pbag += struct.pack("<HH", idx + 1, 0)  # genndx, modndx
    # Terminator: genndx = total pgen entries (= preset_zone_count), modndx=0.
    pbag += struct.pack("<HH", preset_zone_count, 0)
    # Each preset zone gets one generator: instrument = its index.
    pgen = b""
    for idx in range(preset_zone_count):
        pgen += struct.pack("<Hh", GEN_INSTRUMENT, idx)

    # ---- inst ----
    # fluidsynth's load_ihdr (fluid_sffile.c) allocates zones to each
    # instrument from deltas:
    #   inst[0]     : if nZones > 0, warns "N zones not referenced,
    #                 discarding" (no zones allocated to it from its own
    #                 field).  So inst[0].nZones must be 0.
    #   inst[i] (i>=1): zones allocated = inst[i].nZones - inst[i-1].nZones
    #   terminator  : zones allocated to last real inst =
    #                 terminator.nZones - last_inst.nZones
    # For fm_count FM instruments (1 zone each) + PCM kit (pcm_count zones):
    #   inst[i].nZones (i=0..fm_count-1) = i      -> each FM inst gets 1 zone
    #   inst[fm_count].nZones (kit)      = fm_count
    #   terminator.nZones                = fm_count + pcm_count (= total)
    # This yields: FM insts get 1 zone each, kit gets pcm_count zones.
    inst = b""
    for idx in range(instrument_count):
        name = (
            fm_names[idx][:20] if idx < fm_count else "CPS1 PCM KIT"
        )
        nz = idx
        inst += struct.pack("<20sH", name.encode("ascii").ljust(20, b"\x00"), nz)
    # Terminator: nZones = total instrument zones.
    inst += struct.pack("<20sH", b"EOP".ljust(20, b"\x00"), instrument_zone_count)

    # ---- ibag / imod / igen ----
    ibag = b""
    for idx in range(instrument_zone_count):
        ibag += struct.pack("<HH", idx * 3, 0)
    ibag += struct.pack("<HH", instrument_zone_count * 3, 0)  # terminator: genndx = total generators

    # No instrument modulators: leave imod empty (fluidsynth allows size==0).
    imod = b""

    igen = b""
    sample_index = 0
    for idx in range(fm_count):
        igen += struct.pack("<Hh", GEN_KEY_RANGE, (127 << 8) | 0)  # hi=127, lo=0
        igen += struct.pack("<Hh", GEN_OVERRIDING_ROOT_KEY, 60)
        igen += struct.pack("<Hh", GEN_SAMPLE_ID, sample_index)
        sample_index += 1
    for sample in pcm_samples:
        note = sample["note"]
        igen += struct.pack("<Hh", GEN_KEYNUM, note)
        igen += struct.pack("<Hh", GEN_OVERRIDING_ROOT_KEY, note)
        igen += struct.pack("<Hh", GEN_SAMPLE_ID, sample_index)
        sample_index += 1

    # ---- INFO ---- (standard RIFF INFO sub-chunks; see SGM-V2.01.sf2 reference)
    # RIFF chunks must be word-aligned: if a payload has odd length, append a
    # pad byte (the size field still holds the true payload length).
    def _info(tag: bytes, payload: bytes) -> bytes:
        data = tag + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            data += b"\x00"
        return data

    ifil = struct.pack("<HH", 2, 1)  # SoundFont 2.01
    def _info_str(tag: bytes, text: str) -> bytes:
        # NUL-terminate, then pad to even length (RIFF word-alignment;
        # fluidsynth strictly rejects odd-sized INFO sub-chunks).
        data = text.encode("ascii") + b"\x00"
        if len(data) % 2:
            data += b"\x00"
        return _info(tag, data)

    info_body = (
        _info(b"ifil", ifil)
        + _info_str(b"INAM", archive[:30])
        + _info_str(b"isng", "CPS1")
    )

    # ---- assemble ----
    # Top-level order per spec: sfbk -> LIST INFO -> LIST sdta -> LIST pdta.
    # (There is NO hdrl chunk; the SoundFont 2.01 spec has none.)
    out = bytearray()
    out += b"RIFF" + struct.pack("<I", 0) + b"sfbk"
    out += b"LIST" + struct.pack("<I", len(info_body) + 4) + b"INFO" + info_body

    # Sample data LIST chunk.
    sdta_body = b"smpl" + struct.pack("<I", len(smpl)) + bytes(smpl)
    out += b"LIST" + struct.pack("<I", len(sdta_body) + 4) + b"sdta" + sdta_body

    # Preset data LIST chunk.
    # SGM-V2.01.sf2 (a known-good reference) orders pbag before pmod;
    # fluidsynth follows the same order, so match it.
    pdta_body = (
        b"phdr" + struct.pack("<I", len(phdr)) + phdr
        + b"pbag" + struct.pack("<I", len(pbag)) + pbag
        + b"pmod" + struct.pack("<I", len(pmod)) + pmod
        + b"pgen" + struct.pack("<I", len(pgen)) + pgen
        + b"inst" + struct.pack("<I", len(inst)) + inst
        + b"ibag" + struct.pack("<I", len(ibag)) + ibag
        + b"imod" + struct.pack("<I", len(imod)) + imod
        + b"igen" + struct.pack("<I", len(igen)) + igen
        + b"shdr" + struct.pack("<I", len(shdr)) + shdr
    )
    out += b"LIST" + struct.pack("<I", len(pdta_body) + 4) + b"pdta" + pdta_body

    out[4:8] = struct.pack("<I", len(out) - 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_xml", type=Path)
    parser.add_argument("--opm", type=Path, help="OPM instrument file (default: voices/<archive>.opm)")
    parser.add_argument("--wav-dir", type=Path, help="PCM WAV directory (default: wav/<archive>)")
    parser.add_argument("--output", type=Path, help="output .sf2 path (default: voices/<archive>.sf2)")
    parser.add_argument("--duration", type=float, default=FM_DURATION, help="FM render length in seconds")
    args = parser.parse_args()

    game = conv.read_game(args.game_xml)
    opm_path = args.opm or conv.BASE_DIR / "voices" / f"{game.archive}.opm"
    if not opm_path.is_file():
        raise SystemExit(f"OPM instrument file not found: {opm_path}")
    wav_dir = args.wav_dir or conv.WAV_DIR / game.archive
    out_path = args.output or conv.BASE_DIR / "voices" / f"{game.archive}.sf2"

    programs = parse_opm(opm_path)
    print(f"Parsed {len(programs)} instruments from {opm_path}")

    fm_samples: list[dict] = []
    fm_names: list[str] = []
    print(f"Rendering FM samples at C4 ({args.duration:.1f}s @ {FM_SAMPLE_RATE} Hz)...")
    for idx, program in enumerate(programs):
        name = program["name"][:20]
        # Skip fully silent programs (TL=0 and SL=0 everywhere is legal data,
        # so only skip when every operator has zero total level AND zero SL).
        if all(op[5] == 0 and op[4] == 0 for op in program["operators"]):
            fm_names.append(name)
            fm_samples.append(
                {"name": name[:16], "data": array.array("h", [0] * 4),
                 "rate": FM_SAMPLE_RATE, "root_key": 60}
            )
            continue
        data = render_fm(program, args.duration, FM_SAMPLE_RATE)
        fm_samples.append(
            {"name": name[:16], "data": data,
             "rate": FM_SAMPLE_RATE, "root_key": 60}
        )
        fm_names.append(name)
        if (idx + 1) % 16 == 0 or idx + 1 == len(programs):
            print(f"  {idx + 1}/{len(programs)}")

    pcm_samples = load_pcm_wavs(wav_dir)
    for sample in pcm_samples:
        sample["name"] = f"PCM{sample['note']:03d}"
    print(f"Loaded {len(pcm_samples)} PCM samples from {wav_dir}")

    print(f"Writing {out_path}...")
    write_sf2(out_path, fm_samples, fm_names, pcm_samples, game.archive)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(
        f"Done: {size_mb:.1f} MB, "
        f"{len(fm_samples)} FM voices + {len(pcm_samples)} PCM voices"
    )


if __name__ == "__main__":
    main()
