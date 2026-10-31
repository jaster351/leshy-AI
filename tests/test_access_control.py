from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from leshy.access_control import (
    AdminModeSession,
    ConversationAccessDenied,
    ConversationAccessPolicy,
)


class ConversationAccessPolicyTests(unittest.TestCase):
    def test_user_can_read_only_own_history(self):
        policy = ConversationAccessPolicy("owner")
        self.assertTrue(policy.can_read_history("user-a", "user-a").allowed)
        self.assertFalse(policy.can_read_history("user-a", "user-b").allowed)

    def test_other_user_cannot_use_admin_mode(self):
        policy = ConversationAccessPolicy("owner")
        with self.assertRaises(ConversationAccessDenied):
            policy.open_admin_mode("user-a")

    def test_owner_without_admin_mode_cannot_read_other_history(self):
        policy = ConversationAccessPolicy("owner")
        self.assertFalse(
            policy.can_read_history(
                "owner",
                "user-a",
                explicit_request=True,
            ).allowed
        )

    def test_owner_admin_mode_still_requires_explicit_request(self):
        policy = ConversationAccessPolicy("owner")
        session = policy.open_admin_mode("owner")
        self.assertFalse(
            policy.can_read_history(
                "owner",
                "user-a",
                admin_session=session,
                explicit_request=False,
            ).allowed
        )

    def test_owner_can_read_other_history_only_in_active_admin_mode(self):
        policy = ConversationAccessPolicy("owner")
        session = policy.open_admin_mode("owner")
        decision = policy.can_read_history(
            "owner",
            "user-a",
            admin_session=session,
            explicit_request=True,
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.audit_required)

    def test_expired_admin_mode_is_rejected(self):
        policy = ConversationAccessPolicy("owner")
        expired = AdminModeSession(
            session_id="expired",
            owner_person_id="owner",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        self.assertFalse(
            policy.can_read_history(
                "owner",
                "user-a",
                admin_session=expired,
                explicit_request=True,
            ).allowed
        )

    def test_admin_read_is_audited_without_private_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "LeshyBrain"
            policy = ConversationAccessPolicy("owner-secret-id", brain)
            session = policy.open_admin_mode("owner-secret-id")
            policy.require_read_history(
                "owner-secret-id",
                "wife-secret-id",
                admin_session=session,
                explicit_request=True,
                purpose="проверка качества ответов",
            )

            path = brain / "logs" / "privacy-audit.jsonl"
            self.assertTrue(path.is_file())
            content = path.read_text(encoding="utf-8")
            self.assertIn("admin_conversation_read", content)
            self.assertIn("проверка качества ответов", content)
            self.assertNotIn("owner-secret-id", content)
            self.assertNotIn("wife-secret-id", content)


if __name__ == "__main__":
    unittest.main()
