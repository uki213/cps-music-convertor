# -*- coding: utf-8 -*-
"""Dump the PCM program->sample->note mapping used for sf2ce, and verify
each sample's identity against the WAV folder (which the SF2 was built from)."""
import sys
sys.path.insert(0, r"d:\works\cps-music-convertor\cps1")
import convert_to_midi as c
from pathlib import Path

game = c.read_game(Path(r"d:\works\cps-music-convertor\cps1\games\sf2ce.xml"))
print("version:", game.version, "tables_offset:", hex(game.tables_offset))
code = c.load_rom(game)
pcm_rom = c.load_pcm_rom(game)
samples = c.decode_pcm_samples(pcm_rom)
print(f"decoded {len(samples)} PCM samples, indices: {sorted(samples.keys())}")
mapping = c.build_pcm_notes_by_program(code, game.tables_offset, samples, game.version)
print("program -> MIDI note mapping (sf2ce):")
for prog in sorted(mapping):
    print(f"  prog {prog:3d} -> note {mapping[prog]:3d}")
# also show what note each sample_index maps to
notes_by_sample = {si: 24 + i for i, si in enumerate(sorted(samples))}
print("\nsample_index -> note (sorted):")
for si in sorted(samples):
    print(f"  sample {si:2d} -> note {notes_by_sample[si]:3d}  ({len(samples[si])//2} frames)")
