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
(standard chunk tags: hdrl / INFO / pdta{phdr,pmod,pbag,inst,ibag,imod,
igen,shdr,smpl}).
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

    Uses the OPL/YM2151 7-bit attenuation model:
      0   = 0 dB attenuation (full signal)
      127 = 150 dB attenuation (silence)
      1 unit = 150/127 dB ≈ 1.181 dB
    """
    ar = min(15, ar)
    dr = min(15, dr)
    sr = min(15, sr)
    sl = sl & 0x0F          # 4-bit sustain level (0-15)

    # Static attenuation: TL (7-bit) + DT2 (2-bit, 1 step = 2 dB = 16 units)
    static_att = (tl & 0x7F) + (dt2 & 0x03) * 16

    # Envelope attenuation in 0-127 units.
    if ar == 0:
        eg_att = 127.0
    else:
        t_ar = AR_TIMES_MS[ar] / 1000.0
        if t < t_ar:
            # Linear attack from silence (127) to full (0).
            eg_att = 127.0 * (1.0 - t / t_ar)
        else:
            # Sustain level in 0-127 units (4-bit SL, 1 step = 150/16 dB).
            sl_units = float(sl * 8)  # sl=15 -> 120, close to max
            if dr == 0:
                eg_att = sl_units
            else:
                # DR time = 16 x AR time (OPL standard).
                t_dr = AR_TIMES_MS[dr] / 1000.0 * 16.0
                t_d = t - t_ar
                # Exponential approach to sustain level.
                eg_att = sl_units * (1.0 - math.exp(-t_d / t_dr))
                # Sustain phase: exponential decay from SL to silence.
                if sr > 0 and t_d > 3.0 * t_dr:
                    # SR time = 16 x DR time = 256 x AR time.
                    t_sr = AR_TIMES_MS[sr] / 1000.0 * 256.0
                    t_s = t_d - 3.0 * t_dr
                    eg_att = 127.0 - (127.0 - sl_units) * math.exp(-t_s / t_sr)

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
GEN_OVERRIDING_ROOT_KEY = 59


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
        shdr += struct.pack(
            "<20sIIIIIBbhH",
            sample["name"][:20].encode("ascii").ljust(20, b"\x00"),
            sample_offsets[idx],
            sample_offsets[idx] + end,
            sample_offsets[idx],
            sample_offsets[idx] + end,
            sample["rate"],
            root & 0x7F,
            0,
            0,
            0x8000,  # monaural
        )

    # ---- phdr ----
    phdr = b""
    for idx, name in enumerate(fm_names[:fm_count]):
        phdr += struct.pack(
            "<20sHHHIii",
            name[:20].encode("ascii").ljust(20, b"\x00"),
            idx,       # preset number = program (bank select 0/1 in the MIDI)
            1,         # bank LSB (MSB 0 comes from the MIDI stream)
            idx,       # preset bag index
            0, 0, 0,   # library / genre / morph
        )
    if pcm_count:
        phdr += struct.pack(
            "<20sHHHIii",
            b"CPS1 PCM".ljust(20, b"\x00"),
            7,         # preset 8 (GM percussion), bank 0/0
            0,
            fm_count,  # preset bag index
            0, 0, 0,
        )

    # ---- pmod / pbag ----
    pmod = b"\x00" * (10 * preset_zone_count)
    pbag = b""
    for idx in range(preset_zone_count):
        pbag += struct.pack("<HH", idx * 1, idx * 1)  # gen index, mod index
    # Each preset zone gets one generator: instrument = its index.
    pgen = b""
    for idx in range(preset_zone_count):
        pgen += struct.pack("<Hh", GEN_INSTRUMENT, idx)

    # ---- inst ----
    inst = b""
    for idx in range(instrument_count):
        name = (
            fm_names[idx][:20] if idx < fm_count else "CPS1 PCM KIT"
        )
        inst += struct.pack("<20sH", name.encode("ascii").ljust(20, b"\x00"), idx)

    # ---- ibag / imod / igen ----
    ibag = b""
    for idx in range(instrument_zone_count):
        ibag += struct.pack("<HH", idx * 3, 0)

    imod = b"\x00" * (10 * instrument_zone_count)

    igen = b""
    sample_index = 0
    for idx in range(fm_count):
        igen += struct.pack("<Hh", GEN_KEY_RANGE, (127 << 4) | 0)
        igen += struct.pack("<Hh", GEN_OVERRIDING_ROOT_KEY, 60)
        igen += struct.pack("<Hh", GEN_SAMPLE_ID, sample_index)
        sample_index += 1
    for sample in pcm_samples:
        note = sample["note"]
        igen += struct.pack("<Hh", GEN_KEYNUM, note)
        igen += struct.pack("<Hh", GEN_OVERRIDING_ROOT_KEY, note)
        igen += struct.pack("<Hh", GEN_SAMPLE_ID, sample_index)
        sample_index += 1
    igen += struct.pack("<Hh", 0, 0)  # terminator

    # ---- hdrl (24 bytes = 12 uint16) ----
    hdrl = struct.pack("<12H",
        preset_count, instrument_count, 0, 0,
        0, 0, 0,
        0x0002, 0x0001, 0, 2025, 0,
    )

    # ---- INFO ----
    nm04 = archive[:4].encode("ascii").ljust(4, b"\x00").upper()
    isng = b"CPS1"
    info_body = (
        b"nm04" + struct.pack("<I", 4) + nm04
        + b"isng" + struct.pack("<I", 4) + isng
    )

    # ---- assemble ----
    out = bytearray()
    out += b"RIFF" + struct.pack("<I", 0) + b"sfbk"
    out += b"hdrl" + struct.pack("<I", 24) + hdrl
    out += b"LIST" + struct.pack("<I", len(info_body) + 4) + b"INFO" + info_body

    # Sample data LIST chunk.
    sdta_body = b"smpl" + struct.pack("<I", len(smpl)) + bytes(smpl)
    out += b"LIST" + struct.pack("<I", len(sdta_body) + 4) + b"sdta" + sdta_body

    # Preset data LIST chunk.
    pdta_body = (
        b"phdr" + struct.pack("<I", len(phdr)) + phdr
        + b"pmod" + struct.pack("<I", len(pmod)) + pmod
        + b"pbag" + struct.pack("<I", len(pbag)) + pbag
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
