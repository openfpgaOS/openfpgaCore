F19 MESH4 — separate source/app evidence addition

UNQUALIFIED. NOT DEPLOYED. NO PHYSICAL BUILD SELECTED.

Exact app base: mesh-integrated-v1/work-app-v3, as reconstructed by the parent
bundle's sm64-integration.patch and its pinned queued-front base.
New app: mesh-integrated-v1/work-app-v4.
ELF SHA256:883e2b4cfcd105bddc032d89c4379cd72702621cd1923cacbd0b6596ad02ce9e
No binary is included. RTL and MTF2 ABI are unchanged.

Measured two-interval total: 8,901,507 candidate cycles versus 8,531,290
control cycles (+4.34%). See ../../results.txt and the strict receipts for
correctness scope, warm/cold split and limitations. This app policy did not
recover a net speedup.

The only functional change is adaptive command publication in gfx_gpu_mesh_packet.
On the CPU-ring backend, a small batch publishes when fewer than 128 already
published words remain. It subtracts staged bytes from the logical producer
cursor before comparing with the hardware consumer. Normal 512-word/sync kicks
remain; the DMA backend always publishes. The v3 ownership-wait helper is
byte-identical. The display label changes to F19 MESH4.

The extracted actual C policy and unchanged SDK kick functions passed 655,360
cases/1,966,080 ASan/UBSan checks, including all 4096 logical cursor positions,
66,656 wraps, 127/128-word boundary, sync and DMA fallback. test-result.json pins
the source and references the unchanged v3 status/wait tests. This is not a
full-frame performance or hardware qualification.

To rerun this focused test without game assets:
  gcc -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined \
      -fno-omit-frame-pointer actual_feed_test.c -o /tmp/mesh-feed-test
  ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 /tmp/mesh-feed-test
The final strict checker receipt and its exact source dependencies are in the
parent bundle under receipts/native/ and recipes/checker-dependencies/. The
original lifetime recipe here remains historical evidence.
The extraction/build/replay recipes retain original local path assumptions.
Use their exact bases and paths; no portable full-game build is claimed.

No physical build or deployment was selected: this policy did not show a net
full-frame benefit. Any future candidate requires such a benefit, exact native
oracle checks, physical fit/timing/constraint review, and an isolated trial
package preserving normal profiles and saves. No deployment action is included.
