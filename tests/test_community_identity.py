from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.community_identity import CommunityIdentity, ReplayGuard, verify_envelope


class CommunityIdentityTests(unittest.TestCase):
    def test_per_owner_keys_are_persistent_and_distinct(self):
        with tempfile.TemporaryDirectory() as home:
            alice = CommunityIdentity(Path(home), "alice")
            again = CommunityIdentity(Path(home), "alice")
            bob = CommunityIdentity(Path(home), "bob")
            self.assertEqual(alice.public_key, again.public_key)
            self.assertNotEqual(alice.public_key, bob.public_key)
            self.assertNotIn("alice", str(alice.key_path))
            self.assertEqual(len(alice.key_path.read_bytes()), 32)

    def test_sign_verify_and_prevent_replay(self):
        with tempfile.TemporaryDirectory() as home:
            identity = CommunityIdentity(home, "alice")
            guard = ReplayGuard()
            envelope = identity.sign("cluster.hello", {"capacity": 3}, now=1000)
            result = verify_envelope(envelope, replay_guard=guard, now=1001)
            self.assertEqual(result["key_id"], identity.key_id)
            self.assertEqual(result["payload"], {"capacity": 3})
            self.assertNotIn("private_key", envelope)
            self.assertNotIn("secret", envelope)
            with self.assertRaisesRegex(ValueError, "Повтор"):
                verify_envelope(envelope, replay_guard=guard, now=1001)

    def test_reject_tampering_and_expiry(self):
        with tempfile.TemporaryDirectory() as home:
            identity = CommunityIdentity(home, "alice")
            envelope = identity.sign("cluster.hello", {"ok": True}, now=1000)
            with self.assertRaisesRegex(ValueError, "Устаревшее"):
                verify_envelope(envelope, now=1100)
            changed = {**envelope, "payload": {"ok": False}}
            with self.assertRaisesRegex(ValueError, "Подпись"):
                verify_envelope(changed, now=1000)

    def test_reject_unsigned_unknown_actions(self):
        with tempfile.TemporaryDirectory() as home:
            identity = CommunityIdentity(home, "alice")
            with self.assertRaises(ValueError):
                identity.sign("shell.run", {"cmd": "whoami"})
            message = identity.sign("cluster.heartbeat", {"active": True}, now=1000)
            message.pop("signature")
            with self.assertRaises(ValueError):
                verify_envelope(message, now=1000)

    def test_second_owner_cannot_impersonate_first(self):
        with tempfile.TemporaryDirectory() as home:
            alice = CommunityIdentity(home, "alice")
            bob = CommunityIdentity(home, "bob")
            message = alice.sign("cluster.hello", {}, now=1000)
            forged = {**message, "public_key": bob.public_key}
            with self.assertRaisesRegex(ValueError, "Подпись"):
                verify_envelope(forged, now=1000)


if __name__ == "__main__":
    unittest.main()
