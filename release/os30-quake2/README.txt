Retained Quake2 release profile — 2026-10-14
==========================================

This profile preserves the exact app/core pair restored after the I32
instruction-cache regression. The user confirmed performance recovered.
The current general src/ tree is not the source of this specialized image.
Build its frozen source using the instructions in the exported qualification/README.txt.

From the repository root (Python 3.10+):
  python3 tools/core_release.py verify
  python3 tools/core_release.py export
  python3 tools/core_release.py stage

Export creates build/releases/os30-quake2 with the complete portable
qualification bundle under qualification/ (including core/os30.rbf_r,
app/app.elf and checked source archives),
licenses and raw full-sweep evidence. Stage creates
build/releases/os30-quake2-sources with core/ and app/ source trees.
Use --output NEW_DIRECTORY to choose another destination. Existing outputs
are never overwritten. No command deploys or publishes anything.

The portable export is independently verifiable with:
  python3 qualification/verify.py qualification
Its historical README and original qualification records remain unchanged.
hardware-confirmation.json records the later rollback and user confirmation.
The new shipping-profile.json identifies this retained shipping decision.

Pair:
  core 7c7021e65b0bf02b5522be9b184bb9e36497f989f403149b9f98fe1ebbe82551
  app  10581bdb05e98c87dcd08abb2798549267793920f6c91e1c6be964b0dd9fb636
  source fingerprint f3685d901a8688ff9c62cb9e4259a61d04e45fe646532ebc6c744e617df81b1b
  I64 / D64, original cache RAM layout; seed 16
  main clock configured 100 MHz; worst-corner restricted Fmax 90.69 MHz
  NOT timing-closed at 100 MHz
  17995/18480 ALMs, 1848/1848 LABs, 308/308 M10Ks, 42/66 DSPs

All 30 seeds were attempted; seeds 16 and 30 passed the recorded >90 MHz
selection policy. A new compile requires its own timing qualification.
The existing image has user testing, not a universal stability guarantee.

Excluded experiments:
  - I32 cache reduction: user reported 10–20% lower performance; reverted.
  - I64 RAM repack: freed 12 M10Ks, but all 30 seeds failed the >90 MHz
    acceptance policy (best 89.07 MHz). No release substitution.

This profile is for Quake2 only. SM64 retains its separate b1083c26... core.
Generic SDK, os20 and os25 images are not replaced. Game PAKs, user settings,
saves, Quartus, compiler installations and proprietary simulation models
are not distributed. Keep the bundled third-party notices with redistribution.

Archive generation: files from the verified complete sweep package are
stored in sorted POSIX path order as regular PAX tar members, uid/gid/mtime
zero, mode 0755 for executable files or 0644 otherwise; gzip level 9, mtime
zero and an empty gzip filename. The profile pins the resulting SHA256.
