"""Regression cases for conservative remote execution receipts."""
import unittest

from leshy.remote_execution_receipts import Outcome, TerminalReceipt


class RemoteReceiptTests(unittest.TestCase):
    def test_not_started_is_only_safe_retry(self):
        receipt = TerminalReceipt("a", started=False)
        self.assertEqual(receipt.outcome, Outcome.NOT_STARTED)
        self.assertTrue(receipt.automatic_retry_safe)

    def test_lost_transport_is_not_safe_retry(self):
        receipt = TerminalReceipt("b", started=True, transport_lost=True)
        self.assertEqual(receipt.outcome, Outcome.UNKNOWN)
        self.assertFalse(receipt.automatic_retry_safe)

    def test_completed_outcomes(self):
        self.assertEqual(TerminalReceipt("c", True, 0).outcome, Outcome.SUCCEEDED)
        self.assertEqual(TerminalReceipt("d", True, 1).outcome, Outcome.FAILED)
        self.assertFalse(TerminalReceipt("c", True, 0).automatic_retry_safe)

    def test_cancelled_not_started_is_not_retried(self):
        self.assertFalse(TerminalReceipt("e", False, cancelled=True).automatic_retry_safe)

    def test_uncertain_nonstart_must_not_claim_confirmed_nonstart(self):
        for flags in ({"transport_lost": True}, {"cancelled": True}):
            receipt = TerminalReceipt("uncertain", started=False, **flags)
            self.assertEqual(receipt.outcome, Outcome.UNKNOWN)
            self.assertFalse(receipt.automatic_retry_safe)

    def test_cancelled_after_exit_is_not_reported_success(self):
        receipt = TerminalReceipt("cancelled", started=True, exit_code=0, cancelled=True)
        self.assertEqual(receipt.outcome, Outcome.UNKNOWN)
        self.assertFalse(receipt.automatic_retry_safe)

    def test_invalid_receipts(self):
        with self.assertRaises(ValueError):
            TerminalReceipt("", False)
        with self.assertRaises(ValueError):
            TerminalReceipt("x", False, 0)
        with self.assertRaises(TypeError):
            TerminalReceipt("x", True, True)


if __name__ == "__main__":
    unittest.main()
