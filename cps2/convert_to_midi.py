#!/usr/bin/env python3
"""Convert CPS-2 music sequences described by a game XML file to MIDI."""

from __future__ import annotations

import argparse
import csv
import re
import struct
import wave
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ROM_DIR = BASE_DIR / "roms"
MIDI_DIR = BASE_DIR / "midi"
WAV_DIR = BASE_DIR / "wav"
QSOUND_SAMPLE_RATE = 24038
SEQUENCE_TABLE = 0x9005
PPQN = 48
MAX_STEPS = 100_000
NOTE_VELOCITY = 127

DELTA_TABLE = (
    (2, 4, 8, 16, 32, 64, 128),
    (3, 6, 12, 24, 48, 96, 192),
    (0, 9, 18, 36, 72, 144, 0),
)
OCTAVE_TABLE = (0, 12, 24, 36, 48, 60, 72, 84, 24, 36, 48, 60, 72, 84, 96, 108)
VOLUME_TABLE = (
    0, 0xA, 0x18, 0x26, 0x34, 0x42, 0x51, 0x5F, 0x6E, 0x7D, 0x8C, 0x9B,
    0xAA, 0xBA, 0xC9, 0xD9, 0xE9, 0xF8, 0x109, 0x119, 0x129, 0x13A,
    0x14A, 0x15B, 0x16C, 0x17D, 0x18E, 0x1A0, 0x1B2, 0x1C3, 0x1D5,
    0x1E8, 0x1FC, 0x20D, 0x21F, 0x232, 0x245, 0x259, 0x26C, 0x280,
    0x294, 0x2A8, 0x2BD, 0x2D2, 0x2E7, 0x2FC, 0x311, 0x327, 0x33D,
    0x353, 0x36A, 0x381, 0x398, 0x3B0, 0x3C7, 0x3DF, 0x3F8, 0x411,
    0x42A, 0x443, 0x45D, 0x477, 0x492, 0x4AD, 0x4C8, 0x4E4, 0x501,
    0x51D, 0x53B, 0x558, 0x577, 0x596, 0x5B5, 0x5D5, 0x5F5, 0x616,
    0x638, 0x65A, 0x67D, 0x6A1, 0x6C5, 0x6EB, 0x711, 0x738, 0x75F,
    0x788, 0x7B2, 0x7DC, 0x808, 0x834, 0x862, 0x891, 0x8C2, 0x8F3,
    0x927, 0x95B, 0x991, 0x9C9, 0xA03, 0xA3F, 0xA7D, 0xABD, 0xAFF,
    0xB44, 0xB8C, 0xBD7, 0xC25, 0xC76, 0xCCC, 0xD26, 0xD85, 0xDE9,
    0xE53, 0xEC4, 0xF3C, 0xFBD, 0x1048, 0x10DF, 0x1184, 0x123A,
    0x1305, 0x13EA, 0x14F1, 0x1625, 0x179B, 0x1974, 0x1BFB, 0x1FFD,
)

# CPSCommon.h lookup tables; command bytes index all 128 entries.
VIBRATO_DEPTH_UNITS = (
    0x000, 0x00C, 0x00D, 0x00E, 0x00E, 0x00F, 0x010, 0x011,
    0x012, 0x012, 0x013, 0x014, 0x015, 0x016, 0x016, 0x017,
    0x018, 0x019, 0x01A, 0x01C, 0x01D, 0x01F, 0x020, 0x022,
    0x024, 0x025, 0x027, 0x029, 0x02A, 0x02C, 0x02D, 0x02F,
    0x031, 0x032, 0x034, 0x038, 0x03B, 0x03E, 0x041, 0x045,
    0x048, 0x04B, 0x04E, 0x052, 0x055, 0x058, 0x05B, 0x05F,
    0x062, 0x065, 0x06A, 0x072, 0x07A, 0x082, 0x08A, 0x092,
    0x09A, 0x0A2, 0x0AA, 0x0B2, 0x0BA, 0x0C2, 0x0CA, 0x0D2,
    0x0DA, 0x0E2, 0x0EA, 0x0F2, 0x0FA, 0x104, 0x114, 0x124,
    0x134, 0x144, 0x154, 0x164, 0x174, 0x184, 0x194, 0x1A4,
    0x1B4, 0x1C4, 0x1D4, 0x1E4, 0x1F4, 0x208, 0x228, 0x248,
    0x268, 0x288, 0x2A8, 0x2C8, 0x2E8, 0x308, 0x328, 0x348,
    0x368, 0x388, 0x3A8, 0x3C8, 0x3E8, 0x410, 0x450, 0x490,
    0x4D0, 0x510, 0x550, 0x590, 0x5D0, 0x610, 0x650, 0x690,
    0x6D0, 0x710, 0x750, 0x790, 0x7D0, 0x828, 0x888, 0x8E8,
    0x948, 0x9A8, 0xA08, 0xA68, 0xAC8, 0xB28, 0xB88, 0xBE8,
)
LFO_RATE_UNITS = (
    0x0000, 0x0118, 0x0126, 0x0133, 0x0142, 0x0150, 0x015D, 0x016C,
    0x0179, 0x0187, 0x0196, 0x01A3, 0x01B1, 0x01C0, 0x01DB, 0x01F7,
    0x0213, 0x022F, 0x024B, 0x0267, 0x0283, 0x029F, 0x02BB, 0x02D7,
    0x02F3, 0x030F, 0x032B, 0x0347, 0x0363, 0x037F, 0x03B7, 0x03EF,
    0x0427, 0x045F, 0x0496, 0x04CF, 0x0506, 0x053E, 0x0576, 0x05AE,
    0x05E6, 0x061E, 0x0656, 0x068E, 0x06C6, 0x06FE, 0x076E, 0x07DE,
    0x084D, 0x08BD, 0x092D, 0x099D, 0x0A0D, 0x0A7D, 0x0AEC, 0x0B5C,
    0x0BCC, 0x0C3C, 0x0CAC, 0x0D1C, 0x0D8C, 0x0DFB, 0x0EDB, 0x0FBB,
    0x109B, 0x117A, 0x125A, 0x133A, 0x141A, 0x14F9, 0x15D9, 0x16B9,
    0x1799, 0x1878, 0x1958, 0x1A38, 0x1B17, 0x1BF7, 0x1DB6, 0x1F76,
    0x2135, 0x22F5, 0x24B4, 0x2674, 0x2833, 0x29F3, 0x2BB2, 0x2D71,
    0x2F31, 0x30F0, 0x32B0, 0x346F, 0x362F, 0x37EE, 0x3B6D, 0x3EEC,
    0x426B, 0x45EA, 0x4969, 0x4CE8, 0x5066, 0x53E5, 0x5764, 0x5AE3,
    0x5E62, 0x61E1, 0x6550, 0x68DE, 0x6C5E, 0x6FDC, 0x76DA, 0x7DD8,
    0x84D6, 0x8BD3, 0x92D1, 0x99CF, 0xA0CD, 0xA7CB, 0xAEC8, 0xB5C6,
    0xBCC4, 0xC3C2, 0xCABF, 0xD1BD, 0xD8BB, 0xDFB9, 0xEDB4, 0xFBB0,
)


@dataclass
class Track:
    name: str
    channel: int
    events: list[tuple[int, int, bytes]] = field(default_factory=list)
    note_count: int = 0

    def add(self, tick: int, priority: int, payload: bytes) -> None:
        self.events.append((tick, priority, payload))


@dataclass(frozen=True)
class GameMetadata:
    archive: str
    name: str
    code_roms: tuple[tuple[int, str], ...]
    pcm_roms: tuple[tuple[int, str], ...]
    titles: dict[int, str]
    instrument_table_offset: int | None
    instrument_banks: int | None
    instrument_table_format: str
    sample_table_offset: int | None
    sample_table_length: int | None


@dataclass(frozen=True)
class QSoundSample:
    bank: int
    start: int
    loop: int
    end: int
    unity_key: int


def read_game_metadata(xml_path: Path) -> GameMetadata:
    root = ET.parse(xml_path).getroot()
    if root.tag != "game":
        raise ValueError(f"expected a <game> root in {xml_path}")
    driver = root.find("driver")
    if driver is None or driver.get("type", "").lower() != "cps2":
        raise ValueError(f"{xml_path} does not describe a CPS-2 game")

    romlist = root.find("romlist")
    if romlist is None:
        raise ValueError(f"missing <romlist> in {xml_path}")
    archive = romlist.get("archive", "").strip()
    if not archive or archive in (".", "..") or Path(archive).name != archive:
        raise ValueError(f"invalid ROM archive name in {xml_path}")

    code_roms: list[tuple[int, str]] = []
    pcm_roms: list[tuple[int, str]] = []
    for rom in romlist.findall("rom"):
        rom_type = rom.get("type")
        if rom_type not in ("code", "pcm"):
            continue
        filename = (rom.text or "").strip()
        try:
            offset = int(rom.get("offset", ""), 0)
        except ValueError as error:
            raise ValueError(f"invalid {rom_type} ROM offset for {filename!r}") from error
        if not filename or Path(filename).name != filename or offset < 0:
            raise ValueError(f"invalid {rom_type} ROM entry in {xml_path}")
        (code_roms if rom_type == "code" else pcm_roms).append((offset, filename))
    if not code_roms:
        raise ValueError(f"no <rom type=\"code\"> entries in {xml_path}")

    table_attributes = {
        "instrument-table": "instrument_table_offset",
        "instrument-banks": "instrument_banks",
        "sample-table": "sample_table_offset",
        "sample-table-length": "sample_table_length",
    }
    table_values: dict[str, int | None] = {field_name: None for field_name in table_attributes.values()}
    provided_attributes = [attribute for attribute in table_attributes if romlist.get(attribute) is not None]
    if provided_attributes and len(provided_attributes) != len(table_attributes):
        raise ValueError(f"instrument and sample table attributes must be provided together in {xml_path}")
    for attribute, field_name in table_attributes.items():
        value = romlist.get(attribute)
        if value is None:
            continue
        try:
            table_values[field_name] = int(value, 0)
        except ValueError as error:
            raise ValueError(f"invalid {attribute!r} value {value!r} in {xml_path}") from error
    instrument_banks = table_values["instrument_banks"]
    instrument_table_format = romlist.get("instrument-table-format", "linear").strip().lower()
    if instrument_table_format not in ("linear", "pointers"):
        raise ValueError(
            f"unsupported instrument-table-format {instrument_table_format!r} in {xml_path}"
        )
    sample_table_length = table_values["sample_table_length"]
    if instrument_banks is not None and instrument_banks <= 0:
        raise ValueError(f"'instrument-banks' must be positive in {xml_path}")
    if sample_table_length is not None and (
        sample_table_length <= 0 or sample_table_length % 8
    ):
        raise ValueError(f"'sample-table-length' must be a positive multiple of 8 in {xml_path}")

    titles_element = root.find("titlelist")
    if titles_element is None:
        raise ValueError(f"missing <titlelist> in {xml_path}")
    titles: dict[int, str] = {}
    for title in titles_element.findall("title"):
        code_text = title.get("code", "")
        try:
            code = int(code_text, 0)
        except ValueError as error:
            raise ValueError(f"invalid song code {code_text!r} in {xml_path}") from error
        name = (title.text or "").strip()
        if not 0 <= code <= 0xFF or code in titles or not name:
            raise ValueError(f"duplicate or empty song title for code {code_text!r}")
        titles[code] = name
    if not titles:
        raise ValueError(f"no <title> entries in {xml_path}")
    return GameMetadata(
        archive=archive,
        name=root.findtext("name", "").strip(),
        code_roms=tuple(code_roms),
        pcm_roms=tuple(pcm_roms),
        titles=titles,
        instrument_table_offset=table_values["instrument_table_offset"],
        instrument_banks=instrument_banks,
        instrument_table_format=instrument_table_format,
        sample_table_offset=table_values["sample_table_offset"],
        sample_table_length=sample_table_length,
    )


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


def load_code_rom(metadata: GameMetadata) -> bytes:
    roms: list[tuple[int, bytes, str]] = []
    for offset, filename in sorted(metadata.code_roms):
        roms.append((offset, read_rom_file(metadata.archive, filename), filename))

    end = 0
    for offset, rom, filename in roms:
        if offset < end:
            raise ValueError(f"code ROM overlaps another ROM at offset 0x{offset:x}: {filename}")
        end = offset + len(rom)
    image = bytearray(b"\xff" * end)
    for rom_entry in roms:
        offset, rom = rom_entry[0], rom_entry[1]
        image[offset : offset + len(rom)] = rom
    return bytes(image)


def load_pcm_rom(metadata: GameMetadata) -> bytes:
    if not metadata.pcm_roms:
        raise ValueError(f"no <rom type=\"pcm\"> entries for archive {metadata.archive!r}")

    roms: list[tuple[int, bytes, str]] = []
    for offset, filename in sorted(metadata.pcm_roms):
        raw = read_rom_file(metadata.archive, filename)
        if len(raw) % 2:
            raise ValueError(
                f"PCM ROM size must be even for 16-bit word swap: {filename!r}"
            )
        swapped = bytearray(len(raw))
        swapped[0::2] = raw[1::2]
        swapped[1::2] = raw[0::2]
        roms.append((offset, bytes(swapped), filename))

    end = 0
    for offset, rom, filename in roms:
        if offset < end:
            raise ValueError(f"PCM ROM overlaps another ROM at offset 0x{offset:x}: {filename}")
        end = offset + len(rom)
    image = bytearray(b"\xff" * end)
    for rom_entry in roms:
        offset, rom = rom_entry[0], rom_entry[1]
        image[offset : offset + len(rom)] = rom
    return bytes(image)


def load_qsound_samples(data: bytes, metadata: GameMetadata) -> list[QSoundSample]:
    if (
        metadata.sample_table_offset is None
        or metadata.sample_table_length is None
    ):
        raise ValueError("PCM WAV extraction requires sample-table and sample-table-length XML attributes")
    sample_start = metadata.sample_table_offset
    sample_end = sample_start + metadata.sample_table_length
    if sample_end > len(data):
        raise ValueError("sample table extends past the code ROM image")
    return [
        QSoundSample(
            bank=data[offset],
            start=int.from_bytes(data[offset + 1 : offset + 3], "little"),
            loop=int.from_bytes(data[offset + 3 : offset + 5], "little"),
            end=int.from_bytes(data[offset + 5 : offset + 7], "little"),
            unity_key=data[offset + 7],
        )
        for offset in range(sample_start, sample_end, 8)
    ]


def load_instrument_sample_indices(
    data: bytes, metadata: GameMetadata
) -> dict[tuple[int, int], int]:
    if metadata.instrument_table_offset is None or metadata.instrument_banks is None:
        raise ValueError("instrument sample mapping requires instrument-table and instrument-banks XML attributes")

    instrument_start = metadata.instrument_table_offset
    indices: dict[tuple[int, int], int] = {}
    if metadata.instrument_table_format == "linear":
        instrument_end = instrument_start + metadata.instrument_banks * 256 * 8
        if instrument_end > len(data):
            raise ValueError("instrument table extends past the code ROM image")
        for bank in range(metadata.instrument_banks * 2):
            bank_offset = (bank // 2) * 256 + (bank % 2) * 128
            for program in range(128):
                row = bank_offset + program
                offset = instrument_start + row * 8
                indices[(bank, program)] = int.from_bytes(data[offset : offset + 2], "little")
        return indices

    pointer_end = instrument_start + metadata.instrument_banks * 2
    if pointer_end > len(data):
        raise ValueError("instrument table pointer list extends past the code ROM image")
    pointers = [
        int.from_bytes(data[offset : offset + 2], "little")
        for offset in range(instrument_start, pointer_end, 2)
    ]
    for bank_index, table_start in enumerate(pointers):
        if not table_start:
            continue
        if bank_index + 1 < len(pointers) and pointers[bank_index + 1] > table_start:
            table_end = pointers[bank_index + 1]
        else:
            table_end = table_start + 4 * 256
        if table_end > len(data) or table_start > table_end:
            raise ValueError(f"instrument table bank {bank_index} extends past the code ROM image")
        row_count = (table_end - table_start) // 4
        for row in range(min(row_count, 256)):
            offset = table_start + row * 4
            sample_index = int.from_bytes(data[offset : offset + 2], "little")
            if bank_index != 0 and sample_index == 0 and data[offset + 2] == 0:
                break
            bank = bank_index * 2 + row // 128
            program = row % 128
            indices[(bank, program)] = sample_index
    return indices


def load_sample_unity_keys(data: bytes, metadata: GameMetadata) -> dict[tuple[int, int], int]:
    samples = load_qsound_samples(data, metadata)
    instrument_samples = load_instrument_sample_indices(data, metadata)
    return {
        key: samples[resolve_sample_index(sample_index, len(samples))].unity_key
        for key, sample_index in instrument_samples.items()
    }


def resolve_sample_index(sample_index: int, sample_count: int) -> int:
    # Match the QSound instrument parser's legacy bank flag and invalid-index fallback.
    if sample_index >= 0x8000:
        sample_index -= 0x8000
    return sample_index if sample_index < sample_count else 0


def write_qsound_wavs(
    code_rom: bytes,
    pcm_rom: bytes,
    metadata: GameMetadata,
    output_dir: Path,
) -> int:
    samples = load_qsound_samples(code_rom, metadata)
    instrument_samples = load_instrument_sample_indices(code_rom, metadata)
    resolved_indices = {
        key: resolve_sample_index(sample_index, len(samples))
        for key, sample_index in instrument_samples.items()
    }
    referenced_samples = sorted(set(resolved_indices.values()))
    if not referenced_samples:
        raise ValueError(f"no instrument sample references found for {metadata.archive!r}")

    base_address = samples[0].bank << 16
    output_dir.mkdir(parents=True, exist_ok=True)
    for sample_index in referenced_samples:
        sample = samples[sample_index]
        start = (sample.bank << 16) | sample.start
        end = (
            (sample.bank + 1) << 16
            if sample.end == 0
            else (sample.bank << 16) | sample.end
        )
        start -= base_address
        end -= base_address
        if end < start:
            end = start
        if start < 0 or end > len(pcm_rom):
            raise ValueError(
                f"sample {sample_index} range 0x{start:x}-0x{end:x} "
                f"exceeds PCM ROM size 0x{len(pcm_rom):x}"
            )
        pcm = memoryview(pcm_rom)[start:end]
        path = output_dir / f"{sample_index:03d}.wav"
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(QSOUND_SAMPLE_RATE)
            chunk_size = 32768
            for offset in range(0, len(pcm), chunk_size):
                chunk = pcm[offset : offset + chunk_size]
                frames = bytearray(len(chunk) * 2)
                # QSound's signed 8-bit values occupy the high byte of a 16-bit sample.
                frames[1::2] = chunk
                wav_file.writeframesraw(frames)

    manifest_path = output_dir / "instruments.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as manifest_file:
        writer = csv.writer(manifest_file)
        writer.writerow(("bank", "program", "source_sample_index", "sample", "unity_key", "wav"))
        for (bank, program), source_index in sorted(instrument_samples.items()):
            sample_index = resolved_indices[(bank, program)]
            writer.writerow(
                (
                    bank,
                    program,
                    source_index,
                    sample_index,
                    samples[sample_index].unity_key,
                    f"{sample_index:03d}.wav",
                )
            )
    return len(referenced_samples)


def read_u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise ValueError(f"16-bit read outside ROM at 0x{offset:x}")
    return struct.unpack_from(">H", data, offset)[0]


def read_u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"32-bit read outside ROM at 0x{offset:x}")
    return struct.unpack_from(">I", data, offset)[0]


def signed_byte(value: int) -> int:
    return value - 256 if value >= 128 else value


def midi_pitch_wheel(semitones: float) -> int:
    """Map pitch offset to MIDI's wheel using a +/-18-semitone range."""
    return max(0, min(16383, round(8192 + semitones * (8192 / 18))))


def decode_track(
    data: bytes,
    start: int,
    channel: int,
    name: str,
    sample_unity_keys: dict[tuple[int, int], int] | None = None,
    unity_key_reference: int | None = None,
) -> tuple[Track, list[tuple[int, int]]]:
    track = Track(name=name, channel=channel)
    tempo_events: list[tuple[int, int]] = []
    pos = start
    tick = 0
    note_state = 0
    note_duration = 0xFF
    transpose = 0
    global_transpose = 0
    instrument_transpose = 0
    pitch_bend = 0
    portamento_rate = 0
    portamento_offset = 0.0
    portamento_target = 0.0
    midi_wheel = 8192
    midi_note_key: int | None = None
    tempo_ticks_per_iteration = 393
    frame_accumulator = 0.0
    vibrato_depth = 0
    lfo_rate = 0
    lfo_value = 0
    lfo_rising = True
    loops = [0] * 4
    loop_offsets = [0] * 4
    forever_offsets: set[int] = set()
    tied_key: int | None = None

    def emit_pitch(at: int) -> None:
        nonlocal midi_wheel
        vibrato_semitones = lfo_value / (65536 * 256)
        offset = pitch_bend / 256 + vibrato_semitones + portamento_offset
        wheel = midi_pitch_wheel(offset)
        if wheel != midi_wheel:
            track.add(at, 1, bytes((0xE0 | channel, wheel & 0x7F, (wheel >> 7) & 0x7F)))
            midi_wheel = wheel

    def note_on(at: int, key: int, glide_from_key: int | None = None) -> None:
        nonlocal midi_note_key, portamento_offset, portamento_target
        if 0 <= key <= 127:
            midi_note_key = key
            portamento_offset = float(glide_from_key - key) if glide_from_key is not None else 0.0
            portamento_target = 0.0
            emit_pitch(at)
            track.add(at, 2, bytes((0x90 | channel, key, NOTE_VELOCITY)))
            track.note_count += 1

    def tie_pitch(at: int, key: int) -> None:
        nonlocal portamento_offset, portamento_target
        if midi_note_key is None:
            note_on(at, key)
            return
        portamento_target = float(key - midi_note_key)
        if portamento_rate == 0:
            portamento_offset = portamento_target
        emit_pitch(at)

    def note_off(at: int, key: int, priority: int = 0) -> None:
        if 0 <= key <= 127:
            track.add(at, priority, bytes((0x80 | channel, key, 0)))

    def advance_time(delta: int) -> None:
        nonlocal frame_accumulator, lfo_value, lfo_rising, portamento_offset
        if delta <= 0:
            return
        # 256 internal timing units elapse per MIDI tick at the current tempo.
        frames_per_tick = 256 / tempo_ticks_per_iteration
        elapsed_frames = frame_accumulator + delta * frames_per_tick
        frame_count = int(elapsed_frames)
        depth_limit = VIBRATO_DEPTH_UNITS[vibrato_depth] << 16
        lfo_increment = VIBRATO_DEPTH_UNITS[vibrato_depth] * LFO_RATE_UNITS[lfo_rate]
        portamento_step = portamento_rate * 0.008

        for frame in range(frame_count):
            frame_tick = tick + (1 - frame_accumulator + frame) / frames_per_tick
            if depth_limit and lfo_increment:
                if lfo_rising:
                    if lfo_value >= depth_limit - lfo_increment:
                        lfo_value = depth_limit
                        lfo_rising = False
                    else:
                        lfo_value += lfo_increment
                else:
                    if lfo_value <= lfo_increment - depth_limit:
                        lfo_value = -depth_limit
                        lfo_rising = True
                    else:
                        lfo_value -= lfo_increment

            distance = portamento_target - portamento_offset
            if distance:
                change = min(abs(distance), portamento_step)
                portamento_offset += change if distance > 0 else -change

            emit_pitch(round(frame_tick))

        frame_accumulator = elapsed_frames - frame_count

    for _ in range(MAX_STEPS):
        if pos >= len(data):
            raise ValueError(f"track at 0x{start:x} ran past end of ROM")
        command_offset = pos
        status = data[pos]
        pos += 1

        if status >= 0x20:
            delta_index = ((status >> 5) & 7) - 1
            if note_state & 0x30 == 0:
                table = 1
            elif note_state & 0x10 == 0:
                table = 0
            else:
                note_state &= ~0x10
                table = 2
            if not 0 <= delta_index < 7:
                raise ValueError(f"invalid note duration selector at 0x{command_offset:x}")
            delta = DELTA_TABLE[table][delta_index]
            if status & 0x1F:
                key = (status & 0x1F) + OCTAVE_TABLE[note_state & 0x0F] - 1
                key += transpose + global_transpose + instrument_transpose
                duration = (delta * note_duration) >> 8
                is_tied = bool(note_state & 0x40)
                if is_tied:
                    if tied_key is None:
                        note_on(tick, key)
                    elif key != tied_key:
                        tie_pitch(tick, key)
                    tied_key = key
                else:
                    if tied_key is not None:
                        tie_pitch(tick, key)
                        if midi_note_key is not None:
                            note_off(tick + duration, midi_note_key)
                        tied_key = None
                    else:
                        note_on(tick, key)
                        note_off(tick + duration, key, 3 if duration == 0 else 0)
            advance_time(delta)
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
            note_state = (note_state & 0x97) | data[pos]
            pos += 1
        elif status == 0x05:
            tempo = read_u16(data, pos)
            pos += 2
            if tempo:
                tempo_ticks_per_iteration = tempo
                micros_per_quarter = round(((PPQN << 8) / tempo) / 62.5 * 1_000_000)
                tempo_events.append((tick, micros_per_quarter))
        elif status in (0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D):
            value = data[pos]
            pos += 1
            if status == 0x06:
                note_duration = value
            elif status == 0x07:
                volume = round(127 * VOLUME_TABLE[value] / 0x1FFF)
                track.add(tick, 1, bytes((0xB0 | channel, 7, min(volume, 127))))
            elif status == 0x08:
                bank, program = value >> 7, value & 0x7F
                if sample_unity_keys is not None:
                    unity_key = sample_unity_keys.get((bank, program))
                    instrument_transpose = (
                        unity_key_reference - unity_key
                        if unity_key is not None and unity_key_reference is not None
                        else 0
                    )
                track.add(tick, 1, bytes((0xB0 | channel, 0, bank)))
                track.add(tick, 1, bytes((0xB0 | channel, 32, 0)))
                track.add(tick, 1, bytes((0xC0 | channel, program)))
            elif status == 0x09:
                note_state = (note_state & 0xF8) | value
            elif status == 0x0A:
                global_transpose = signed_byte(value)
            elif status == 0x0B:
                transpose = signed_byte(value)
            elif status == 0x0C:
                pitch_bend = signed_byte(value)
            elif status == 0x0D:
                portamento_rate = value
        elif 0x0E <= status <= 0x11:
            loop_num = status - 0x0E
            if loops[loop_num] == 0 and loop_offsets[loop_num] == 0:
                loop_offsets[loop_num] = pos
                loops[loop_num] = data[pos]
                pos += 1
            elif loop_offsets[loop_num] != pos:
                raise ValueError(f"loop desynchronized in track at 0x{start:x}")
            else:
                loops[loop_num] -= 1
                pos += 1
            if data[pos] & 0x80:
                jump = pos + 2 + struct.unpack_from(">h", data, pos)[0]
                pos += 2
            else:
                jump_byte = data[pos]
                pos += 1
                jump = pos - jump_byte
            if loops[loop_num] == 0:
                loop_offsets[loop_num] = 0
            else:
                pos = jump
        elif 0x12 <= status <= 0x15:
            loop_num = status - 0x12
            if loops[loop_num] - 1 == 0:
                loops[loop_num] = 0
                loop_offsets[loop_num] = 0
                note_state = (note_state & 0x97) | data[pos]
                pos += 1
                jump = read_u16(data, pos)
                pos += 2
                pos += jump if jump < 0x8000 else jump - 0x10000
            else:
                pos += 3
        elif status == 0x16:
            if command_offset in forever_offsets:
                break
            forever_offsets.add(command_offset)
            jump = pos + 2 + struct.unpack_from(">h", data, pos)[0]
            pos = jump
        elif status == 0x17:
            break
        elif status == 0x18:
            pan = min(127, data[pos] * 4)
            if pan:
                pan -= 1
            track.add(tick, 1, bytes((0xB0 | channel, 10, pan)))
            pos += 1
        elif status in (0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23):
            if pos >= len(data):
                raise ValueError(f"missing command operand at 0x{command_offset:x}")
            value = data[pos]
            pos += 1
            if status == 0x1B:
                if value >= len(VIBRATO_DEPTH_UNITS):
                    raise ValueError(f"unknown vibrato depth index {value} at 0x{command_offset:x}")
                vibrato_depth = value
                if vibrato_depth == 0:
                    lfo_value = 0
                    lfo_rising = True
                    emit_pitch(tick)
            elif status == 0x1D:
                if value >= len(LFO_RATE_UNITS):
                    raise ValueError(f"unknown LFO rate index {value} at 0x{command_offset:x}")
                lfo_rate = value
            elif status == 0x1E:
                lfo_value = 0
                lfo_rising = True
                emit_pitch(tick)
            elif status == 0x1F:
                bank, program = 2 + (value >> 7), value & 0x7F
                if sample_unity_keys is not None:
                    unity_key = sample_unity_keys.get((bank, program))
                    instrument_transpose = (
                        unity_key_reference - unity_key
                        if unity_key is not None and unity_key_reference is not None
                        else 0
                    )
                track.add(tick, 1, bytes((0xB0 | channel, 0, bank)))
                track.add(tick, 1, bytes((0xB0 | channel, 32, 0)))
                track.add(tick, 1, bytes((0xC0 | channel, program)))
        else:
            raise ValueError(f"unknown command 0x{status:02x} at 0x{command_offset:x}")
    else:
        raise ValueError(f"track at 0x{start:x} exceeded {MAX_STEPS} commands")

    if tied_key is not None and midi_note_key is not None:
        note_off(tick, midi_note_key)
    if any(event[2][0] & 0xF0 == 0xE0 for event in track.events):
        # The largest tied-note interval is one octave; extra range accommodates bend plus vibrato.
        for controller, value in ((101, 0), (100, 0), (6, 18), (38, 0), (101, 127), (100, 127)):
            track.add(0, -1, bytes((0xB0 | channel, controller, value)))
    return track, tempo_events


def variable_length(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative MIDI delta time")
    buffer = value & 0x7F
    result = bytearray()
    while value >> 7:
        value >>= 7
        buffer <<= 8
        buffer |= (value & 0x7F) | 0x80
    while True:
        result.append(buffer & 0xFF)
        if buffer & 0x80:
            buffer >>= 8
        else:
            return bytes(result)


def make_midi_track(events: list[tuple[int, int, bytes]], track_name: str) -> bytes:
    name = track_name.encode("ascii", "replace")
    ordered = [(0, -2, b"\xFF\x03" + variable_length(len(name)) + name), *events]
    ordered.sort(key=lambda event: (event[0], event[1]))
    payload = bytearray()
    previous_tick = 0
    for tick, _priority, message in ordered:
        payload.extend(variable_length(tick - previous_tick))
        payload.extend(message)
        previous_tick = tick
    payload.extend(b"\x00\xFF\x2F\x00")
    return b"MTrk" + struct.pack(">I", len(payload)) + payload


def midi_file(tracks: list[Track], tempo_events: list[tuple[int, int]], title: str) -> bytes:
    conductor_events: list[tuple[int, int, bytes]] = []
    conductor_events.append(
        (0, -1, b"\xFF\x01" + variable_length(len(title.encode("ascii", "replace"))) + title.encode("ascii", "replace"))
    )
    seen_tempos: set[tuple[int, int]] = set()
    for tick, tempo in sorted(tempo_events):
        if (tick, tempo) in seen_tempos:
            continue
        seen_tempos.add((tick, tempo))
        payload = b"\xFF\x51\x03" + tempo.to_bytes(3, "big")
        conductor_events.append((tick, -1, payload))
    events = list(conductor_events)
    for track in tracks:
        events.extend(track.events)
    chunk = make_midi_track(events, title)
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQN)
    return header + chunk


def safe_filename(title: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    return value or "track"


def convert_game(
    xml_path: Path,
    output_dir: Path | None = None,
    unity_key_reference: int | None = None,
) -> None:
    if unity_key_reference is not None and not 0 <= unity_key_reference <= 127:
        raise ValueError("unity key reference must be between 0 and 127")
    metadata = read_game_metadata(xml_path)
    data = load_code_rom(metadata)
    wav_count = 0
    if metadata.pcm_roms:
        if (
            metadata.instrument_table_offset is not None
            and metadata.instrument_banks is not None
            and metadata.sample_table_offset is not None
            and metadata.sample_table_length is not None
        ):
            pcm_data = load_pcm_rom(metadata)
            wav_count = write_qsound_wavs(
                data,
                pcm_data,
                metadata,
                WAV_DIR / metadata.archive,
            )
            print(
                f"Generated {wav_count} QSound sample WAV files "
                f"and instrument map in {WAV_DIR / metadata.archive}"
            )
        else:
            print(
                f"Skipping QSound WAV extraction for {metadata.archive}: "
                "instrument/sample table metadata is incomplete"
            )
    sample_unity_keys = (
        load_sample_unity_keys(data, metadata) if unity_key_reference is not None else None
    )
    first_sequence = 0
    for offset in range(SEQUENCE_TABLE, SEQUENCE_TABLE + 0x4000, 4):
        first_sequence = read_u32(data, offset) & 0x0FFFFF
        if first_sequence:
            break
    if first_sequence <= SEQUENCE_TABLE:
        raise ValueError("could not determine the sequence table length")
    table_length = first_sequence - SEQUENCE_TABLE

    output_dir = output_dir or MIDI_DIR / metadata.archive
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = 0
    total_notes = 0
    for code, title in metadata.titles.items():
        pointer_offset = SEQUENCE_TABLE + code * 4
        if pointer_offset + 4 > SEQUENCE_TABLE + table_length:
            raise ValueError(f"song code 0x{code:02x} is outside the sequence table")
        song_offset = read_u32(data, pointer_offset) & 0x0FFFFF
        if song_offset == 0:
            raise ValueError(f"song code 0x{code:02x} has no sequence")
        flags = data[song_offset]
        if flags & 0x80:
            raise ValueError(f"song code 0x{code:02x} has an invalid sequence header")

        tracks: list[Track] = []
        tempo_events: list[tuple[int, int]] = []
        for slot in range(16):
            relative_offset = read_u16(data, song_offset + 1 + slot * 2)
            if relative_offset == 0:
                continue
            track_start = song_offset + relative_offset
            track, tempos = decode_track(
                data,
                track_start,
                slot,
                f"Track {slot + 1:02d}",
                sample_unity_keys,
                unity_key_reference,
            )
            tracks.append(track)
            tempo_events.extend(tempos)

        filename = f"{code:02x}_{safe_filename(title)}.mid"
        (output_dir / filename).write_bytes(midi_file(tracks, tempo_events, title))
        generated += 1
        total_notes += sum(track.note_count for track in tracks)
        print(f"{filename}: {len(tracks)} tracks, {sum(t.note_count for t in tracks)} notes")

    print(f"Generated {generated} MIDI files ({total_notes} note-ons) in {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_xml", type=Path, help="game metadata XML file")
    parser.add_argument("--output-dir", type=Path, help="output directory (default: cps2/midi/<archive>)")
    parser.add_argument(
        "--unity-key-reference",
        type=int,
        default=None,
        metavar="MIDI_NOTE",
        help="enable per-instrument unity-key transposition to this MIDI note",
    )
    arguments = parser.parse_args()
    convert_game(arguments.game_xml, arguments.output_dir, arguments.unity_key_reference)
