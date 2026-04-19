# MIDI Banks (.ofsf)

Runtime sample banks for the sample-based MIDI synthesizer
(`of_midi` + `of_smp_voice`).  Apps load a bank at init time via:

```c
of_smp_bank_load("slot:10/sc55.ofsf");   // or any path the file API sees
```

## Included banks

| File | Source | Size | Notes |
| --- | --- | --- | --- |
| `sc55.ofsf` | Roland SC-55 SoundFont → `sf2_to_ofsf` | ~3 MB | General MIDI (128 melodic + 128 drum kits), OFSF v3 |

## Bank format version

`OFSF_VERSION` lives in `src/firmware/api/of_smp_bank.h`.  The kernel's
bank preloader and SDK binding both reject any file whose version
doesn't match — so a version bump invalidates every existing bank and
forces a regenerate.

- **v2** (pre-AWE): SF2 generators stored as their raw SF2 units
  (timecents, centibels, cents).  Runtime converted at note-on /
  envelope-stage transitions using the helpers in `of_smp_tables.c`.
- **v3** (current, AWE Phase 0 pre-bake): the offline converter calls
  those same helpers up front and stores pre-resolved per-tick rates,
  Q16.16 sustain levels, and 0..255 linear attenuation.  Runtime note-on
  becomes close to `memcpy`; envelope-stage transitions become field
  reads.  Zone grows from 80 B to 112 B.  `mod_lfo_to_volume` (unused
  in v2 runtime) was dropped.

## Building your own bank

```bash
cd tools && make
./sf2_to_ofsf input.sf2 output.ofsf
```

The converter resolves all SF2 generators at build time and emits a
flat binary the device can load directly into CRAM1 without on-device
parsing.  See `src/firmware/api/of_smp_bank.h` for the layout.
