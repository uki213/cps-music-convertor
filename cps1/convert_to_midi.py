#!/usr/bin/env python3
"""Convert CPS-1 V4.25 music sequences described by a game XML file to MIDI."""

from __future__ import annotations

import argparse
import re
import struct
import wave
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ROM_DIR = BASE_DIR / "roms"
MIDI_DIR = BASE_DIR / "midi"
WAV_DIR = BASE_DIR / "wav"
PPQN = 48
NOTE_VELOCITY = 127
MAX_STEPS = 200_000
PCM_SAMPLE_RATE = 7576
PCM_NOTE_BASE = 24
CPS1_FM_INSTRUMENT_SIZE = 40
VM5_PROGRAM_COUNT = 127
VM5_ALGORITHM_MAP = (4, 3, 5, 6, 5, 7, 7, 2)
SMAF_USER_VOICE_BANK_MSB = 0
SMAF_USER_VOICE_BANK_LSB = 1
DELTA_TABLE = (
    (2, 4, 8, 16, 32, 64, 128),
    (3, 6, 12, 24, 48, 96, 192),
    (0, 9, 18, 36, 72, 144, 0),
)
OCTAVE_TABLE = (0, 12, 24, 36, 48, 60, 72, 84, 24, 36, 48, 60, 72, 84, 96, 108)


@dataclass(frozen=True)
class Game:
    archive: str
    tables_offset: int
    version: str
    roms: tuple[tuple[int, str], ...]
    pcm_roms: tuple[tuple[int, str], ...]
    titles: dict[int, str]


@dataclass
class Track:
    name: str
    channel: int
    events: list[tuple[int, int, bytes]] = field(default_factory=list)
    note_count: int = 0

    def add(self, tick: int, priority: int, data: bytes) -> None:
        self.events.append((tick, priority, data))


def read_game(path: Path) -> Game:
    root = ET.parse(path).getroot()
    if root.tag != "game":
        raise ValueError(f"expected a <game> root in {path}")
    driver = root.find("driver")
    if driver is None or driver.get("type", "").lower() != "cps1":
        raise ValueError(f"{path} does not describe a CPS-1 game")
    romlist = root.find("romlist")
    if romlist is None:
        raise ValueError(f"missing <romlist> in {path}")
    archive = romlist.get("archive", "").strip()
    if not archive or Path(archive).name != archive or archive in (".", ".."):
        raise ValueError(f"invalid ROM archive name in {path}")
    tables_text = (romlist.get("tables") or "").strip()
    tables_offset = int(tables_text, 0) if tables_text else 0
    version = (romlist.get("format-version") or "CPS1_V4.25").strip()
    if version not in ("CPS1_V4.25", "CPS1_V3.50", "CPS1_V2.00"):
        raise ValueError(
            f"unsupported CPS-1 format version {version!r}; expected "
            "'CPS1_V4.25', 'CPS1_V3.50' or 'CPS1_V2.00'"
        )
    
    # For now, we treat both versions the same way since they're likely to be
    # compatible at the core conversion level, but can be extended for specific differences

    roms: list[tuple[int, str]] = []
    pcm_roms: list[tuple[int, str]] = []
    for element in romlist.findall("rom"):
        rom_type = element.get("type")
        if rom_type not in ("code", "pcm"):
            continue
        filename = (element.text or "").strip()
        try:
            offset = int(element.get("offset", ""), 0)
        except ValueError as error:
            raise ValueError(f"invalid code ROM offset for {filename!r}") from error
        if not filename or Path(filename).name != filename or offset < 0:
            raise ValueError(f"invalid code ROM entry in {path}")
        if rom_type == "code":
            roms.append((offset, filename))
        else:
            pcm_roms.append((offset, filename))
    if not roms:
        raise ValueError(f"no <rom type=\"code\"> entries in {path}")
    if not pcm_roms:
        raise ValueError(f"no <rom type=\"pcm\"> entries in {path}")

    titles_element = root.find("titlelist")
    if titles_element is None:
        raise ValueError(f"missing <titlelist> in {path}")
    titles: dict[int, str] = {}
    for element in titles_element.findall("title"):
        code_text = element.get("code", "")
        try:
            code = int(code_text, 0)
        except ValueError as error:
            raise ValueError(f"invalid song code {code_text!r} in {path}") from error
        title = (element.text or "").strip()
        if not 0 <= code <= 0xFF or not title:
            raise ValueError(f"invalid or empty song title for code {code_text!r}")
        # Duplicate codes (e.g. a demo loop reusing the same BGM) are allowed;
        # the last entry in the XML wins.
        titles[code] = title
    if not titles:
        raise ValueError(f"no <title> entries in {path}")
    return Game(archive, tables_offset, version, tuple(roms), tuple(pcm_roms), titles)


def read_rom_file(archive: str, filename: str) -> bytes:
    """Read a single ROM, resolving it from the folder or a ZIP archive."""
    folder_path = ROM_DIR / archive / filename
    if folder_path.is_file():
        return folder_path.read_bytes()

    zip_path = ROM_DIR / f"{archive}.zip"
    if zip_path.is_file():
        candidates = (
            filename.replace("\\", "/"),
            f"{archive}/{filename}".replace("\\", "/"),
        )
        with zipfile.ZipFile(zip_path) as archive_file:
            names = set(archive_file.namelist())
            for name in candidates:
                if name in names:
                    return archive_file.read(name)

    raise FileNotFoundError(
        f"ROM not found: {folder_path} or {filename!r} in {zip_path}"
    )


def load_rom(game: Game) -> bytes:
    chunks: list[tuple[int, bytes, str]] = []
    for offset, filename in sorted(game.roms):
        chunks.append((offset, read_rom_file(game.archive, filename), filename))
    end = 0
    for offset, chunk, filename in chunks:
        if offset < end:
            raise ValueError(f"code ROM overlaps another ROM at 0x{offset:x}: {filename}")
        end = offset + len(chunk)
    image = bytearray(b"\xff" * end)
    for offset, chunk, _filename in chunks:
        image[offset : offset + len(chunk)] = chunk
    return bytes(image)


def load_pcm_rom(game: Game) -> bytes:
    chunks: list[tuple[int, bytes, str]] = []
    for offset, filename in sorted(game.pcm_roms):
        chunks.append((offset, read_rom_file(game.archive, filename), filename))
    end = 0
    for offset, chunk, filename in chunks:
        if offset < end:
            raise ValueError(f"PCM ROM overlaps another ROM at 0x{offset:x}: {filename}")
        end = offset + len(chunk)
    image = bytearray(b"\xff" * end)
    for offset, chunk, _filename in chunks:
        image[offset : offset + len(chunk)] = chunk
    return bytes(image)


def autodetect_tables_candidates(
    data: bytes, titles: dict[int, str], version: str, max_candidates: int = 10
) -> list[int]:
    """Locate candidate CPS-1 sound-program table offsets when the XML omits the offset.

    Returns up to *max_candidates* offsets sorted by score (best first).
    The caller should try each candidate until sequence decoding succeeds.
    """
    if not titles:
        raise ValueError("cannot auto-detect the CPS-1 tables offset without song titles")
    minimum_count = max(titles) + 1
    limit = min(len(data) - 8, 0x20000)
    scored: list[tuple[tuple[int, int, int, int], int]] = []
    for offset in range(0x400, limit, 1):
        if version in ("CPS1_V2.00", "CPS1_V3.50"):
            # V2.00 header: [count][FM table ptr]; V3.50: [count][masterVol][FM table ptr].
            sequence_table = offset + (3 if version == "CPS1_V2.00" else 4)
            record_size = 32 if version == "CPS1_V2.00" else CPS1_FM_INSTRUMENT_SIZE
            pointer_offset = 1 if version == "CPS1_V2.00" else 2
        else:
            sequence_table = offset + 6
            record_size = CPS1_FM_INSTRUMENT_SIZE
            pointer_offset = 2
        sequence_count = data[offset]
        if sequence_count > 256:
            continue
        # V2.00/V3.50 count bytes record used slots, not the highest code
        # (e.g. Final Fight: count 0xC3 but code 0xF0), so they may be
        # smaller than max(titles) + 1; V4.25 count equals 255 in practice.
        if version not in ("CPS1_V2.00", "CPS1_V3.50") and sequence_count < minimum_count:
            continue
        if sequence_table + sequence_count * 2 > len(data):
            continue
        if offset + pointer_offset + 2 > len(data):
            continue
        instrument_table = read_u16(data, offset + pointer_offset)
        if instrument_table + VM5_PROGRAM_COUNT * record_size > len(data):
            continue
        # The FM instrument table must point at real program data, not the
        # 0xFF/0x00 padding that surrounds the actual code content. A pointer
        # into padding still passes the bounds check above but would produce
        # garbage voices and, more importantly, usually sits at a fake tables
        # offset whose sequence table also decodes to spurious notes.
        if offset + pointer_offset + 2 <= len(data):
            opm_window = data[instrument_table : instrument_table + 40]
            if opm_window and len(set(opm_window)) == 1:
                continue
        resolved = 0
        strict = 0
        distinct = set()
        for code in titles:
            if sequence_table + code * 2 + 2 > len(data):
                resolved = -1
                break
            start = read_u16(data, sequence_table + code * 2)
            if start == 0 or start >= len(data) or data[start] & 0x80:
                continue
            resolved += 1
            distinct.add(start)
            if start + 3 > len(data):
                continue
            first_track = read_u16(data, start + 1)
            if 0 < first_track < len(data) and not (data[first_track] & 0x80):
                strict += 1
        if resolved < 0:
            continue
        # A genuine table has strict == resolved (every pointer is valid).
        # False positives always have strict < resolved.
        perfect = 1 if strict == resolved and strict > 0 else 0
        scored.append(((perfect * 100000 + strict, resolved, len(distinct), -offset), offset))
    if not scored:
        raise ValueError(
            "could not auto-detect the CPS-1 tables offset; "
            "set the 'tables' attribute on <romlist>"
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    minimum_strict = max(4, min(len(titles), 255) // 4)
    # Prefer offsets where every resolved pointer is valid (strict == resolved).
    perfect = [offset for key, offset in scored if key[0] >= 100000 and (key[0] % 100000) >= minimum_strict]
    candidates = perfect if perfect else [
        offset for key, offset in scored if key[0] >= minimum_strict
    ]
    if not candidates:
        best_key = scored[0][0]
        raise ValueError(
            "could not auto-detect a confident CPS-1 tables offset "
            f"(best strict={best_key[0] % 100000}, resolved={best_key[1]}); "
            "set the 'tables' attribute on <romlist>"
        )
    return candidates[:max_candidates]


def decode_msm6295(data: bytes) -> bytes:
    step_table = (
        16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66,
        73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253,
        279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
        1060, 1166, 1282, 1411, 1552,
    )
    index_shift = (-1, -1, -1, -1, 2, 4, 6, 8)
    signal = 0
    step_index = 0
    output = bytearray()
    for packed in data:
        for nibble in (packed >> 4, packed & 0x0F):
            step = step_table[step_index]
            difference = (
                (step if nibble & 0x04 else 0)
                + (step // 2 if nibble & 0x02 else 0)
                + (step // 4 if nibble & 0x01 else 0)
                + step // 8
            )
            signal += -difference if nibble & 0x08 else difference
            signal = max(-2048, min(2047, signal))
            step_index = max(0, min(48, step_index + index_shift[nibble & 0x07]))
            output.extend(struct.pack("<h", signal << 4))
    return bytes(output)


def decode_pcm_samples(pcm_rom: bytes) -> dict[int, bytes]:
    """Decode all MSM6295 PCM samples from the PCM ROM."""
    samples: dict[int, bytes] = {}
    sample_table_end = min(0x400, len(pcm_rom))
    for offset in range(8, sample_table_end - 7, 8):
        record = pcm_rom[offset : offset + 8]
        if record == b"\xff" * 8:
            break
        sample_index = offset // 8 - 1
        start = int.from_bytes(record[:4], "big") >> 8
        end = int.from_bytes(record[3:7], "big") >> 8
        if start == 0 or end <= start:
            continue
        if end > len(pcm_rom):
            raise ValueError(f"PCM sample {sample_index} ends outside the ROM: 0x{end:x}")
        samples[sample_index] = decode_msm6295(pcm_rom[start:end])
    return samples


def write_pcm_wavs(samples: dict[int, bytes], output_dir: Path) -> int:
    """Write decoded PCM samples as WAV files assigned MIDI notes by index."""
    if not samples:
        return 0
    notes_by_sample = {
        sample_index: PCM_NOTE_BASE + index
        for index, sample_index in enumerate(sorted(samples))
    }
    if max(notes_by_sample.values()) > 127:
        raise ValueError("too many PCM waveforms to assign each one a MIDI note")
    output_dir.mkdir(parents=True, exist_ok=True)
    for sample_index, pcm_data in samples.items():
        note = notes_by_sample[sample_index]
        with wave.open(str(output_dir / f"{note:03d}.wav"), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(PCM_SAMPLE_RATE)
            wav_file.writeframes(pcm_data)
    return len(samples)


def build_pcm_notes_by_program(
    code_rom: bytes, tables_offset: int, samples: dict[int, bytes], version: str
) -> dict[int, int]:
    """Map CPS-1 PCM instrument programs to MIDI notes using decoded samples."""
    notes_by_sample = {
        sample_index: PCM_NOTE_BASE + index
        for index, sample_index in enumerate(sorted(samples))
    }
    if version in ("CPS1_V2.00", "CPS1_V3.50"):
        # V2.00/V3.50 have no PCM instrument table: the mapping is identity
        # (program N selects sample N-1), per VGMTrans CPS1Instr.cpp.
        notes_by_program: dict[int, int] = {}
        for program in range(1, VM5_PROGRAM_COUNT + 1):
            sample_index = program - 1
            if sample_index in notes_by_sample:
                notes_by_program[program] = notes_by_sample[sample_index]
        return notes_by_program

    if tables_offset + 6 > len(code_rom):
        raise ValueError(f"CPS-1 tables header outside ROM at 0x{tables_offset:x}")
    instrument_table = read_u16(code_rom, tables_offset + 4)
    instrument_end = min(instrument_table + 128 * 4, len(code_rom))
    if instrument_table >= len(code_rom):
        raise ValueError(f"MSM6295 instrument table outside ROM at 0x{instrument_table:x}")
    notes_by_sample = {
        sample_index: PCM_NOTE_BASE + index
        for index, sample_index in enumerate(sorted(samples))
    }
    notes_by_program: dict[int, int] = {}
    for program in range(min(128, (instrument_end - instrument_table) // 4)):
        offset = instrument_table + program * 4
        if not code_rom[offset] & 0x80:
            break
        sample_index = code_rom[offset + 1] - 1
        if sample_index in notes_by_sample:
            notes_by_program[program] = notes_by_sample[sample_index]
    return notes_by_program


def convert_cps1_fm_instrument(instrument: bytes, program: int) -> bytes:
    if len(instrument) != CPS1_FM_INSTRUMENT_SIZE:
        raise ValueError(f"expected a {CPS1_FM_INSTRUMENT_SIZE}-byte CPS-1 FM instrument")

    algorithm = instrument[5] & 0x07
    vm5_algorithm = VM5_ALGORITHM_MAP[algorithm]
    feedback = instrument[5] >> 3 & 0x07
    lfo_enabled = bool(instrument[1] & 0x80)
    lfo_rate = round(instrument[2] * 3 / 255) if lfo_enabled else 0
    pitch_depth = round((instrument[6] >> 4) * 3 / 15) if lfo_enabled else 0
    amplitude_depth = instrument[6] & 0x03 if lfo_enabled else 0

    name = f"SF2CE {program:03d}".encode("ascii")[:16].ljust(16, b"\x00")
    result = bytearray(
        struct.pack(
            ">H16s6B",
            0,
            name,
            0,
            0,
            0,
            program,
            0,
            0,
        )
    )
    result.extend((0, 0x79, (lfo_rate << 6) | vm5_algorithm))

    for operator in range(4):
        dt_multiplier = instrument[20 + operator]
        key_attack = instrument[24 + operator]
        am_decay = instrument[28 + operator]
        dt2_sustain = instrument[32 + operator]
        sustain_release = instrument[36 + operator]

        attenuation = cps1_operator_attenuation(instrument, operator)
        total_level = min(63, round(attenuation / 2))
        key_scale = key_attack >> 6
        attack = (key_attack & 0x1F) >> 1
        decay = (am_decay & 0x1F) >> 1
        sustain_rate = (dt2_sustain & 0x1F) >> 1
        release = sustain_release & 0x0F
        sustain_level = sustain_release >> 4
        amplitude_enabled = lfo_enabled and bool(am_decay & 0x80)
        vibrato_enabled = lfo_enabled and pitch_depth > 0

        result.extend(
            (
                sustain_rate << 4,
                (release << 4) | decay,
                (attack << 4) | sustain_level,
                (total_level << 2) | key_scale,
                (amplitude_depth << 5)
                | (0x10 if amplitude_enabled else 0)
                | (pitch_depth << 1)
                | (1 if vibrato_enabled else 0),
                ((dt_multiplier & 0x0F) << 4) | (dt_multiplier >> 4 & 0x07),
                feedback if operator == 0 else 0,
            )
        )
    return bytes(result)


def cps1_operator_attenuation(instrument: bytes, operator: int) -> int:
    volume_offset = 8 + operator * 3
    volume = instrument[volume_offset + 2]
    packed_volume = ((volume << 8) | (volume >> 4)) & 0x0F07
    key_attenuation = packed_volume >> 8
    scaled_volume = ((packed_volume & 0xFF) * 2) & 0x0F
    return (
        max(0, 16 + key_attenuation - (15 * scaled_volume // 16))
        + instrument[volume_offset]
    )


def load_cps1_instruments(code_rom: bytes, tables_offset: int, version: str) -> list[bytes]:
    if version == "CPS1_V2.00":
        # V2.00: 3-byte header ([count][FM table ptr (2B BE)]), 32-byte records.
        if tables_offset + 3 > len(code_rom):
            raise ValueError(f"CPS-1 tables header outside ROM at 0x{tables_offset:x}")
        instrument_table = read_u16(code_rom, tables_offset + 1)
        record_size = 32
    elif version == "CPS1_V3.50":
        # V3.50: 4-byte header ([count][masterVol][FM table ptr (2B BE)]), 40-byte records.
        if tables_offset + 4 > len(code_rom):
            raise ValueError(f"CPS-1 tables header outside ROM at 0x{tables_offset:x}")
        instrument_table = read_u16(code_rom, tables_offset + 2)
        record_size = CPS1_FM_INSTRUMENT_SIZE
    else:
        if tables_offset + 6 > len(code_rom):
            raise ValueError(f"CPS-1 tables header outside ROM at 0x{tables_offset:x}")
        instrument_table = read_u16(code_rom, tables_offset + 2)
        record_size = CPS1_FM_INSTRUMENT_SIZE
    table_end = instrument_table + VM5_PROGRAM_COUNT * record_size
    if table_end > len(code_rom):
        raise ValueError("CPS-1 YM2151 instrument table extends past the code ROM")
    return [
        code_rom[
            instrument_table + program * record_size :
            instrument_table + (program + 1) * record_size
        ]
        for program in range(VM5_PROGRAM_COUNT)
    ]


def write_vm5_library(code_rom: bytes, tables_offset: int, output_path: Path, version: str) -> int:
    programs = bytearray()
    for program, instrument in enumerate(load_cps1_instruments(code_rom, tables_offset, version)):
        if len(instrument) < CPS1_FM_INSTRUMENT_SIZE:
            # V2.00 records are 32 bytes; the 40-byte V4.25 layout's trailing
            # fields (sustain/release) were not present, so zero-fill them.
            instrument = instrument + b"\x00" * (CPS1_FM_INSTRUMENT_SIZE - len(instrument))
        programs.extend(convert_cps1_fm_instrument(instrument, program))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"VOM5" + struct.pack(">I", len(programs)) + programs)
    return VM5_PROGRAM_COUNT


def convert_cps1_instrument_to_mucom(instrument: bytes, program: int, archive: str) -> str:
    if len(instrument) != CPS1_FM_INSTRUMENT_SIZE:
        raise ValueError(f"expected a {CPS1_FM_INSTRUMENT_SIZE}-byte CPS-1 FM instrument")

    feedback = instrument[5] >> 3 & 0x07
    algorithm = instrument[5] & 0x07
    archive_name = re.sub(r"[^A-Za-z0-9_-]", "_", archive.upper())[:8] or "CPS1"
    lines = [f"  @{program}:{{", f"   {feedback:2d}, {algorithm:2d}"]
    for operator in range(4):
        total_level = min(127, cps1_operator_attenuation(instrument, operator))

        dt_multiplier = instrument[20 + operator]
        key_attack = instrument[24 + operator]
        am_decay = instrument[28 + operator]
        dt_sustain = instrument[32 + operator]
        sustain_release = instrument[36 + operator]
        parameters = (
            key_attack & 0x1F,
            am_decay & 0x1F,
            dt_sustain & 0x1F,
            sustain_release & 0x0F,
            sustain_release >> 4,
            total_level,
            key_attack >> 6,
            dt_multiplier & 0x0F,
            dt_multiplier >> 4 & 0x07,
        )
        values = ", ".join(f"{value:2d}" for value in parameters)
        suffix = f',"{archive_name} {program:03d}"' if operator == 3 else ""
        lines.append(f"  {values}{suffix}")
    lines[-1] += "}"
    return "\n".join(lines)


def write_mucom_library(
    code_rom: bytes, tables_offset: int, archive: str, output_path: Path, version: str
) -> int:
    instruments = load_cps1_instruments(code_rom, tables_offset, version)
    if instruments and len(instruments[0]) < CPS1_FM_INSTRUMENT_SIZE:
        instruments = [
            instrument + b"\x00" * (CPS1_FM_INSTRUMENT_SIZE - len(instrument))
            for instrument in instruments
        ]
    entries = [
        convert_cps1_instrument_to_mucom(instrument, program, archive)
        for program, instrument in enumerate(instruments)
    ]
    text = (
        "; CPS-1 YM2151 voices exported in MUCOM tone-definition syntax.\n"
        "; YM2151 DT2 and AM/LFO settings are not represented by this format.\n\n"
        + "\n\n".join(entries)
        + "\n"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="ascii", newline="\r\n")
    return VM5_PROGRAM_COUNT


def convert_cps1_instrument_to_opm(instrument: bytes, program: int, archive: str) -> str:
    if len(instrument) != CPS1_FM_INSTRUMENT_SIZE:
        raise ValueError(f"expected a {CPS1_FM_INSTRUMENT_SIZE}-byte CPS-1 FM instrument")

    feedback = instrument[5] >> 3 & 0x07
    algorithm = instrument[5] & 0x07
    archive_name = re.sub(r"[^A-Za-z0-9_-]", "_", archive.upper())[:8] or "CPS1"
    lines = [
        f"@:{program} {archive_name} {program:03d}",
        "LFO: 0 0 0 0 0",
        f"CH: 64 {feedback} {algorithm} 0 0 120 0",
    ]
    for operator, name in enumerate(("M1", "M2", "C1", "C2")):
        dt_multiplier = instrument[20 + operator]
        key_attack = instrument[24 + operator]
        am_decay = instrument[28 + operator]
        dt2_sustain = instrument[32 + operator]
        sustain_release = instrument[36 + operator]
        parameters = (
            key_attack & 0x1F,
            am_decay & 0x1F,
            dt2_sustain & 0x1F,
            sustain_release & 0x0F,
            sustain_release >> 4,
            min(127, cps1_operator_attenuation(instrument, operator)),
            key_attack >> 6 & 0x03,
            dt_multiplier & 0x0F,
            dt_multiplier >> 4 & 0x07,
            dt2_sustain >> 6 & 0x03,
            am_decay >> 7,
        )
        lines.append(f"{name}: " + " ".join(str(value) for value in parameters))
    return "\n".join(lines)


def write_opm_library(
    code_rom: bytes, tables_offset: int, archive: str, output_path: Path, version: str
) -> int:
    instruments = load_cps1_instruments(code_rom, tables_offset, version)
    if instruments and len(instruments[0]) < CPS1_FM_INSTRUMENT_SIZE:
        instruments = [
            instrument + b"\x00" * (CPS1_FM_INSTRUMENT_SIZE - len(instrument))
            for instrument in instruments
        ]
    entries = [
        convert_cps1_instrument_to_opm(instrument, program, archive)
        for program, instrument in enumerate(instruments)
    ]
    text = (
        "// YM2151 OPM instrument definitions exported from CPS-1 ROM data.\n"
        "// Operator register fields are mapped from the CPS-1 instrument records.\n"
        "// LFO, pan and AMS/PMS sensitivity are not decoded and use neutral defaults.\n\n"
        + "\n\n".join(entries)
        + "\n"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="ascii", newline="\r\n")
    return VM5_PROGRAM_COUNT


def read_u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise ValueError(f"16-bit read outside ROM at 0x{offset:x}")
    return struct.unpack_from(">H", data, offset)[0]


def signed_byte(value: int) -> int:
    return value - 256 if value >= 128 else value


def variable_length(value: int) -> bytes:
    result = bytearray((value & 0x7F,))
    value >>= 7
    while value:
        result.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(result)


def midi_track(events: list[tuple[int, int, bytes]]) -> bytes:
    output = bytearray()
    previous_tick = 0
    for tick, _priority, payload in sorted(events, key=lambda event: (event[0], event[1])):
        if tick < previous_tick:
            raise ValueError("MIDI events are not in chronological order")
        output.extend(variable_length(tick - previous_tick))
        output.extend(payload)
        previous_tick = tick
    output.extend(b"\x00\xff\x2f\x00")
    return b"MTrk" + struct.pack(">I", len(output)) + output


def make_midi(tracks: list[Track], tempo_events: list[tuple[int, int]]) -> bytes:
    track_name = b"CPS-1"
    events: list[tuple[int, int, bytes]] = [
        (0, -2, b"\xf0" + variable_length(5) + b"\x7e\x7f\x09\x01\xf7"),
        (0, 0, b"\xff\x03" + bytes((len(track_name),)) + track_name)
    ]
    fm_channels = sorted({track.channel for track in tracks if track.channel != 9})
    for channel in fm_channels:
        events.extend(
            (
                (0, 0, bytes((0xB0 | channel, 0, SMAF_USER_VOICE_BANK_MSB))),
                (0, 0, bytes((0xB0 | channel, 32, SMAF_USER_VOICE_BANK_LSB))),
            )
        )
    for tick, micros_per_beat in sorted(set(tempo_events)):
        events.append((tick, 1, b"\xff\x51\x03" + micros_per_beat.to_bytes(3, "big")))
    for track in tracks:
        events.extend(track.events)
    chunk = midi_track(events)
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQN) + chunk


def decode_track(
    data: bytes,
    start: int,
    track_index: int,
    name: str,
    pcm_notes_by_program: dict[int, int],
) -> tuple[Track, list[tuple[int, int]]]:
    channel = track_index % 16
    track = Track(name, channel)
    tempo_events: list[tuple[int, int]] = []
    pos = start
    tick = 0
    note_state = 0
    note_duration = 0xFF
    transpose = 0
    global_transpose = 0
    pitch_bend = 0
    tempo_ticks_per_iteration = 393
    loops = [0] * 4
    loop_offsets = [0] * 4
    forever_offsets: set[int] = set()
    tied_pitch: int | None = None
    held_note: int | None = None
    sample_channel = track_index >= 8
    sample_program = 0

    def emit_bend(at: int, semitones: float) -> None:
        wheel = max(0, min(16383, round(8192 + semitones * (8192 / 18))))
        track.add(at, 1, bytes((0xE0 | channel, wheel & 0x7F, wheel >> 7)))

    def note_off(at: int, key: int, priority: int = 0) -> None:
        track.add(at, priority, bytes((0x80 | channel, key, 0)))

    for _ in range(MAX_STEPS):
        if pos >= len(data):
            break
        command_offset = pos
        status = data[pos]
        pos += 1
        if status >= 0x20:
            selector = ((status >> 5) & 7) - 1
            if note_state & 0x30 == 0:
                table = 1
            elif note_state & 0x10 == 0:
                table = 0
            else:
                note_state &= ~0x10
                table = 2
            if not 0 <= selector < 7:
                break
            delta = DELTA_TABLE[table][selector]
            if status & 0x1F:
                if sample_channel:
                    if sample_program not in pcm_notes_by_program:
                        tick += delta
                        continue
                    key = pcm_notes_by_program[sample_program]
                else:
                    key = max(
                        0,
                        min(
                            127,
                            (status & 0x1F) + OCTAVE_TABLE[note_state & 0x0F] + 11
                            + transpose + global_transpose,
                        ),
                    )
                duration = delta * note_duration >> 8
                is_tied = bool(note_state & 0x40) and not sample_channel
                if is_tied:
                    if held_note is None:
                        held_note = key
                        tied_pitch = key
                        emit_bend(tick, pitch_bend / 100)
                        track.add(tick, 2, bytes((0x90 | channel, held_note, NOTE_VELOCITY)))
                        track.note_count += 1
                    elif key != tied_pitch:
                        emit_bend(tick, (key - held_note) + pitch_bend / 100)
                        tied_pitch = key
                else:
                    if held_note is not None:
                        note_off(tick, held_note)
                        held_note = None
                        tied_pitch = None
                        emit_bend(tick, 0)
                    track.add(tick, 2, bytes((0x90 | channel, key, NOTE_VELOCITY)))
                    track.note_count += 1
                    note_off(tick + duration, key, 0 if duration else 3)
            tick += delta
            continue

        if status == 0x00:
            note_state ^= 0x20
        elif status == 0x01:
            note_state ^= 0x40
        elif status == 0x02:
            note_state |= 0x10
        elif status == 0x03:
            note_state ^= 8
        elif status == 0x04:
            if pos >= len(data):
                break
            note_state = (note_state & 0x97) | data[pos]
            pos += 1
        elif status == 0x05:
            if pos + 2 > len(data):
                break
            tempo_ticks_per_iteration = read_u16(data, pos)
            pos += 2
            if tempo_ticks_per_iteration:
                micros = round(((PPQN << 8) / tempo_ticks_per_iteration) / 62.5 * 1_000_000)
                # A MIDI meta tempo event is a 3-byte big-endian integer
                # (max 0xFFFFFF ≈ 16.7 s). Clamp any absurd value so the
                # SMF file remains valid and players can still render it.
                micros = max(1, min(0xFFFFFF, micros))
                tempo_events.append((tick, micros))
        elif status in (0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D):
            if pos >= len(data):
                break
            value = data[pos]
            pos += 1
            if status == 0x06:
                note_duration = value
            elif status == 0x07:
                if sample_channel:
                    attenuation = (32, 22, 16, 11, 8, 6, 4, 3, 2, 0, 0, 0, 0, 0, 0, 0)
                    volume = round(127 * attenuation[(8 - value) & 0x0F] / 32)
                else:
                    volume = value
                track.add(tick, 1, bytes((0xB0 | channel, 7, max(0, min(127, volume)))))
            elif status == 0x08:
                if sample_channel:
                    sample_program = value % 128
                else:
                    track.add(tick, 1, bytes((0xC0 | channel, value % 128)))
            elif status == 0x09:
                note_state = (note_state & 0xF8) | value
            elif status == 0x0A:
                global_transpose = signed_byte(value)
            elif status == 0x0B:
                transpose = signed_byte(value)
            elif status == 0x0C:
                raw = value >> 2
                pitch_bend = round((raw * 100 / 63) - (100 if raw >= 32 else 0))
                if not sample_channel:
                    if held_note is not None and tied_pitch is not None:
                        emit_bend(tick, (tied_pitch - held_note) + pitch_bend / 100)
                    else:
                        emit_bend(tick, pitch_bend / 100)
            elif status == 0x0D:
                pass
        elif 0x0E <= status <= 0x11:
            loop_num = status - 0x0E
            if loops[loop_num] == 0 and loop_offsets[loop_num] == 0:
                if pos >= len(data):
                    break
                loop_offsets[loop_num] = pos
                loops[loop_num] = data[pos]
                pos += 1
            elif loop_offsets[loop_num] != pos:
                # VGMTrans simply stops the track when the loop re-syncs badly.
                break
            else:
                loops[loop_num] -= 1
                pos += 1
            if pos + 2 > len(data):
                break
            jump = read_u16(data, pos)
            pos += 2
            if not jump:
                break
            if loops[loop_num] == 0:
                loop_offsets[loop_num] = 0
            else:
                pos = jump
        elif 0x12 <= status <= 0x15:
            loop_num = status - 0x12
            if loops[loop_num] - 1 == 0:
                loops[loop_num] = 0
                loop_offsets[loop_num] = 0
                if pos + 3 > len(data):
                    break
                note_state = (note_state & 0x97) | data[pos]
                pos += 1
                pos = read_u16(data, pos)
            else:
                pos += 3
        elif status == 0x16:
            if command_offset in forever_offsets:
                break
            forever_offsets.add(command_offset)
            if pos + 2 > len(data):
                break
            pos = read_u16(data, pos)
            if pos == 0:
                break
        elif status == 0x17:
            break
        elif status == 0x18:
            if pos >= len(data):
                break
            pan = data[pos] * 4
            pos += 1
            track.add(tick, 1, bytes((0xB0 | channel, 10, min(pan, 127))))
        elif 0x19 <= status <= 0x23:
            if pos >= len(data):
                break
            pos += 1
        else:
            # Unknown opcode: stop decoding this track (VGMTrans behavior).
            break
    # Reaching MAX_STEPS without a break means the track loops forever;
    # return what we have decoded so far rather than failing the sequence.

    if held_note is not None:
        note_off(tick, held_note)
        emit_bend(tick, 0)
    return track, tempo_events


def decode_sequence(
    data: bytes,
    start: int,
    title: str,
    pcm_notes_by_program: dict[int, int],
) -> tuple[list[Track], list[tuple[int, int]]]:
    if start >= len(data) or data[start] & 0x80:
        raise ValueError(f"invalid sequence header at 0x{start:x}")
    # Some entries are placeholders: a valid flag byte but all 12 track
    # pointers zero (e.g. Final Fight BGM 3, code 0x42). Treat them as an
    # empty sequence instead of a failure, matching VGMTrans behavior.
    if all(read_u16(data, start + 1 + index * 2) == 0 for index in range(12)):
        print(f"  warning: {title}: all track pointers empty (skipped)")
        return [], []
    tracks: list[Track] = []
    pcm_track = Track("PCM (Channel 10)", 9)
    has_pcm_tracks = False
    tempo_events: list[tuple[int, int]] = []
    for index in range(12):
        pointer = read_u16(data, start + 1 + index * 2)
        if pointer == 0:
            continue
        # Track data may legally sit at a lower address than the sequence
        # header, so only the upper bound and zero are invalid.
        if pointer >= len(data):
            print(
                f"  warning: {title}: invalid track {index + 1} pointer "
                f"0x{pointer:x}; skipping track"
            )
            continue
        # Track pointers that fall in the 0xFF padding beyond the actual
        # code content (e.g. Final Fight Phone 2 track 0 @ 0x9108 where the
        # real content ends at 0x07FF) would otherwise decode as thousands
        # of spurious note-ons and balloon the MIDI output to several MB.
        # Detect this by checking whether the first 16 bytes are all 0xFF.
        if all(b == 0xFF for b in data[pointer : pointer + 16]):
            print(
                f"  warning: {title}: track {index + 1} pointer 0x{pointer:x} "
                f"in 0xFF padding; skipping track"
            )
            continue
        try:
            track, tempos = decode_track(
                data, pointer, index, f"Track {index + 1:02d}", pcm_notes_by_program
            )
        except ValueError as exc:
            print(
                f"  warning: {title}: track {index + 1} at 0x{pointer:x} "
                f"failed to decode ({exc}); skipping track"
            )
            continue
        if index >= 8:
            has_pcm_tracks = True
            pcm_track.note_count += track.note_count
            for tick, priority, payload in track.events:
                if payload[0] < 0xF0:
                    payload = bytes(((payload[0] & 0xF0) | 9,)) + payload[1:]
                pcm_track.add(tick, priority, payload)
        else:
            for controller, value in (
                (101, 0), (100, 0), (6, 18), (38, 0), (101, 127), (100, 127)
            ):
                track.add(0, 0, bytes((0xB0 | track.channel, controller, value)))
            tracks.append(track)
        tempo_events.extend(tempos)
    if has_pcm_tracks:
        tracks.append(pcm_track)
    if not tracks:
        print(f"  warning: {title}: no track decoded any events (skipped)")
    return tracks, tempo_events


def safe_filename(title: str) -> str:
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")
    return result or "untitled"


def convert_game(
    xml_path: Path,
    output_dir: Path | None = None,
    wav_dir: Path | None = None,
    vm5_output: Path | None = None,
    muc_output: Path | None = None,
    opm_output: Path | None = None,
) -> None:
    game = read_game(xml_path)
    data = load_rom(game)

    # Collect candidate tables offsets (best first).
    if game.tables_offset != 0:
        candidates: list[int] = [game.tables_offset]
    else:
        candidates = autodetect_tables_candidates(data, game.titles, game.version)
        print(f"Auto-detected {len(candidates)} CPS-1 tables offset candidate(s): "
              + ", ".join(f"0x{c:x}" for c in candidates))

    # PCM sample decoding and WAV extraction are independent of the tables offset.
    pcm_rom = load_pcm_rom(game)
    pcm_samples = decode_pcm_samples(pcm_rom)
    pcm_sample_count = write_pcm_wavs(pcm_samples, wav_dir or WAV_DIR / game.archive)
    print(
        f"Extracted {pcm_sample_count} PCM WAV files "
        f"to {wav_dir or WAV_DIR / game.archive}"
    )

    destination = output_dir or MIDI_DIR / game.archive
    destination.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None
    for tables in candidates:
        is_v200 = game.version == "CPS1_V2.00"
        is_v350 = game.version == "CPS1_V3.50"
        header_size = 3 if is_v200 else 4 if is_v350 else 6
        if tables + header_size > len(data):
            last_error = ValueError(f"CPS-1 tables header outside ROM at 0x{tables:x}")
            continue
        sequence_count = data[tables]
        sequence_table = tables + header_size
        # The count byte only records how many of the 256 table slots are
        # used, not the highest code (e.g. Final Fight has code 0xF0 with
        # count 0xC3). Size the table by the highest code we actually need.
        table_codes = min(256, max(game.titles, default=0) + 1)
        if sequence_table + table_codes * 2 > len(data):
            last_error = ValueError(f"CPS-1 sequence pointer table at 0x{tables:x} extends past the code ROM")
            continue

        # Build PCM program→note mapping for this candidate.
        try:
            pcm_notes_by_program = build_pcm_notes_by_program(
                data, tables, pcm_samples, game.version
            )
        except ValueError:
            pcm_notes_by_program = {}

        # Attempt to decode every title with this candidate offset.
        decoded: list[tuple[int, str, list[Track], list[tuple[int, int]]]] = []
        failure: str | None = None
        for code, title in game.titles.items():
            if code >= table_codes:
                failure = f"song code 0x{code:02x} is outside the sequence table (0x{table_codes:02x})"
                break
            sequence_start = read_u16(data, sequence_table + code * 2)
            if sequence_start == 0:
                continue
            if sequence_start >= len(data) or data[sequence_start] & 0x80:
                continue
            try:
                tracks, tempos = decode_sequence(data, sequence_start, title, pcm_notes_by_program)
            except ValueError as exc:
                failure = f"0x{code:02x} {title}: {exc}"
                break
            if not any(track.note_count for track in tracks):
                print(
                    f"  warning: 0x{code:02x} {title}: decoded sequence "
                    f"contains no notes (skipped)"
                )
                continue
            decoded.append((code, title, tracks, tempos))

        if failure is not None:
            print(f"Candidate 0x{tables:x} failed: {failure}")
            last_error = ValueError(failure)
            continue
        # A genuine tables offset must explain at least one title; the XML
        # legitimately contains PCM one-shots (flag bit 7) and empty
        # placeholder entries that do not produce MIDI output.
        if not decoded:
            last_error = ValueError(f"0x{tables:x} explained no titles")
            print(f"Candidate 0x{tables:x} failed: {last_error}")
            continue

        # All sequences decoded successfully — commit this candidate.
        print(f"Using CPS-1 tables offset 0x{tables:x} ({len(decoded)} sequences)")
        voice_path = vm5_output or BASE_DIR / "voices" / f"{game.archive}.vm5"
        try:
            program_count = write_vm5_library(data, tables, voice_path, game.version)
            print(f"Wrote {program_count} YM2151 programs to {voice_path}")
        except ValueError as exc:
            print(f"Skipping VM5 voice export: {exc}")
        muc_path = muc_output or BASE_DIR / "voices" / f"{game.archive}.muc"
        try:
            muc_count = write_mucom_library(data, tables, game.archive, muc_path, game.version)
            print(f"Wrote {muc_count} MUCOM YM2151 voice definitions to {muc_path}")
        except ValueError as exc:
            print(f"Skipping MUCOM voice export: {exc}")
        opm_path = opm_output or BASE_DIR / "voices" / f"{game.archive}.opm"
        try:
            opm_count = write_opm_library(data, tables, game.archive, opm_path, game.version)
            print(f"Wrote {opm_count} YM2151 OPM voice definitions to {opm_path}")
        except ValueError as exc:
            print(f"Skipping OPM voice export: {exc}")

        for code, title, tracks, tempos in decoded:
            midi = make_midi(tracks, tempos)
            output = destination / f"{code:02x}_{safe_filename(title)}.mid"
            output.write_bytes(midi)
            print(f"{output.name}: {len(tracks)} tracks, {sum(t.note_count for t in tracks)} notes")
        return

    raise ValueError(
        f"no CPS-1 tables offset candidate produced a valid sequence set: {last_error}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_xml", type=Path, help="game metadata XML file")
    parser.add_argument("--output-dir", type=Path, help="directory for generated MIDI files")
    parser.add_argument("--wav-dir", type=Path, help="directory for extracted PCM WAV files")
    parser.add_argument("--vm5-output", type=Path, help="path for the generated MA-5 VM5 voice library")
    parser.add_argument("--muc-output", type=Path, help="path for the generated MUCOM voice definitions")
    parser.add_argument("--opm-output", type=Path, help="path for the generated YM2151 OPM voice definitions")
    arguments = parser.parse_args()
    convert_game(
        arguments.game_xml,
        arguments.output_dir,
        arguments.wav_dir,
        arguments.vm5_output,
        arguments.muc_output,
        arguments.opm_output,
    )


if __name__ == "__main__":
    main()
