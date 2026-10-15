AJ03 coarse mesh source and evidence bundle

Outcome: NO_GAIN. Read REPORT.txt for denominators and limits.

No physical fit, deployment, ROM, game assets or executable binaries are included.
verify.py checks this source inventory. --patches and --evidence additionally
require the hash-bound original workspace and verify exact patch round trips
and external evidence. Native patches include simulator wiring; they are not
a hardware deployment patch. An optional separately identified physical
overlay remains prepared-only and carries no timing qualification.

run_tests.py --output /tmp/aj03-tests runs portable wrapper/fault/combined RTL
and actual extracted-C ownership/cache tests with sanitizers. It requires
Verilator and a C/C++ compiler. These tests use synthetic inputs, not game
assets. Full native scene replay requires the separately held, hash-bound
fixtures and tools. Original workspace recipes preserve their audit paths.
