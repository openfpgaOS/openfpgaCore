COPY-state enable factoring

This isolated candidate starts from the proven f535bc90 worker arithmetic
candidate and changes only the local COPY branch condition. The previous
candidate and every frozen source/package remain unchanged. There is no
new state, register, feature, output format, latency, or dock/video change.

In the enabled COPY branch, the existing global take_input expression is:
  enable && !input_empty &&
  ((state == HEADER) || (state == LOAD) ||
   (state == COPY && (mode != PASS || queue_has_space)))

The enclosing case establishes state==COPY, and the enclosing sequential
else-if establishes enable==1. The condition therefore reduces exactly to:
  !input_empty && (mode != PASS || queue_has_space)

The reset/soft-reset/ring-reset branch has precedence over the entire case
in both versions. When disabled, neither version executes it. No condition
outside COPY changes: in particular the input ring's consume/lookahead,
queue emission, RAM writes, and native fetch ownership retain their original
logic. The reduction holds for every possible mode, queue occupancy and
input-empty value, including malformed packet states and FIFO underflow.

This exposes an implication to synthesis instead of asking it to remove
HEADER/LOAD alternatives inside the common take_input cone. Seed2's old
slow85 critical path traversed state.HEADER through three take_input LUTs
and two native_ys enable LUTs, with about7.54ns of listed routing arcs.
The expression is intended to remove that irrelevant dependency; only a
fitted report can establish an actual area or timing improvement.

run_lockstep.py rebuilds the existing paired-worker harness for all four
native/particle configurations against the direct f535bc90 parent. The
reference module is the exact parent with only its module name changed.
The inherited tests cover legacy/native/particle commands, malformed input,
stalls, reset, busy publication, and ring/mesh interface timing. Receipt and
complete logs are written into a new output directory. Reproduce with:
  python3 run_lockstep.py --output NEW_DIRECTORY

No Quartus jobs are performed by this component. A logical equivalence
pass does not establish physical timing closure or hardware performance.
