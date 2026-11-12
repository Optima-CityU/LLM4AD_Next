#!/usr/bin/env python3
"""test_auto.py — `autoloop.py` 里**仍由 Python 实现**的那几处的单测。

    cd loop_v1/auto && python3 -m unittest -v

auto/ 下的判定口径已经全部搬进 Rust, 对应的用例也跟着搬过去了 —— 口径只有一份,
测试也只该有一份:

| 原来在这里测的 | 现在在哪 |
|---|---|
| 白名单构建/校验、plan 生成、功耗温度解析、判定规则 | `cargo test sysparam` |
| 画面动没动 | `cargo test framecheck` |
| 模型回包解析、密钥解析、局部变异、序数启发 | `cargo test llm` |
| CPython random 的逐位复刻 (局部变异靠它才可复现) | `cargo test pyrandom` |

autoloop 的编排已搬进 `src/autoloop.rs` (`cargo test autoloop`), test_auto 只剩
`knob_sysparam.sh` 的回滚波及面 (设备端 shell, 直接抠出来在真 sh 里跑)。

涉及设备的部分不在这里测 —— 那部分由真机跑出来的 report.json 作证。
"""
from __future__ import annotations
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import pybridge as WL           # noqa: E402  (白名单已搬进 src/sysparam.rs, 这里只为造一份测试用的表)


PROBE = """# probe_sysparam v1
cpu.policies=0,3
cpu.policy0.avail_freqs=300000 1000000 2000000
cpu.policy0.avail_governors=schedutil performance
cpu.policy0.scaling_min_freq.cur=300000
cpu.policy0.scaling_max_freq.cur=2000000
cpu.policy0.scaling_governor.cur=schedutil
cpu.policy0.scaling_min_freq.writable=yes
cpu.policy0.scaling_min_freq.effect=live
cpu.policy0.scaling_max_freq.writable=yes
cpu.policy0.scaling_governor.writable=yes
cpu.policy3.avail_freqs=500000 3000000
cpu.policy3.scaling_min_freq.cur=500000
cpu.policy3.scaling_min_freq.writable=yes
cpu.policy3.scaling_min_freq.effect=rejected(wrote=3000000 readback=500000)
gpu.num_pwrlevels=4
gpu.min_pwrlevel.cur=3
gpu.max_pwrlevel.cur=0
gpu.min_pwrlevel.writable=yes
gpu.min_pwrlevel.effect=live
gpu.max_pwrlevel.writable=yes
gpu.devfreq.avail_freqs=220000000 1000000000
gpu.devfreq.min_freq.cur=220000000
gpu.devfreq.min_freq.writable=no
bus.DDR.boost_freq.cur=0
bus.DDR.hw_min_freq=200000
bus.DDR.hw_max_freq=5333000
bus.DDR.avail_freqs=200000 3200000 5333000
bus.DDR.boost_freq.writable=yes
bus.DDR.boost_freq.effect=live
setting.system.peak_refresh_rate.cur=120
setting.system.min_refresh_rate.cur=60
display.modes=fps=60,fps=120
thermal.cpu-1-0=42000
"""


class TestSnapshotDiffClassification(unittest.TestCase):
    """留痕的判据是「这一组候选到底写过哪些项」, 不是一张写死的豁免名单。

    实测: 有一组候选只写了 DDR/LLCC 的 boost_freq, 退出时 cpu.policy0.scaling_max
    (1785600->1228800)、cpu.policy6.scaling_max、kgsl.max_pwrlevel 照样变了 ——
    红魔的厂商温控/性能管家在游戏过程中自己在改。拿写死名单豁免它们等于给自己
    开后门; 按「写没写过」分类才站得住。
    """

    def setUp(self):
        import autoloop
        self.classify = autoloop.classify_diff
        self.touched = autoloop.touched_keys
        self.keyof = autoloop.snapshot_key_of
        self.wl = WL.build_whitelist(PROBE)

    @staticmethod
    def _sd(*pairs):
        return {"checked": True, "n_lines": 38, "identical": not pairs,
                "n_diff": len(pairs),
                "diffs": [{"line": i + 1, "before": f"{k}={a}", "after": f"{k}={b}"}
                          for i, (k, a, b) in enumerate(pairs)]}

    def test_key_mapping_matches_device_snapshot_naming(self):
        # device_snapshot.sh 用 scaling_max / governor, 不是 sysfs 的叶子名
        self.assertEqual(self.keyof("sysfs", "/sys/devices/system/cpu/cpufreq/"
                                             "policy6/scaling_max_freq"),
                         "cpu.policy6.scaling_max")
        self.assertEqual(self.keyof("sysfs", "/sys/devices/system/cpu/cpufreq/"
                                             "policy0/scaling_governor"),
                         "cpu.policy0.governor")
        self.assertEqual(self.keyof("sysfs", "/sys/class/kgsl/kgsl-3d0/max_pwrlevel"),
                         "kgsl.max_pwrlevel")
        self.assertEqual(self.keyof("sysfs", "/sys/devices/system/cpu/bus_dcvs/"
                                             "DDR/boost_freq"), "bus.DDR.boost_freq")
        self.assertEqual(self.keyof("setting", "system:refresh_rate_mode"),
                         "settings.refresh_rate_mode")

    def test_touched_keys_include_the_rollback_siblings(self):
        keys = self.touched({"cpu.policy0.scaling_governor": "performance"}, self.wl)
        # 碰 governor 会波及同 policy 的 min/max, 所以这三项都算我们的账
        self.assertEqual(keys, {"cpu.policy0.governor",
                                "cpu.policy0.scaling_max",
                                "cpu.policy0.scaling_min"})

    def test_vendor_manager_drift_on_untouched_keys_is_not_our_residue(self):
        """只写了 DDR/LLCC 的那一组, 厂商管家改的三项不该算我们留的痕。"""
        keys = self.touched({"bus.DDR.boost_freq": "5333000"}, self.wl)
        c = self.classify(self._sd(
            ("cpu.policy0.scaling_max", "1785600", "1228800"),
            ("kgsl.max_pwrlevel", "2", "0")), keys)
        self.assertTrue(c["ours_identical"])
        self.assertFalse(c["strict_identical"])        # 严格 diff 原样保留, 不藏
        self.assertEqual(len(c["environment_diffs"]), 2)

    def test_a_node_we_wrote_still_counts_as_residue(self):
        keys = self.touched({"bus.DDR.boost_freq": "5333000"}, self.wl)
        c = self.classify(self._sd(("bus.DDR.boost_freq", "0", "5333000")), keys)
        self.assertFalse(c["ours_identical"])
        self.assertEqual(c["environment_diffs"], [])

    def test_sibling_left_behind_is_caught(self):
        """切 governor 导致 scaling_max 没回来 —— 这正是它必须被抓住的那一类。"""
        keys = self.touched({"cpu.policy0.scaling_governor": "performance"}, self.wl)
        c = self.classify(self._sd(("cpu.policy0.scaling_max", "1785600", "1228800")),
                          keys)
        self.assertFalse(c["ours_identical"])

    def test_clean_snapshot_passes_both_layers(self):
        c = self.classify(self._sd(), set())
        self.assertTrue(c["ours_identical"])
        self.assertTrue(c["strict_identical"])

    def test_no_key_set_means_everything_counts(self):
        """探测阶段没有「候选写过什么」的概念, 这时一切差异都算数, 不放水。"""
        c = self.classify(self._sd(("kgsl.max_pwrlevel", "2", "0")), None)
        self.assertFalse(c["ours_identical"])


class TestKnobRollbackBlastRadius(unittest.TestCase):
    """写一个节点会把兄弟节点一起改掉 —— 实测把 cpufreq 的 governor 切成
    performance 再切回 walt, scaling_max_freq 从 1785600 变成 1228800 且没回来,
    一组本来干净的数据 (p95 -7.2%) 因此作废。回滚必须覆盖整组兄弟节点。

    直接把脚本里的 siblings() 抠出来在真 sh 里跑, 不做字符串位置断言 ——
    要验的是行为, 不是源码长什么样。"""

    SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knob_sysparam.sh")

    @classmethod
    def setUpClass(cls):
        src = open(cls.SH).read()
        cls.fn = src[src.index("siblings() {"):src.index("read_one() {")]

    def siblings(self, path):
        import subprocess
        r = subprocess.run(["sh", "-c", self.fn + f'\nsiblings "{path}"'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split()

    def test_touching_any_cpufreq_node_covers_the_whole_policy(self):
        base = "/sys/devices/system/cpu/cpufreq/policy0"
        for touched in ("scaling_min_freq", "scaling_max_freq", "scaling_governor"):
            got = self.siblings(f"{base}/{touched}")
            self.assertEqual(got, [f"{base}/scaling_governor",
                                   f"{base}/scaling_max_freq",
                                   f"{base}/scaling_min_freq"],
                             f"碰 {touched} 时波及面不对")

    def test_governor_is_restored_before_min_and_max(self):
        """governor 会重设 min/max, 所以它必须排在回滚顺序最前。"""
        got = self.siblings("/sys/devices/system/cpu/cpufreq/policy6/scaling_governor")
        self.assertTrue(got[0].endswith("scaling_governor"))
        self.assertTrue(got[-1].endswith("scaling_min_freq"))

    def test_kgsl_pwrlevel_pair_is_covered_both_ways(self):
        for touched in ("min_pwrlevel", "max_pwrlevel"):
            got = self.siblings(f"/sys/class/kgsl/kgsl-3d0/{touched}")
            self.assertEqual(got, ["/sys/class/kgsl/kgsl-3d0/max_pwrlevel",
                                   "/sys/class/kgsl/kgsl-3d0/min_pwrlevel"])

    def test_unrelated_node_has_no_blast_radius(self):
        p = "/sys/devices/system/cpu/bus_dcvs/DDR/boost_freq"
        self.assertEqual(self.siblings(p), [p])

    def test_state_is_written_before_any_device_write(self):
        """回滚依据必须先落盘再动设备, 否则中途被杀就回不去了。"""
        src = open(self.SH).read()
        apply_part = src[src.index("  apply)"):src.index("  restore)")]
        self.assertLess(apply_part.index('> "$STATE"'), apply_part.index("两遍写"))


class TestTempCapIsASafetyLimit(unittest.TestCase):
    """温度上限的职责是「别把机器烤坏」, 不是「保证两臂热态一样」。"""

    def test_cap_floor_is_a_real_hardware_margin(self):
        import autoloop
        # 45C 在本机跑原神根本达不到 (基线就 55.2C), 拿它当上限每组都会作废
        self.assertGreaterEqual(autoloop.TEMP_CAP_FLOOR_C, 60.0)
        # 骁龙结温保护在 95C 上下, 上限要留足余量
        self.assertLess(autoloop.TEMP_CAP_FLOOR_C, 90.0)

    def test_margin_tolerates_ordinary_drift(self):
        """同一场连跑里 0.5C 的漂移再正常不过, 不该判成安全事故。"""
        import autoloop
        self.assertGreaterEqual(autoloop.TEMP_CAP_MARGIN_C, 5.0)


if __name__ == "__main__":
    unittest.main()
