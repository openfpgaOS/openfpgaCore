// Liveness watchdog — wraps any test bench, asserts that monitored
// signals make progress within a bounded number of cycles.  Fails
// loudly with the signal name + last-change cycle so the wedge is
// localized immediately.
//
// Usage:
//   LivenessWatchdog wd;
//   wd.add("rdptr",      [&]() { return tb->dbg_ring_rdptr; }, 5000);
//   wd.add("fence",      [&]() { return tb->dbg_fence_reached; }, 5000);
//   wd.add("bvalid_cnt", [&]() { return bvalid_cnt; }, 5000);
//   for (int t = 0; t < N; t++) {
//       tick();
//       wd.tick();        // increments cycle, checks each monitor
//       if (wd.failed()) break;
//   }
//   wd.report();
//
// The threshold is per-monitor — different signals advance at
// different rates.  ring_rdptr advances every few cycles; fence might
// only advance every few thousand.

#pragma once
#include <cstdint>
#include <cstdio>
#include <functional>
#include <string>
#include <vector>

class LivenessWatchdog {
public:
    struct Monitor {
        std::string name;
        std::function<uint64_t(void)> probe;
        uint64_t threshold;
        uint64_t last_value;
        uint64_t last_change_cycle;
        bool     ever_changed;
    };

    void add(const std::string& name,
             std::function<uint64_t(void)> probe,
             uint64_t threshold) {
        Monitor m;
        m.name      = name;
        m.probe     = probe;
        m.threshold = threshold;
        m.last_value        = probe();
        m.last_change_cycle = 0;
        m.ever_changed      = false;
        monitors_.push_back(m);
    }

    void tick() {
        cycle_++;
        for (auto& m : monitors_) {
            uint64_t v = m.probe();
            if (v != m.last_value) {
                m.last_value        = v;
                m.last_change_cycle = cycle_;
                m.ever_changed      = true;
            } else if (cycle_ - m.last_change_cycle > m.threshold) {
                if (!failed_) {
                    failed_      = true;
                    failed_name_ = m.name;
                    failed_cycle_ = cycle_;
                    failed_last_change_ = m.last_change_cycle;
                    failed_value_       = m.last_value;
                }
            }
        }
    }

    bool failed() const { return failed_; }

    void report() const {
        if (failed_) {
            printf("  FAIL [watchdog] '%s' silent for %lu cycles "
                   "(stuck at 0x%lx since cycle %lu, current cycle %lu)\n",
                   failed_name_.c_str(),
                   (unsigned long)(failed_cycle_ - failed_last_change_),
                   (unsigned long)failed_value_,
                   (unsigned long)failed_last_change_,
                   (unsigned long)failed_cycle_);
        } else {
            printf("  OK  [watchdog] all %zu monitors stayed live "
                   "over %lu cycles\n",
                   monitors_.size(),
                   (unsigned long)cycle_);
        }
    }

    uint64_t cycle() const { return cycle_; }

private:
    std::vector<Monitor> monitors_;
    uint64_t cycle_ = 0;
    bool     failed_ = false;
    std::string failed_name_;
    uint64_t failed_cycle_ = 0;
    uint64_t failed_last_change_ = 0;
    uint64_t failed_value_ = 0;
};
