#include "../layer/artifact_stats.h"
#include <cassert>
#include <cstdio>
int main() {
    ArtifactStats s;
    s.allocated(1,100); s.allocated(2,100); s.allocated(3,200);
    s.record(2); s.record(2); s.inherit(1,2); s.inherit(1,2);
    assert(s.dispatch_recorded == 2 && s.submitted_count({1}) == 4);
    s.submit_result(s.submitted_count({1}),false); assert(s.queue_submitted == 0);
    s.submit_result(s.submitted_count({1}),true); s.submit_result(s.submitted_count({1}),true);
    assert(s.queue_submitted == 8);
    s.reset(1); assert(s.submitted_count({1}) == 0);
    s.reset_pool(100); assert(s.submitted_count({2}) == 0);
    s.record(3); s.free(3); assert(s.pending.empty());
    s.record(2); s.reset_pool(100,true); assert(s.pending.empty() && s.pools.empty());
    s.disable("compile_failed"); s.disable("second_reason");
    assert(s.disabled == 1 && s.disabled_reason == "compile_failed");
    puts(s.json().c_str());
}
