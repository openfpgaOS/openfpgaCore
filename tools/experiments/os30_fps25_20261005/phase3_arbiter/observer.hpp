// Passive host-only observations, sampled before each rising edge.
#include <algorithm>
#include <vector>
#include <stdexcept>

static bool obs_enabled = false;
struct Latencies {
    std::vector<uint64_t> values;
    void print(const char *prefix, const char *name) {
        std::sort(values.begin(), values.end());
        uint64_t sum=0;
        for (auto x: values) sum+=x;
        auto percentile=[&](unsigned p) -> uint64_t {
            return values.empty() ? 0 : values[(values.size()*p+99)/100-1];
        };
        printf(" %s_%s_n=%llu %s_%s_sum=%llu %s_%s_p50=%llu %s_%s_p95=%llu %s_%s_max=%llu",
            prefix,name,(unsigned long long)values.size(),prefix,name,(unsigned long long)sum,
            prefix,name,(unsigned long long)percentile(50),prefix,name,(unsigned long long)percentile(95),
            prefix,name,(unsigned long long)(values.empty()?0:values.back()));
    }
};
struct ReaderObserver {
    bool pending=false, inflight=false, counted=false, active_counted=false;
    uint64_t issued=0,accepted=0,active_issued=0;
    unsigned beats=0;
    uint64_t arrivals=0, accepts=0, completed=0, wait=0, occupied=0;
    Latencies queue, service, total;
    void tick(uint64_t cycle, bool valid, bool ready, bool rvalid, bool rlast) {
        if (obs_enabled) {
            wait += valid&&!ready;
            occupied += inflight;
        }
        if (rvalid) {
            if (!inflight) throw std::runtime_error("Response without accepted request");
            ++beats;
            if (rlast != (beats==16)) throw std::runtime_error("Read burst is not exactly16 beats");
            if (rlast) {
                if (obs_enabled) ++completed;
                if (active_counted) {
                    service.values.push_back(cycle-accepted);
                    total.values.push_back(cycle-active_issued);
                }
                inflight=false;
            }
        }
        if (valid && !pending) {
            pending=true; issued=cycle; counted=obs_enabled;
            if (obs_enabled) ++arrivals;
        }
        if (valid&&ready) {
            if (inflight || !pending) throw std::runtime_error("Invalid synthetic reader request overlap");
            if (obs_enabled) ++accepts;
            if (counted) queue.values.push_back(cycle-issued);
            active_counted=counted;
            active_issued=issued;accepted=cycle;beats=0;
            inflight=true;pending=false;
        }
    }
    void print(const char *name) {
        printf(" %s_arrivals=%llu %s_accepts=%llu %s_completed=%llu %s_ar_wait=%llu %s_outstanding=%llu",
            name,(unsigned long long)arrivals,name,(unsigned long long)accepts,
            name,(unsigned long long)completed,name,(unsigned long long)wait,
            name,(unsigned long long)occupied);
        queue.print(name,"queue"); service.print(name,"service");total.print(name,"total");
    }
};
static ReaderObserver obs_cpu, obs_audio;
static uint64_t obs_window_cycles=0,obs_owner[4]={},obs_idle=0;
static uint64_t obs_cpu_wait_owner[5]={};
static uint64_t obs_gpu_ar_wait=0,obs_gpu_aw_wait=0,obs_gpu_w_wait=0;
static bool obs_scan_active=false,obs_scan_counted=false;
static uint64_t obs_scan_start=0,obs_scan_arrivals=0,obs_scan_completed=0;
static Latencies obs_scan_latency;
static void observe_tick() {
    if (!tb->reset_n) return;
    const auto t=cycles;
    obs_cpu.tick(t,tb->obs_cpu_valid,tb->obs_cpu_ready,tb->obs_cpu_rvalid,tb->obs_cpu_rlast);
    obs_audio.tick(t,tb->obs_audio_valid,tb->obs_audio_ready,tb->obs_audio_rvalid,tb->obs_audio_rlast);
    if (tb->obs_scan_req) {
        if (obs_scan_active) throw std::runtime_error("Overlapping scanout request");
        obs_scan_active=true;obs_scan_start=t;obs_scan_counted=obs_enabled;
        if (obs_enabled) ++obs_scan_arrivals;
    }
    if (tb->obs_scan_done) {
        if (!obs_scan_active) throw std::runtime_error("Scanout done without request");
        if (obs_enabled) ++obs_scan_completed;
        if (obs_scan_counted) obs_scan_latency.values.push_back(t-obs_scan_start);
        obs_scan_active=false;
    }
    if (!obs_enabled) return;
    ++obs_window_cycles;
    const unsigned owner=tb->obs_arb_state?tb->obs_grant:4;
    if (owner<4) ++obs_owner[owner];else ++obs_idle;
    if (tb->obs_cpu_valid&&!tb->obs_cpu_ready) ++obs_cpu_wait_owner[owner];
    obs_gpu_ar_wait+=tb->obs_gpu_arvalid&&!tb->obs_gpu_arready;
    obs_gpu_aw_wait+=tb->obs_gpu_awvalid&&!tb->obs_gpu_awready;
    obs_gpu_w_wait+=tb->obs_gpu_wvalid&&!tb->obs_gpu_wready;
}
static void observe_report() {
    printf("ARBITER window_cycles=%llu gpu_service=%llu cpu_service=%llu bridge_service=%llu audio_service=%llu idle=%llu",
        (unsigned long long)obs_window_cycles,(unsigned long long)obs_owner[0],
        (unsigned long long)obs_owner[1],(unsigned long long)obs_owner[2],
        (unsigned long long)obs_owner[3],(unsigned long long)obs_idle);
    printf(" cpu_wait_gpu=%llu cpu_wait_cpu=%llu cpu_wait_bridge=%llu cpu_wait_audio=%llu cpu_wait_idle=%llu",
        (unsigned long long)obs_cpu_wait_owner[0],(unsigned long long)obs_cpu_wait_owner[1],
        (unsigned long long)obs_cpu_wait_owner[2],(unsigned long long)obs_cpu_wait_owner[3],
        (unsigned long long)obs_cpu_wait_owner[4]);
    printf(" gpu_ar_wait=%llu gpu_aw_wait=%llu gpu_w_wait=%llu",
        (unsigned long long)obs_gpu_ar_wait,(unsigned long long)obs_gpu_aw_wait,(unsigned long long)obs_gpu_w_wait);
    obs_cpu.print("cpu");obs_audio.print("audio");
    printf(" scan_arrivals=%llu scan_completed=%llu",(unsigned long long)obs_scan_arrivals,(unsigned long long)obs_scan_completed);
    obs_scan_latency.print("scan","latency");
    printf("\n");
}
