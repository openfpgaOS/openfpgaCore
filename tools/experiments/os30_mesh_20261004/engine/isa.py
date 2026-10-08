"""Private mesh processor ISA; 32-bit scratch and bank words, 10-bit code PC (768 words)."""
OP = dict(HALT=0, LI=1, LUI=2, ORI=3, MOV=4, ADD=5, SUB=6,
          AND=7, OR=8, XOR=9, SHL=10, SHR=11, SAR=12, ADDI=13,
          MUL=14, MAC=15, ACCLO=16, ACCHI=17, ACCSAR=18,
          ACCSET=19, DIVU=20, DIVS=21, LOAD=22, STORE=23,
          OUT=24, JMP=25, BR=26, CMP=27, ADDC=28, SUBB=29,
          JAL=30, JREG=31, SEXT=32, DIVW=33, REM=34,
          ACCADD=35, ACCSUB=36, SAT=37, NEG=38, ACCSHL=39, PLANE=40, SLOAD=41, SSTORE=42, EDGE=43, MATROW=44, DOT3=45, NORM=46, PROJ=47, VERTEX=48, COPY=49, GATHER=50, PROJA=51, ATTR=52, SCOPY=53, PLANES=54, OUTBLOCK=55, SETUP=56)
COND = dict(EQ=0, NE=1, LT=2, GE=3, LTU=4, GEU=5, C=6, NC=7)

def ins(op, d=0, a=0, b=0):
    return (OP[op] << 24) | ((d & 255) << 16) | ((a & 255) << 8) | (b & 255)

def imm(op, d, value):
    return (OP[op] << 24) | ((d & 255) << 16) | (value & 65535)

class Program:
    def __init__(self):
        self.words, self.labels, self.fixups = [], {}, []
    def emit(self, op, d=0, a=0, b=0):
        self.words.append(ins(op, d, a, b)); return self
    def immediate(self, op, d, value):
        self.words.append(imm(op, d, value)); return self
    def const(self, d, value):
        value &= 0xffffffff
        if value < 0x8000 or value >= 0xffff8000:
            return self.immediate('LI', d, value)
        self.immediate('LUI', d, value >> 16)
        if value & 65535: self.immediate('ORI', d, value)
        return self
    def label(self, name):
        assert name not in self.labels, name
        self.labels[name] = len(self.words); return self
    def jump(self, target, cond=None, link=None):
        op = 'JAL' if link is not None else 'BR' if cond is not None else 'JMP'
        d = link if link is not None else COND[cond] if cond is not None else 0
        self.fixups.append((len(self.words), target))
        self.words.append(ins(op, d)); return self
    def finish(self):
        words = self.words[:]
        for pc, name in self.fixups: words[pc] |= self.labels[name]
        assert len(words) <= 768, len(words)
        return words
