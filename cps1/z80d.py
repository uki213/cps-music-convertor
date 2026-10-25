#!/usr/bin/env python3
"""Minimal Z80 disassembler for investigating CPS-1 sound-driver ROMs."""
import sys

RR = ("B", "C", "D", "E", "H", "L", "(HL)", "A")
RR16LD = {0x01: "BC", 0x11: "DE", 0x21: "HL", 0x31: "SP"}
RR16PUSH = {0xC5: "BC", 0xD5: "DE", 0xE5: "HL", 0xF5: "AF"}
RR16POP = {0xC1: "BC", 0xD1: "DE", 0xE1: "HL", 0xF1: "AF"}
RR16INCD = {0x03: "BC", 0x0B: "BC", 0x13: "DE", 0x1B: "DE", 0x23: "HL", 0x2B: "HL", 0x33: "SP", 0x3B: "SP"}
RR8INC = {0x04: "B", 0x0C: "C", 0x14: "D", 0x1C: "E", 0x24: "H", 0x2C: "L", 0x34: "(HL)", 0x3C: "A"}
RR8DEC = {0x05: "B", 0x0D: "C", 0x15: "D", 0x1D: "E", 0x25: "H", 0x2D: "L", 0x35: "(HL)", 0x3D: "A"}
RR8LD = {0x06: "B", 0x0E: "C", 0x16: "D", 0x1E: "E", 0x26: "H", 0x2E: "L", 0x3E: "A"}


def _dst(op):
    r = (op >> 3) & 7
    return RR[r] if r != 3 else RR[op & 7]


def disasm(data, base=0, max_lines=500):
    out = []
    i = 0
    n = len(data)
    while i < n and len(out) < max_lines:
        addr = base + i
        b = data[i]
        if b == 0xCB:
            if i + 1 >= n:
                break
            c = data[i + 1]
            rot = ("RLC", "RRC", "RL", "RR", "SLA", "SRA", "SLL", "SRR")[c >> 3 & 7]
            out.append("%04X:  %s %s" % (addr, rot, RR[c & 7]))
            i += 2
            continue
        if 0x40 <= b <= 0x7F:
            g = (b >> 3) & 7
            r = b & 7
            if g in (0, 1, 2):
                out.append("%04X:  LD %s,%s" % (addr, RR[g], RR[r]))
            elif g == 3:
                if r == 7:
                    out.append("%04X:  LD (HL),A" % addr)
                else:
                    out.append("%04X:  LD A,(%s)" % (addr, RR[r]))
            i += 1
            continue
        if 0x80 <= b <= 0xBF:
            opname = ("ADD", "ADC", "SUB", "SBC", "AND", "XOR", "OR", "CP")[(b >> 3) & 7]
            g = (b >> 3) & 7
            dst = RR[g] if g in (0, 1, 2, 3) else RR[b & 7]
            if g in (0, 1, 2):
                dst = RR[g]
            out.append("%04X:  %s %s,%s" % (addr, opname, dst, RR[b & 7]))
            i += 1
            continue
        if b in (0xC6, 0xCE, 0xD6, 0xE6, 0xEE, 0xF6, 0xFE):
            opname = {0xC6: "ADD A,", 0xCE: "ADC A,", 0xD6: "SUB", 0xE6: "AND",
                      0xEE: "XOR", 0xF6: "OR", 0xFE: "CP"}[b]
            out.append("%04X:  %s %02X" % (addr, opname, data[i + 1]))
            i += 2
            continue
        if b in (0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x3E):
            out.append("%04X:  LD %s,%02X" % (addr, RR[(b - 0x06) // 8], data[i + 1]))
            i += 2
            continue
        if b == 0x36:
            out.append("%04X:  LD (HL),%02X" % (addr, data[i + 1]))
            i += 2
            continue
        if b in RR16LD:
            out.append("%04X:  LD %s,%04X" % (addr, RR16LD[b], int.from_bytes(data[i + 1:i + 3], 'little')))
            i += 3
            continue
        if b in (0xC3, 0xC2, 0xCA, 0xC4, 0xCC, 0xCD, 0xD2, 0xD4, 0xDC,
                 0xE4, 0xEC, 0xF4, 0xFC, 0x2A, 0x32, 0x3A, 0xE0, 0xF0):
            name = {0xC3: "JP", 0xC2: "RET Z", 0xCA: "CALL Z", 0xC4: "CALL NZ",
                    0xCC: "CALL C", 0xCD: "CALL", 0xD2: "RET C", 0xD4: "CALL NC",
                    0xDC: "CALL NC", 0xE4: "CALL PO", 0xEC: "CALL P", 0xF4: "CALL PE",
                    0xFC: "CALL M", 0x2A: "LD HL,(..)", 0x32: "LD(..),A",
                    0x3A: "LD A,(..)", 0xE0: "LD(..),A", 0xF0: "LD A,(..)"}[b]
            out.append("%04X:  %s %04X" % (addr, name, int.from_bytes(data[i + 1:i + 3], 'little')))
            i += 3
            continue
        if b in (0x10, 0x20, 0x28, 0x30, 0x38):
            name = {0x10: "DJNZ", 0x20: "JR", 0x28: "JR Z", 0x30: "JR NC", 0x38: "JR NZ"}[b]
            d = data[i + 1]
            d = d - 256 if d > 127 else d
            out.append("%04X:  %s %04X" % (addr, name, addr + 2 + d))
            i += 2
            continue
        if b in (0xE8, 0xF8):
            name = "ADD SP," if b == 0xE8 else "ADD HL,SP+"
            d = data[i + 1]
            d = d - 256 if d > 127 else d
            out.append("%04X:  %s %+d" % (addr, name, d))
            i += 2
            continue
        if b in RR16INCD:
            out.append("%04X:  %s %s" % (addr, "INC" if b in (0x03, 0x13, 0x23, 0x33) else "DEC", RR16INCD[b]))
            i += 1
            continue
        if b in RR8INC:
            out.append("%04X:  INC %s" % (addr, RR8INC[b]))
            i += 1
            continue
        if b in RR8DEC:
            out.append("%04X:  DEC %s" % (addr, RR8DEC[b]))
            i += 1
            continue
        if b in RR16PUSH:
            out.append("%04X:  PUSH %s" % (addr, RR16PUSH[b]))
            i += 1
            continue
        if b in RR16POP:
            out.append("%04X:  POP %s" % (addr, RR16POP[b]))
            i += 1
            continue
        if b in (0xC0, 0xC8, 0xD0, 0xD8, 0xC9, 0xD9, 0x09, 0x19, 0x29, 0x39, 0x08,
                 0x00, 0x07, 0x0F, 0x17, 0x1F, 0x27, 0x2F, 0x37, 0x3F, 0xE9, 0x7F):
            name = {0xC0: "RET NZ", 0xC8: "RET Z", 0xD0: "RET NC", 0xD8: "RET C",
                    0xC9: "RET", 0xD9: "EXX", 0x09: "ADD HL,BC", 0x19: "ADD HL,DE",
                    0x29: "ADD HL,HL", 0x39: "ADD HL,SP", 0x08: "EX AF,AF'",
                    0x00: "NOP", 0x07: "RLCA", 0x0F: "RRCA", 0x17: "RLA", 0x1F: "RRA",
                    0x27: "DAA", 0x2F: "CPL", 0x37: "SCF", 0x3F: "CCF", 0xE9: "JP (HL)",
                    0x7F: "LD A,A"}[b]
            out.append("%04X:  %s" % (addr, name))
            i += 1
            continue
        if b in (0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xF7):
            out.append("%04X:  RST %d" % (addr, (b & 7) * 8))
            i += 1
            continue
        out.append("%04X:  .db %02X" % (addr, b))
        i += 1
    return out


if __name__ == "__main__":
    path, start, count = sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3], 0)
    data = open(path, 'rb').read()
    for line in disasm(data[start:start + count], start):
        print(line)
