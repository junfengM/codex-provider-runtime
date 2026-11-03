from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prebuilt_retry import COOLDOWN_SECONDS, allowed


class RetryGateTests(unittest.TestCase):
    def test_scheduled_failure_expires_and_manual_or_push_bypass(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "failure.json"
            self.assertTrue(allowed(marker, "schedule", 100))
            marker.write_text(json.dumps({"failed_at": 100}))
            self.assertFalse(allowed(marker, "schedule", 101))
            self.assertFalse(allowed(marker, "schedule", 100 + COOLDOWN_SECONDS - 1))
            self.assertTrue(allowed(marker, "schedule", 100 + COOLDOWN_SECONDS))
            self.assertTrue(allowed(marker, "workflow_dispatch", 101))
            self.assertTrue(allowed(marker, "push", 101))

    def test_bad_markers_never_suppress_a_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "failure.json"
            for payload in ('oops', 'null', '[]', '{}', '{"failed_at":true}',
                            '{"failed_at":"100"}', '{"failed_at":200}',
                            '{"failed_at":NaN}', '{"failed_at":Infinity}'):
                with self.subTest(payload=payload):
                    marker.write_text(payload)
                    self.assertTrue(allowed(marker, "schedule", 101))
