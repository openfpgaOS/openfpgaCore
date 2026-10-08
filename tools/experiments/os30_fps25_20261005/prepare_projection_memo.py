from pathlib import Path
import difflib,hashlib,json
root=Path.cwd();out=root/'docs/measurements/os30-fps25-20261005';base=out/'viewport-coeff-v1/gfx_gpu.c';s=base.read_text();t=s
old='static int32_t g_clip_hw = SCR_W / 2, g_clip_hh = (SCR_H / 2) * 16;'
new=old+'''
/* Only integer-quantized position and viewport affect clip projection. A tiny
 * direct map reuses shared fallback/fan vertices without changing arithmetic. */
static struct ClipProjectionMemo {
    int32_t xq, yq, wq;
    uint32_t epoch;
    int16_t x, y;
} g_clip_projection_memo[64];
static uint32_t g_clip_projection_epoch = 1;
'''
assert t.count(old)==1;t=t.replace(old,new)
old='static void gpu_set_viewport(int x, int y, int width, int height) {'
new=old+'''
    /* A wrap cannot make stale entries valid. Viewport changes are infrequent. */
    if (++g_clip_projection_epoch == 0) {
        memset(g_clip_projection_memo, 0, sizeof(g_clip_projection_memo));
        g_clip_projection_epoch = 1;
    }
'''
assert t.count(old)==1;t=t.replace(old,new)
old='    uint32_t den = wq < 256 ? 256u : (uint32_t)wq;'
new='''    uint32_t key = (uint32_t)xq ^ ((uint32_t)yq * 33u) ^ ((uint32_t)wq * 257u);
    key ^= key >> 16;
    key ^= key >> 8;
    struct ClipProjectionMemo *memo = &g_clip_projection_memo[key & 63u];
    if (memo->epoch == g_clip_projection_epoch
        && memo->xq == xq && memo->yq == yq && memo->wq == wq) {
        *x = memo->x;
        *y = memo->y;
        return;
    }
'''+old
assert t.count(old)==1;t=t.replace(old,new)
old='    *y = sy < -32768 ? -32768 : sy > 32767 ? 32767 : (int16_t)sy;'
new=old+'''
    memo->epoch = g_clip_projection_epoch;
    memo->xq = xq; memo->yq = yq; memo->wq = wq;
    memo->x = *x; memo->y = *y;
'''
assert t.count(old)==1;t=t.replace(old,new)
c=out/'projection-memo-v1';c.mkdir();(c/'gfx_gpu.c').write_text(t)
(c/'change.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),fromfile='a/gfx_gpu.c',tofile='b/gfx_gpu.c')))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(c/'manifest.json').write_text(json.dumps({'parent':str(base.relative_to(root)),'parent_sha256':sha(base),'candidate_sha256':sha(c/'gfx_gpu.c'),'qualified':False,'scope':'Exact quantized clip projection memo of 64 entries, invalidated at viewport setter. Key includes xq,yq,wq; no UV/zi/depth changes.'},indent=2)+'\n')
(root/'tools/experiments/os30_fps25_20261005/prepare_projection_memo.py').write_bytes(Path(__file__).read_bytes())
