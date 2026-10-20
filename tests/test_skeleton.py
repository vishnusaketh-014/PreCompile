"""Skeleton verification test.

Ensures all modules from Stage 1 to Stage 10 are importable and signatures exist.
Compatible with standard library unittest and pytest.
"""

import unittest

import precompile
import precompile.benefit
import precompile.cgroup
import precompile.cli
import precompile.config
import precompile.gates
import precompile.matcher
import precompile.monitor
import precompile.predictor
import precompile.reservation
import precompile.safety
import precompile.store
import precompile.watcher


class TestSkeleton(unittest.TestCase):
    """Test suite for repository skeleton and stub interfaces."""

    def test_imports(self):
        """Verify all modules can be imported cleanly."""
        self.assertEqual(precompile.__version__, "0.1.0")

    def test_skeleton_raises_not_implemented(self):
        """Verify skeleton methods raise NotImplementedError."""
        with self.assertRaises(NotImplementedError):
            precompile.config.load_config()

        # precompile.monitor.FileMonitor is implemented in Step 2
        fm = precompile.monitor.FileMonitor()
        fm.stop()

        # precompile.watcher.ProcessWatcher & matcher.IdentityMatcher implemented in Step 3
        pw = precompile.watcher.ProcessWatcher()
        pw.stop()

        im = precompile.matcher.IdentityMatcher()

        # precompile.store.DataStore implemented in Step 4
        store = precompile.store.DataStore(":memory:")
        store.close()

        # precompile.predictor.LatencyPredictor & gates.ConfidenceGate implemented in Step 5
        pred = precompile.predictor.LatencyPredictor()
        gate = precompile.gates.ConfidenceGate()

        # precompile.cgroup.CgroupController & reservation.ReservationManager implemented in Step 8a
        cg = precompile.cgroup.CgroupController()
        rm = precompile.reservation.ReservationManager(cgroup_backend=precompile.cgroup.FakeCgroupBackend())

        # precompile.benefit.BenefitEstimator implemented in Step 9
        be = precompile.benefit.BenefitEstimator(store=store)

        # precompile.safety.SafetyGovernor implemented in Step 10
        sg = precompile.safety.SafetyGovernor()
        self.assertIsNotNone(sg)


if __name__ == "__main__":
    unittest.main()
