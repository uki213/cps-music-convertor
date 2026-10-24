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
    if version != "CPS1_V4.25":
        raise ValueError(f"unsupported CPS-1 format version {version!r}; expected 'CPS1_V4.25'")

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


def autodetect_tables_offset(data: bytes, titles: dict[int, str]) -> int:
    """Locate the CPS-1 sound-program tables when the XML omits the offset.

    The header layout is fixed by this tool:
      +0  sequence count (u8)
      +2  YM2151 instrument table base (u16 big-endian)
      +6  sequence pointer table (count * u16 big-endian)
    A candidate offset is ranked by (resolved song codes, distinct sequence
    starts, strict first-track pointers).  Requiring distinct and well-formed
    starts filters out data regions that merely contain words with a clear
    high bit, which otherwise masquerade as a valid table.
    """
    if not titles:
        raise ValueError("cannot auto-detect the CPS-1 tables offset without song titles")
    minimum_count = max(titles) + 1
    limit = min(len(data) - 8, 0x20000)
    # A real table is one whose pointer entries resolve to a large set of
    # *distinct, well-formed* sequence starts.  Scoring on resolved entries
    # alone is not enough: unrelated data regions can contain many words whose
    # high bit is clear.  We therefore rank candidates on (resolved, distinct,
    # strict) and require the best candidate to clear a minimum threshold on
    # all three axes.
    best_key = None
    best_offset = -1
    for offset in range(0x400, limit, 1):
        sequence_count = data[offset]
        if sequence_count < minimum_count or sequence_count > 256:
            continue
        sequence_table = offset + 6
        if sequence_table + sequence_count * 2 > len(data):
            continue
        # The YM2151 instrument table must fit inside the code ROM.
        instrument_table = read_u16(data, offset + 2)
        if instrument_table + VM5_PROGRAM_COUNT * CPS1_FM_INSTRUMENT_SIZE > len(data):
            continue
        resolved = 0
        strict = 0
        distinct = set()
        for code in titles:
            start = read_u16(data, sequence_table + code * 2)
            if start == 0 or start >= len(data) or data[start] & 0x80:
                continue
            resolved += 1
            distinct.add(start)
            first_track = read_u16(data, start + 1)
            if start < first_track < len(data):
                strict += 1
        key = (resolved, len(distinct), strict, -offset)
        if best_key is None or key > best_key:
            best_key = key
            best_offset = offset
    if best_key is None:
        raise ValueError(
            "could not auto-detect the CPS-1 tables offset; "
            "set the 'tables' attribute on <romlist>"
        )
    resolved, distinct, strict = best_key[0], best_key[1], best_key[2]
    # strict is only a tie-breaker: many legitimate codes (PCM / voice-only)
    # resolve to valid starts but have no FM first-track, so requiring a high
    # strict count would reject real tables.
    threshold = max(4, len(titles) // 2)
    if resolved < threshold or distinct < threshold:
        raise ValueError(
            f"could not auto-detect a confident CPS-1 tables offset "
            f"(best: resolved={resolved}, distinct={distinct}, strict={strict}); "
            "set the 'tables' attribute on <romlist>"
        )
    return best_offset


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


def extract_pcm_samples(
    code_rom: bytes,
    pcm_rom: bytes,
    tables_offset: int,
    output_dir: Path,
) -> tuple[dict[int, int], int]:
    if tables_offset + 6 > len(code_rom):
        raise ValueError(f"CPS-1 tables header outside ROM at 0x{tables_offset:x}")
    instrument_table = read_u16(code_rom, tables_offset + 4)
    instrument_end = min(instrument_table + 128 * 4, len(code_rom))
    if instrument_table >= len(code_rom):
        raise ValueError(f"MSM6295 instrument table outside ROM at 0x{instrument_table:x}")

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
            raise ValueError(
                f"PCM sample {sample_index} ends outside the ROM: 0x{end:x}"
            )
        samples[sample_index] = decode_msm6295(pcm_rom[start:end])

    notes_by_sample = {
        sample_index: PCM_NOTE_BASE + index
        for index, sample_index in enumerate(sorted(samples))
    }
    if notes_by_sample and max(notes_by_sample.values()) > 127:
        raise ValueError("too many PCM waveforms to assign each one a MIDI note")

    output_dir.mkdir(parents=True, exist_ok=True)
    for sample_index, pcm_data in samples.items():
        note = notes_by_sample[sample_index]
        with wave.open(str(output_dir / f"{note:03d}.wav"), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(PCM_SAMPLE_RATE)
            wav_file.writeframes(pcm_data)

    notes_by_program: dict[int, int] = {}
    for program in range(min(128, (instrument_end - instrument_table) // 4)):
        offset = instrument_table + program * 4
        if not code_rom[offset] & 0x80:
            break
        sample_index = code_rom[offset + 1] - 1
        if sample_index in notes_by_sample:
            notes_by_program[program] = notes_by_sample[sample_index]
    return notes_by_program, len(samples)


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


def load_cps1_instruments(code_rom: bytes, tables_offset: int) -> list[bytes]:
    if tables_offset + 6 > len(code_rom):
        raise ValueError(f"CPS-1 tables header outside ROM at 0x{tables_offset:x}")
    instrument_table = read_u16(code_rom, tables_offset + 2)
    table_end = instrument_table + VM5_PROGRAM_COUNT * CPS1_FM_INSTRUMENT_SIZE
    if table_end > len(code_rom):
        raise ValueError("CPS-1 YM2151 instrument table extends past the code ROM")
    return [
        code_rom[
            instrument_table + program * CPS1_FM_INSTRUMENT_SIZE :
            instrument_table + (program + 1) * CPS1_FM_INSTRUMENT_SIZE
        ]
        for program in range(VM5_PROGRAM_COUNT)
    ]


def write_vm5_library(code_rom: bytes, tables_offset: int, output_path: Path) -> int:
    programs = bytearray()
    for program, instrument in enumerate(load_cps1_instruments(code_rom, tables_offset)):
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
    code_rom: bytes, tables_offset: int, archive: str, output_path: Path
) -> int:
    instruments = load_cps1_instruments(code_rom, tables_offset)
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
    code_rom: bytes, tables_offset: int, archive: str, output_path: Path
) -> int:
    instruments = load_cps1_instruments(code_rom, tables_offset)
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
            raise ValueError(f"track at 0x{start:x} ran past end of ROM")
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
                raise ValueError(f"invalid note duration selector at 0x{command_offset:x}")
            delta = DELTA_TABLE[table][selector]
            if status & 0x1F:
                if sample_channel:
                    if sample_program not in pcm_notes_by_program:
                        raise ValueError(
                            f"PCM instrument {sample_program} has no mapped sample "
                            f"at 0x{command_offset:x}"
                        )
                    key = pcm_notes_by_program[sample_program]
                else:
                    key = (
                        (status & 0x1F) + OCTAVE_TABLE[note_state & 0x0F] + 11
                        + transpose + global_transpose
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
                    if not 0 <= key <= 127:
                        raise ValueError(f"note {key} is outside the MIDI range at 0x{command_offset:x}")
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
                raise ValueError(f"missing note-state operand at 0x{command_offset:x}")
            note_state = (note_state & 0x97) | data[pos]
            pos += 1
        elif status == 0x05:
            tempo_ticks_per_iteration = read_u16(data, pos)
            pos += 2
            if tempo_ticks_per_iteration:
                micros = round(((PPQN << 8) / tempo_ticks_per_iteration) / 62.5 * 1_000_000)
                tempo_events.append((tick, micros))
        elif status in (0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D):
            if pos >= len(data):
                raise ValueError(f"missing command operand at 0x{command_offset:x}")
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
                    if held_note is not None:
                        emit_bend(tick, (tied_pitch - held_note) + pitch_bend / 100)
                    else:
                        emit_bend(tick, pitch_bend / 100)
            elif status == 0x0D:
                pass
        elif 0x0E <= status <= 0x11:
            loop_num = status - 0x0E
            if loops[loop_num] == 0 and loop_offsets[loop_num] == 0:
                if pos >= len(data):
                    raise ValueError(f"missing loop count at 0x{command_offset:x}")
                loop_offsets[loop_num] = pos
                loops[loop_num] = data[pos]
                pos += 1
            elif loop_offsets[loop_num] != pos:
                raise ValueError(f"loop desynchronized in track at 0x{start:x}")
            else:
                loops[loop_num] -= 1
                pos += 1
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
                    raise ValueError(f"truncated loop break at 0x{command_offset:x}")
                note_state = (note_state & 0x97) | data[pos]
                pos += 1
                pos = read_u16(data, pos)
            else:
                pos += 3
        elif status == 0x16:
            if command_offset in forever_offsets:
                break
            forever_offsets.add(command_offset)
            pos = read_u16(data, pos)
            if pos == 0:
                break
        elif status == 0x17:
            break
        elif status == 0x18:
            if pos >= len(data):
                raise ValueError(f"missing pan operand at 0x{command_offset:x}")
            pan = data[pos] * 4
            pos += 1
            track.add(tick, 1, bytes((0xB0 | channel, 10, min(pan, 127))))
        elif 0x19 <= status <= 0x23:
            if pos >= len(data):
                raise ValueError(f"missing command operand at 0x{command_offset:x}")
            pos += 1
        else:
            raise ValueError(f"unknown command 0x{status:02x} at 0x{command_offset:x}")
    else:
        raise ValueError(f"track at 0x{start:x} exceeded {MAX_STEPS} commands")

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
    first_track = read_u16(data, start + 1)
    if first_track <= start or first_track > len(data):
        raise ValueError(f"invalid first track pointer 0x{first_track:x} at 0x{start:x}")
    tracks: list[Track] = []
    pcm_track = Track("PCM (Channel 10)", 9)
    has_pcm_tracks = False
    tempo_events: list[tuple[int, int]] = []
    for index in range(12):
        pointer = read_u16(data, start + 1 + index * 2)
        if pointer == 0:
            continue
        if pointer < start or pointer >= len(data):
            raise ValueError(f"invalid track {index + 1} pointer 0x{pointer:x} in {title}")
        track, tempos = decode_track(
            data, pointer, index, f"Track {index + 1:02d}", pcm_notes_by_program
        )
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
        raise ValueError(f"sequence {title!r} has no tracks")
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
    if game.tables_offset == 0:
        detected = autodetect_tables_offset(data, game.titles)
        game = replace(game, tables_offset=detected)
        print(f"Auto-detected CPS-1 tables offset 0x{detected:x}")
    voice_path = vm5_output or BASE_DIR / "voices" / f"{game.archive}.vm5"
    program_count = write_vm5_library(data, game.tables_offset, voice_path)
    print(f"Wrote {program_count} YM2151 programs to {voice_path}")
    muc_path = muc_output or BASE_DIR / "voices" / f"{game.archive}.muc"
    muc_count = write_mucom_library(data, game.tables_offset, game.archive, muc_path)
    print(f"Wrote {muc_count} MUCOM YM2151 voice definitions to {muc_path}")
    opm_path = opm_output or BASE_DIR / "voices" / f"{game.archive}.opm"
    opm_count = write_opm_library(data, game.tables_offset, game.archive, opm_path)
    print(f"Wrote {opm_count} YM2151 OPM voice definitions to {opm_path}")
    pcm_rom = load_pcm_rom(game)
    pcm_notes_by_program, pcm_sample_count = extract_pcm_samples(
        data,
        pcm_rom,
        game.tables_offset,
        wav_dir or WAV_DIR / game.archive,
    )
    print(
        f"Extracted {pcm_sample_count} PCM WAV files "
        f"to {wav_dir or WAV_DIR / game.archive}"
    )
    tables = game.tables_offset
    if tables + 6 > len(data):
        raise ValueError(f"CPS-1 tables header outside ROM at 0x{tables:x}")
    sequence_count = data[tables]
    sequence_table = tables + 6
    if sequence_table + sequence_count * 2 > len(data):
        raise ValueError("CPS-1 sequence pointer table extends past the code ROM")
    destination = output_dir or MIDI_DIR / game.archive
    destination.mkdir(parents=True, exist_ok=True)
    for code, title in game.titles.items():
        if code >= sequence_count:
            raise ValueError(f"song code 0x{code:02x} is outside the sequence table")
        sequence_start = read_u16(data, sequence_table + code * 2)
        if sequence_start == 0:
            print(f"Skipped 0x{code:02x} {title}: no sequence pointer")
            continue
        if sequence_start >= len(data) or data[sequence_start] & 0x80:
            print(f"Skipped 0x{code:02x} {title}: not a music sequence")
            continue
        tracks, tempos = decode_sequence(
            data, sequence_start, title, pcm_notes_by_program
        )
        midi = make_midi(tracks, tempos)
        output = destination / f"{code:02x}_{safe_filename(title)}.mid"
        output.write_bytes(midi)
        print(f"{output.name}: {len(tracks)} tracks, {sum(t.note_count for t in tracks)} notes")


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
