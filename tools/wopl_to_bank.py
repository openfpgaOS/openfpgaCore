#!/usr/bin/env python3
"""Convert a Wohlstand WOPL3 bank into openfpgaOS of_midi.c 4-op tables.

Input:  tools/banks/GM_4op_nguyen.wopl (WOPL v3, 1 melodic + 1 percussion bank)
Output: stdout — C tables for gm_bank4 (128 melodic) + gm_bank4_drum (47 drums
        GM 35..81) + gm_drum_pk (per-drum OPL3 note offset from WOPL
        percussion_key_number field).

WOPL instrument record (v3, 66 bytes):
  [ 0..31]  inst_name (32 bytes, null-terminated)
  [32..33]  note_offset1 (int16 BE)
  [34..35]  note_offset2 (int16 BE, pseudo-4op only)
  [36]      midi_velocity_offset (int8)
  [37]      second_voice_detune  (int8)
  [38]      percussion_key_number (uint8 — OPL3 play note for fixed-pitch)
  [39]      inst_flags (bit0=4op, bit1=pseudo4op, bit2=blank, bit6=fixed)
  [40]      fb_conn1_C0 — FB/CNT for op pair 1 (first 2 operators)
  [41]      fb_conn2_C0 — CNT for op pair 2 (4-op only)
  [42..46]  operators[0] = slot0 carrier  (avekf_20, ksl_l_40, atdec_60, susrel_80, waveform_E0)
  [47..51]  operators[1] = slot0 modulator
  [52..56]  operators[2] = slot1 carrier  (4-op ops 3,4)
  [57..61]  operators[3] = slot1 modulator
  [62..63]  delay_on_ms (v3)
  [64..65]  delay_off_ms (v3)

We re-order operators into modulator-first for easier firmware writes:
  op1 = operators[1] (mod pair 1)
  op2 = operators[0] (car pair 1)
  op3 = operators[3] (mod pair 2)
  op4 = operators[2] (car pair 2)

Output record (25 bytes per instrument):
  [ 0] flags  (bit0=4op, bit6=fixed)
  [ 1] note_offset1  (int8)
  [ 2] fb_cnt1   (C0 reg value, low nibble = FB/CNT; L/R bits 4,5 set at load time)
  [ 3] fb_cnt2   (4-op secondary C0 — only low nibble's CNT2 matters)
  [ 4.. 8] op1 (mod pair 1):  20,40,60,80,E0
  [ 9..13] op2 (car pair 1):  20,40,60,80,E0
  [14..18] op3 (mod pair 2):  20,40,60,80,E0  (4-op only; else zero)
  [19..23] op4 (car pair 2):  20,40,60,80,E0  (4-op only; else zero)
  [24] percussion_key_number (0 if melodic)
"""
import struct, sys, os

BANK_PATH = os.path.join(os.path.dirname(__file__), 'banks', 'DMXOPL3_GS.wopl')

GM_PROG_NAMES = [
    "Acoustic Grand Piano","Bright Acoustic Piano","Electric Grand Piano","Honky-tonk Piano",
    "Electric Piano 1","Electric Piano 2","Harpsichord","Clavinet",
    "Celesta","Glockenspiel","Music Box","Vibraphone","Marimba","Xylophone","Tubular Bells","Dulcimer",
    "Drawbar Organ","Percussive Organ","Rock Organ","Church Organ","Reed Organ","Accordion","Harmonica","Tango Accordion",
    "Nylon Guitar","Steel Guitar","Jazz Guitar","Clean Guitar","Muted Guitar","Overdriven Guitar","Distortion Guitar","Guitar Harmonics",
    "Acoustic Bass","Electric Bass (finger)","Electric Bass (pick)","Fretless Bass","Slap Bass 1","Slap Bass 2","Synth Bass 1","Synth Bass 2",
    "Violin","Viola","Cello","Contrabass","Tremolo Strings","Pizzicato Strings","Orchestral Harp","Timpani",
    "String Ensemble 1","String Ensemble 2","Synth Strings 1","Synth Strings 2","Choir Aahs","Voice Oohs","Synth Voice","Orchestra Hit",
    "Trumpet","Trombone","Tuba","Muted Trumpet","French Horn","Brass Section","Synth Brass 1","Synth Brass 2",
    "Soprano Sax","Alto Sax","Tenor Sax","Baritone Sax","Oboe","English Horn","Bassoon","Clarinet",
    "Piccolo","Flute","Recorder","Pan Flute","Blown Bottle","Shakuhachi","Whistle","Ocarina",
    "Square Lead","Saw Lead","Calliope Lead","Chiff Lead","Charang Lead","Voice Lead","Fifths Lead","Bass+Lead",
    "New Age Pad","Warm Pad","Polysynth Pad","Choir Pad","Bowed Pad","Metallic Pad","Halo Pad","Sweep Pad",
    "Rain FX","Soundtrack FX","Crystal FX","Atmosphere FX","Brightness FX","Goblins FX","Echoes FX","Sci-Fi FX",
    "Sitar","Banjo","Shamisen","Koto","Kalimba","Bag Pipe","Fiddle","Shanai",
    "Tinkle Bell","Agogo","Steel Drums","Woodblock","Taiko Drum","Melodic Tom","Synth Drum","Reverse Cymbal",
    "Guitar Fret Noise","Breath Noise","Seashore","Bird Tweet","Telephone Ring","Helicopter","Applause","Gunshot",
]

DRUM_NAMES = [
    "Acoustic Bass Drum","Bass Drum 1","Side Stick","Acoustic Snare","Hand Clap",
    "Electric Snare","Low Floor Tom","Closed Hi-Hat","High Floor Tom","Pedal Hi-Hat",
    "Low Tom","Open Hi-Hat","Low-Mid Tom","Hi-Mid Tom","Crash Cymbal 1",
    "High Tom","Ride Cymbal 1","Chinese Cymbal","Ride Bell","Tambourine",
    "Splash Cymbal","Cowbell","Crash Cymbal 2","Vibraslap","Ride Cymbal 2",
    "Hi Bongo","Low Bongo","Mute Hi Conga","Open Hi Conga","Low Conga",
    "High Timbale","Low Timbale","High Agogo","Low Agogo","Cabasa",
    "Maracas","Short Whistle","Long Whistle","Short Guiro","Long Guiro",
    "Claves","Hi Wood Block","Low Wood Block","Mute Cuica","Open Cuica",
    "Mute Triangle","Open Triangle",
]

F_4OP       = 0x01
F_PSEUDO4OP = 0x02
F_BLANK     = 0x04
F_FIXED     = 0x40

# Output record flag bits (firmware-side)
OUT_PSEUDO4OP = 0x01   # bit0: load voice1+voice2 onto two independent 2-op channels
OUT_BLANK     = 0x02   # bit1: silent patch
OUT_FIXED     = 0x40   # bit6: fixed-pitch (drum)

def parse_wopl(path):
    """Parse a WOPL3 bank and return (melodic[128], drums[128]) for the
    standard GM bank entries (msb=0, lsb=0). Multi-bank GS files store
    SC-88 / SC-8850 variants in additional bank slots; we always use the
    first melodic and first percussion bank, which by Wohlstand convention
    is the GM-compatible one. """
    with open(path, 'rb') as f:
        d = f.read()
    if d[:11] != b'WOPL3-BANK\0':
        raise RuntimeError("not a WOPL3-BANK file")
    version = struct.unpack('<H', d[11:13])[0]
    mel_banks = struct.unpack('>H', d[13:15])[0]
    per_banks = struct.unpack('>H', d[15:17])[0]
    if mel_banks < 1 or per_banks < 1:
        raise RuntimeError(f"need >=1 melodic and >=1 percussion bank, got {mel_banks}/{per_banks}")
    off = 19
    if version >= 2:
        off += 34 * (mel_banks + per_banks)  # all bank-metadata entries
    ins_size = 66 if version > 2 else 62

    # Bank instrument blocks are laid out as:
    #   [mel bank 0][mel bank 1]...[mel bank N-1][per bank 0][per bank 1]...
    # Each bank is 128 instruments. We pick the first of each.
    mel0_off = off
    per0_off = off + mel_banks * 128 * ins_size
    melodic = [d[mel0_off + i * ins_size : mel0_off + (i + 1) * ins_size]
               for i in range(128)]
    drums   = [d[per0_off + i * ins_size : per0_off + (i + 1) * ins_size]
               for i in range(128)]
    return melodic, drums

def _clamp_i8(x):
    if x >  127: return 127
    if x < -128: return -128
    return x

def convert_record(rec):
    note_offset1 = struct.unpack('>h', rec[32:34])[0]
    note_offset2 = struct.unpack('>h', rec[34:36])[0]
    # second_voice_detune is a signed byte at rec[37]; libADLMIDI maps it
    # to (sv/64) semitones. DMXOPL3-GS uses extreme values like -124
    # (≈ -1.94 semitones) to create the chorus-detune between the two
    # stacked voices. Without folding this into note_offset2, voice 2
    # plays at an exact integer interval from voice 1 — often a dissonant
    # minor 2nd or similar — and the stack becomes pure noise. Round to
    # the nearest semitone (our note_to_freq is integer-only) and bake
    # it into note_offset2 at conversion time so the engine stays simple.
    sv_det = struct.unpack('b', rec[37:38])[0]
    if sv_det >= 0:
        detune_semis = (sv_det + 32) // 64
    else:
        detune_semis = -(((-sv_det) + 32) // 64)
    note_offset2 += detune_semis
    note_offset1 = _clamp_i8(note_offset1)
    note_offset2 = _clamp_i8(note_offset2)
    pk = rec[38]
    flags = rec[39]
    fb1 = rec[40]
    fb2 = rec[41]
    # WOPL operator order: operators[0]=CARRIER1, [1]=MODULATOR1,
    # [2]=CARRIER2, [3]=MODULATOR2. We re-emit modulator-first per pair
    # (matches the way our load_instrument_2op writes operator regs):
    #   op1 = mod of voice 1, op2 = car of voice 1
    #   op3 = mod of voice 2, op4 = car of voice 2
    def op(idx):
        base = 42 + idx * 5
        return rec[base:base + 5]
    op_car1 = op(0); op_mod1 = op(1); op_car2 = op(2); op_mod2 = op(3)

    # Firmware flags. WOPL marks "pseudo 4-op" with both bits 0 and 1
    # set; we treat any 4-op flag as pseudo (= load voice1 + voice2 on
    # two independent 2-op channels) since DMXOPL3-GS has no true 4-op
    # melodic patches anyway. The fused-pair real-4-op path stays
    # available in the engine for future banks that use it.
    out_flags = 0
    if flags & F_4OP:   out_flags |= OUT_PSEUDO4OP
    if flags & F_BLANK: out_flags |= OUT_BLANK
    if flags & F_FIXED: out_flags |= OUT_FIXED

    return (
        bytes([out_flags, note_offset1 & 0xFF, fb1, fb2])
        + op_mod1 + op_car1 + op_mod2 + op_car2
        + bytes([pk, note_offset2 & 0xFF])
    )

def emit_inst(label, idx, rec, flags):
    hex_ = ", ".join(f"0x{b:02X}" for b in rec)
    if flags & F_BLANK:
        tag = "blank"
    elif (flags & F_4OP) and (flags & F_PSEUDO4OP):
        tag = "p4op "
    elif flags & F_4OP:
        tag = "4op  "
    else:
        tag = "2op  "
    return f"    /* {idx:3d} {tag} {label} */\n    {hex_},\n"

def main():
    mel_recs, drum_recs = parse_wopl(BANK_PATH)

    print("/* Generated from " + os.path.basename(BANK_PATH) + " by")
    print(" * tools/wopl_to_bank.py. 128 melodic + 47 GM-drum entries,")
    print(" * 26 bytes per record (mod-first per pair, plus voice2 offset). */")
    print(" * Each entry is 25 bytes:")
    print(" *   [0]     flags (bit0=4op, bit1=blank, bit6=fixed)")
    print(" *   [1]     note_offset1 (int8, semitones added to MIDI note)")
    print(" *   [2]     C0 reg pair 1 (FB/CNT1, L/R set at load time)")
    print(" *   [3]     C0 reg pair 2 (CNT2 for 4-op; 2-op entries leave zero)")
    print(" *   [4-8]   op1 = modulator of voice 1 (or first 4-op pair)")
    print(" *   [9-13]  op2 = carrier  of voice 1")
    print(" *   [14-18] op3 = modulator of voice 2 (pseudo-4-op stack)")
    print(" *   [19-23] op4 = carrier  of voice 2")
    print(" *   [24]    percussion_key_number (drum fixed OPL3 note; 0 melodic)")
    print(" *   [25]    note_offset2 (int8, semitones for voice 2 stack)")
    print(" */")
    print("#define OF_MIDI_INST4_SIZE 26")
    print()

    print("static const uint8_t gm_bank4[128 * OF_MIDI_INST4_SIZE] = {")
    for i in range(128):
        rec = mel_recs[i]
        flags = rec[39]
        cvt = convert_record(rec)
        name = GM_PROG_NAMES[i] if i < len(GM_PROG_NAMES) else f"prog{i}"
        print(emit_inst(name, i, cvt, flags), end="")
    print("};\n")

    print("static const uint8_t gm_bank4_drum[47 * OF_MIDI_INST4_SIZE] = {")
    for gm_note in range(35, 82):
        rec = drum_recs[gm_note]
        flags = rec[39]
        cvt = convert_record(rec)
        name = DRUM_NAMES[gm_note - 35] if (gm_note - 35) < len(DRUM_NAMES) else f"drum{gm_note}"
        label = f"GM {gm_note} {name}"
        print(emit_inst(label, gm_note - 35, cvt, flags), end="")
    print("};\n")

if __name__ == "__main__":
    main()
