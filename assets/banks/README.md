# MIDI Banks (.ofsf)

Runtime sample banks for the sample-based MIDI synthesizer
(`of_midi` + `of_smp_voice`).  Apps load a bank at init time via:

```c
of_smp_bank_load("slot:10/sc55.ofsf");   // or any path the file API sees
```

## Included banks

| File | Source | Size | Notes |
| --- | --- | --- | --- |
| `sc55.ofsf` | Roland SC-55 SoundFont → `sf2_to_ofsf` | ~3 MB | General MIDI (128 melodic + 128 drum kits) |

## Building your own bank

```bash
cd tools && make
./sf2_to_ofsf input.sf2 output.ofsf
```

The converter resolves all SF2 generators at build time and emits a
flat binary the device can load directly into CRAM1 without on-device
parsing.  See `src/firmware/api/of_smp_bank.h` for the layout.
