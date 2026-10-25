#!/usr/bin/env python3
"""Minimal Z80 disassembler for investigating CPS-1 sound-driver ROMs."""
import sys

def _reg(op):
    r = op & 0x07
    g = (op & 0x18) >> 3
    regs = ("B","C","D","E","H","L","(HL)","A")
    return regs[r] if g in (0,1,2) else regs[r] if g==3 else regs[r]

def disasm(data, base=0, max_lines=200):
    out = []
    i = 0
    n = len(data)
    while i < n and len(out) < max_lines:
        addr = base + i
        b = data[i]
        # CB prefix
        if b == 0xCB:
            c = data[i+1] if i+1 < n else 0
            # rotate/bit group
            rot = ("RLC","RRC","RL","RR","SLA","SRA","SLL","SRR")[c>>3 & 7]
            r = ("B","C","D","E","H","L","(HL)","A")[c&7]
            out.append("%04X:  %s %s" % (addr, rot, r))
            i += 2
            continue
        # basic
        b3 = b >> 5
        # LD r,r
        if 0x40 <= b <= 0x7F and (b & 0x07) != 7 and b & 0x38:
            g=(b>>3)&7
            if g in (0,1,2):
                dst=(b>>3)&7 if g!=3 else 7
                src=b&7
                rr=("B","C","D","E","H","L","(HL)","A")
                if (b>>3)&3 == 3:
                    # LD (HL),r / LD r,(HL) / LD (HL),A / LD A,(HL)
                    if b&7==7 and b>=0x70: pass
                if (b & 0x38) == 0x08 or (b & 0x38)==0x18 or (b&0x38)==0x28 or (b&0x38)==0x38:
                    # memory operand group
                    if b & 0x07 == 0x07:
                        out.append("%04X:  LD %s,(HL)" % (addr, rr[(b>>3)&7]) if (b>>3)&7==7 else "%04X:  LD (HL),%s" % (addr, rr[b&7]))
                        out[-1] = "%04X:  %s" % (addr, ("LD (HL),B","LD (HL),C","LD (HL),D","LD (HL),E","LD (HL),H","LD (HL),L","LD (HL),A","LD A,(HL)")[b&0x38>>3==0 and 0 or 0] if False else "%04X:  (cb?)"%addr
                        i += 1
                        continue
                # simple register LD
                rr=("B","C","D","E","H","L","(HL)","A")
                if g in (0,1,2):
                    out.append("%04X:  LD %s,%s" % (addr, rr[(b>>3)&7], rr[b&7]))
                    i += 1
                    continue
        # ALU r
        if 0x80 <= b <= 0xBF:
            rr=("B","C","D","E","H","L","(HL)","A")
            opname=("ADD","ADC","SUB","SBC","AND","XOR","OR","CP")[(b>>3)&7]
            if (b>>3)&7 in (0,1,2):
                out.append("%04X:  %s %s,%s" % (addr, opname, rr[(b>>3)&7], rr[b&7]))
                i += 1
                continue
        # ALU A,#
        if b in (0xC6,0xCE,0xD6,0xE6,0xEE,0xF6,0xFE):
            opname="ADD A," if b==0xC6 else "ADC A," if b==0xCE else "SUB " if b==0xD6 else "AND " if b==0xE6 else "XOR " if b==0xEE else "OR " if b==0xF6 else "CP "
            out.append("%04X:  %s %02X" % (addr, opname, data[i+1]))
            i += 2
            continue
        # LD r,#
        if b in (0x06,0x0E,0x16,0x1E,0x26,0x2E,0x3E):
            rr=("B","C","D","E","H","L","A")[(b-0x06)//8]
            out.append("%04X:  LD %s,%02X" % (addr, rr, data[i+1]))
            i += 2
            continue
        # LD (HL),#
        if b == 0x36:
            out.append("%04X:  LD (HL),%02X" % (addr, data[i+1]))
            i += 2
            continue
        # LD rr,#  (16-bit)
        if b in (0x01,0x11,0x21,0x31):
            rr=("BC","DE","HL","SP")[(b-0x01)//8]
            out.append("%04X:  LD %s,%04X" % (addr, rr, int.from_bytes(data[i+1:i+3],'little')))
            i += 3
            continue
        # 16-bit abs / 16-bit rel
        if b in (0xC3,0xC2,0xCA,0xC4,0xCC,0xCD,0xD2,0xD4,0xDC,0xE4,0xEC,0xF4,0xFC):
            name={0xC3:"JP",0xC2:"RET Z",0xCA:"CALL Z",0xC4:"CALL NZ",0xCC:"CALL C",0xCD:"CALL",
                  0xD2:"RET C",0xD4:"CALL NC",0xDC:"CALL NC",0xE4:"CALL PO",0xEC:"CALL P",
                  0xF4:"CALL PE",0xFC:"CALL M"}[b]
            out.append("%04X:  %s %04X" % (addr, name, int.from_bytes(data[i+1:i+3],'little')))
            i += 3
            continue
        if b in (0x2A,0x32,0x3A,0xE0,0xF0):
            name={0x2A:"LD HL,(..)",0x32:"LD(..),A",0x3A:"LD A,(..)",0xE0:"LD(..),A",0xF0:"LD A,(..)"}[b]
            out.append("%04X:  %s %04X" % (addr, name, int.from_bytes(data[i+1:i+3],'little')))
            i += 3
            continue
        # 8-bit rel
        if b in (0x10,0x20,0x28,0x30,0x38):
            name={0x10:"DJNZ",0x20:"JR",0x28:"JR Z",0x30:"JR NC",0x38:"JR NZ"}[b]
            d=data[i+1]; d = d-256 if d>127 else d
            out.append("%04X:  %s +%d (to %04X)" % (addr, name, d, addr+2+d))
            i += 2
            continue
        # 16-bit rel (RST? no) E8/F8
        if b in (0xE8,0xF8):
            name="ADD SP," if b==0xE8 else "ADD HL,SP+"
            d=data[i+1]; d=d-256 if d>127 else d
            out.append("%04X:  %s %+d" % (addr, name, d))
            i += 2
            continue
        # 16-bit inc/dec, push, pop, call rel, ret
        if b in (0x03,0x0B,0x13,0x1B,0x23,0x2B,0x33,0x3B):
            rr=("BC","BC","DE","DE","HL","HL","SP","SP")[(b-0x03)//8]
            out.append("%04X:  %s %s" % (addr, "INC" if b in (0x03,0x13,0x23,0x33) else "DEC", rr))
            i += 1
            continue
        if b in (0x04,0x0C,0x14,0x1C,0x24,0x2C,0x34,0x3C):
            rr=("B","C","D","E","H","L","(HL)","A")[(b-0x04)//8]
            out.append("%04X:  INC %s" % (addr, rr))
            i += 1
            continue
        if b in (0x05,0x0D,0x15,0x1D,0x25,0x2D,0x35,0x3D):
            rr=("B","C","D","E","H","L","(HL)","A")[(b-0x05)//8]
            out.append("%04X:  DEC %s" % (addr, rr))
            i += 1
            continue
        if b in (0xC5,0xD5,0xE5,0xF5):
            rr=("BC","DE","HL","AF")[(b-0xC5)//8]
            out.append("%04X:  PUSH %s" % (addr, rr))
            i += 1
            continue
        if b in (0xC1,0xD1,0xE1,0xF1):
            rr=("BC","DE","HL","AF")[(b-0xC1)//8]
            out.append("%04X:  POP %s" % (addr, rr))
            i += 1
            continue
        if b in (0xC0,0xC8,0xD0,0xD8,0xC9,0xD9):
            name={0xC0:"RET NZ",0xC8:"RET Z",0xD0:"RET NC",0xD8:"RET C",0xC9:"RET",0xD9:"EXX"}[b]
            out.append("%04X:  %s" % (addr, name))
            i += 1
            continue
        if b in (0x90,0x99,0x9A,0x9B,0x9C,0x9D,0x9E,0x9F,0xB0,0xB9,0xBA,0xBB,0xBC,0xBD,0xBE,0xBF,
                 0x88,0x89,0x8A,0x8B,0x8C,0x8D,0x8E,0x8F,0xA8,0xA9,0xAA,0xAB,0xAC,0xAD,0xAE,0xAF,
                 0x80,0x81,0x82,0x83,0x84,0x85,0x87,0x97,0xA0,0xA7,0xB7,0xC0):
            rr=("B","C","D","E","H","L","(HL)","A")
            grp=(b>>4)&1
            out.append("%04X:  (alu) %02X" % (addr, b))
            i += 1
            continue
        # single byte misc
        name = {0x00:"NOP",0x07:"RLCA",0x0F:"RRCA",0x17:"RLA",0x1F:"RRA",0x27:"DAA",
                0x2F:"CPL",0x37:"SCF",0x3F:"CCF",0x77:"LD (HL),A",0x7E:"LD A,(HL)",
                0xE9:"JP (HL)",0xFF:"RST 18"}
        if b in name:
            out.append("%04X:  %s" % (addr, name[b])); i += 1; continue
        if 0xC7 in (b,) or b in (0xCF,0xD7,0xDF,0xE7,0xF7):
            out.append("%04X:  RST %d" % (addr, (b&7)*8)); i += 1; continue
        out.append("%04X:  .db %02X" % (addr, b)); i += 1
    return out

if __name__ == "__main__":
    path, start, count = sys.argv[1], int(sys.argv[2],0), int(sys.argv[3],0)
    data = open(path,'rb').read()
    for line in disasm(data[start:start+count], start):
        print(line)
