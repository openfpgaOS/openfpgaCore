# Simple OFSF / SoundFont Audio Architecture

## Goal

Use the simplest architecture that can do both at the same time:

- one stereo PCM playback path
- one OFSF / SoundFont playback path

The mixer does **not** need to stay a generic 32-voice PCM block.

## Simplest Architecture

Keep the SoundFont control logic on the **main CPU** and make the hardware do only sample playback and mixing.

That means:

- CPU parses MIDI / OFSF zones
- CPU handles voice allocation, envelopes, LFOs, and controller state
- hardware mixer only plays samples, pans them, mixes them, and feeds `audio_output`

This is the simplest design because it reuses what already exists in the repo instead of adding a new coprocessor control plane.

## What Stays On The CPU

Use the existing CPU-side SoundFont engine in `of_smp_voice.c`.

That code already has:

- OFSF zone lookup
- MIDI event handling
- voice stealing
- envelope / LFO / filter updates

Relevant current limits in the repo:

- `SMP_MAX_VOICES = 28` in `src/firmware/api/of_smp_voice.h`

For the simple architecture, the CPU remains the SF2/OFSF scheduler.

## What Stays In Hardware

The hardware block should do only this:

### 1. Stereo PCM path

- one dedicated stereo stream / sample path
- intended for stereo music, stereo SFX, or decoded audio

### 2. SF2 sample voices

- a small pool of mono sample voices
- each voice supports:
  - rate
  - loop
  - volume
  - pan

### 3. Final mix

- mix stereo PCM + SF2 voices together
- send result to existing `audio_output`

No new hardware SF2 scheduler is required.

## Polyphony Target

The default target should be **16 SF2 voices**.

Why:

- the stereo path is separate, so it does not consume SF2 slots
- most of the complexity comes from the synth voice count
- `32` should not be the default unless real MIDI / bank testing proves it is necessary

Recommended target:

- `1` stereo PCM path
- `16` SF2 voices

If testing later shows dense banks need more headroom, move to `24`. Do not design around `32` first.

## Data Flow

```text
Main CPU
  - MIDI / OFSF logic
  - voice allocation
  - env / LFO / control updates
            |
            | mixer register writes
            v
   +----------------------+
   | Simple Audio Mixer   |
   |  - 1 stereo PCM path |
   |  - 16 SF2 voices     |
   |  - pan / mix         |
   +----------------------+
            |
            v
      `audio_output`
```

## Memory Model

### SDRAM

- OFSF metadata
- CPU-side voice state
- event / stream state

### CRAM1

- sample blob
- stereo PCM buffers

### BRAM

- mixer voice table
- small per-voice sample cache
- existing output buffers

## CRAM1 Strategy

Simplest rule:

- CPU reads metadata from SDRAM
- hardware mixer reads samples from CRAM1

That already matches the current software direction in `of_smp_bank.c`, which avoids CPU metadata reads from CRAM1 during playback.

## Current Resource Usage

Latest fit report:

### Whole build

- ALMs: `14,612 / 18,480` (`79%`)
- RAM blocks: `261 / 308` (`85%`)
- DSPs: `19 / 66` (`29%`)
- registers: `20,412`
- worst setup slack: about `-0.623 ns`

### Current subsystem context

| Subsystem | ALMs | M10Ks | DSPs |
| --- | ---: | ---: | ---: |
| CPU (`VexiiRiscv + cpu_system + CRAM0`) | `~6,236` | `153` | `5` |
| GPU | `2,365` | `50` | `1` |
| audio | `~2,075` | `13` | `11` |
| `axi_periph_slave` | `1,076` | `33` | `0` |
| analogizer | `624` | `0` | `2` |
| SDRAM path | `485` | `0` | `0` |
| CRAM1 path | `388` | `3` | `0` |

### Current audio breakdown

| Block | ALMs | M10Ks | DSPs | Notes |
| --- | ---: | ---: | ---: | --- |
| `audio_mixer` | `1,881` | `9` | `11` | voice table `2`, per-voice cache `1`, reverb `2`, chorus `1`, sends `2`, pos latch `1` |
| `audio_output` | `194` | `4` | `0` | 1024-deep `dcfifo` |

Current audio subtotal:

- `~2,075 ALMs`
- `13 M10Ks`
- `11 DSPs`

## Target Budget

Because this architecture keeps SF2 control on the CPU and simplifies the hardware role, the target should stay **at or below** the current audio footprint.

### Recommended target build

| Item | Target |
| --- | ---: |
| Stereo PCM paths | `1` |
| SF2 voices | `16` |
| Total mixer ALMs | `1.7k-2.0k` |
| Total mixer M10Ks | `8-10` |
| `audio_output` | unchanged |

### Combined audio budget

| Block | Est. ALMs | Est. M10Ks |
| --- | ---: | ---: |
| simplified mixer | `1.7k-2.0k` | `8-10` |
| `audio_output` | `194` | `4` |
| total audio path | `1.9k-2.2k` | `12-14` |

Design intent:

- ALMs should stay roughly flat or go down from the current `~2,075`
- M10Ks should stay roughly flat around the current `13`
- DSP usage should stay near the current audio footprint unless filter math changes materially

## Why This Is Simpler

- no new hardware SF2 scheduler
- no mailbox / coprocessor protocol
- no second core
- no new ring-BRAM refill engine
- no need to preserve a 32-voice generic PCM surface

It is mostly a reduction and reshaping of the current mixer, not a new subsystem.

## Design Rules

1. Keep `audio_output` unchanged.
2. Keep OFSF / MIDI control on the CPU.
3. Add one stereo PCM path to the mixer.
4. Reduce hardware synth voice count to `16` by default.
5. Increase polyphony only after real bank testing.

## Conclusion

The simplest architecture is:

- CPU-managed OFSF / SoundFont playback
- one stereo PCM path in hardware
- one small SF2 voice pool in hardware
- shared final mix into `audio_output`

Default target:

- `1` stereo PCM path
- `16` SF2 voices

This is simpler than introducing a new coprocessor architecture and better aligned with the current codebase.
