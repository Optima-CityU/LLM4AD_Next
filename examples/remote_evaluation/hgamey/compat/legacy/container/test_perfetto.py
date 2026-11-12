"""Check cross-thread ATrace window markers against the real pinned processor."""

import io
import shutil
import unittest


class MarkerTests(unittest.TestCase):
    """Exercise the Android external-printf emitter behavior without a phone."""

    def test_async_window_closes_across_different_emitter_threads(self):
        """A unique async pair must form one window despite different printf PIDs."""
        from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

        binary = shutil.which("trace_processor_shell")
        self.assertIsNotNone(binary)
        marker = "llm4ad_window_" + "a" * 32
        trace = (
            "# tracer: nop\n"
            f"  printf-101 (  100) [000] .... 1.000000: tracing_mark_write: S|100|{marker}|1\n"
            f"  printf-102 (  100) [000] .... 2.000000: tracing_mark_write: F|100|{marker}|1\n"
        )
        with TraceProcessor(trace=io.BytesIO(trace.encode()), config=TraceProcessorConfig(bin_path=binary)) as tp:
            rows = list(tp.query(f"SELECT ts,dur FROM slice WHERE name='{marker}'"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].dur, 1_000_000_000)


if __name__ == "__main__":
    unittest.main()
