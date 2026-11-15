"""Autonomous apt updates are typed, owned and independently verified.

No test opens a network connection or touches a user's server.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest import mock

from leshy.owner_identity import OwnerIdentityService
from leshy.storage import Storage, utcnow
from leshy.superagent_remote_maintenance import (
    maintenance_intent, _actor_for_ticket, _choose_registered_node,
    _matching_credential, _run_apt_update, run_remote_maintenance,
    _FIXED_UPGRADE,
)
from leshy.superagent_remote_permissions import RemotePermissionPolicy


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = Path(self.tmp.name)
        self.storage = Storage(self.brain / "leshy.sqlite3")
        self.identity = OwnerIdentityService(self.storage, self.brain)
        self.owner, self.cookie, _ = self.identity.ensure_web_session("", "web:local")
        self.app = mock.Mock(storage=self.storage, owner_identity=self.identity)
        self.policy = RemotePermissionPolicy(self.identity)
        self.node = "192.168.0.64"
        self.pin = "a" * 64

    def _link_verified_telegram(self):
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO owner_identity_links"
                "(provider,provider_user_id,owner_id,display_label,metadata_json,linked_at) "
                "VALUES(?,?,?,?,?,?)",
                ("telegram", "12345", self.owner, "Test Telegram", "{}", utcnow()),
            )

    def _enroll_node(self):
        # This test fixture uses an existing verified registration. It does not
        # test or weaken the independent host-ownership verification procedure.
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO superagent_remote_nodes(node_id,owner_id,host_fingerprint,created_at)"
                " VALUES(?,?,?,?)", (self.node, self.owner, self.pin, time.time()),
            )

    def test_natural_language_apt_request_is_recognized(self):
        request = "зайди на сервер и обнови пакеты информация по серверу и как войти"
        self.assertEqual(maintenance_intent(request), "apt_upgrade")
        self.assertEqual(maintenance_intent(
            "зайди на SSH-сервер 192.168.0.64, проверь ОС"
        ), "inspect_os")
        self.assertEqual(maintenance_intent(
            "расскажи, как обновить пакеты на сервере"
        ), "")
        self.assertEqual(maintenance_intent("не обновляй пакеты на сервере"), "")
        self.assertEqual(maintenance_intent(
            "покажи картинки Ubuntu без подключения к серверу"
        ), "")

    def test_guest_or_old_ticket_cannot_gain_remote_rights_later(self):
        self._link_verified_telegram()
        # The signed-in owner is not a substitute for authorization at the
        # time the original user mandate was created.
        ticket = {
            "owner_id": self.owner,
            "request_text": "зайди на сервер и обнови пакеты",
            "mandate_json": '{"source":"chat","remote_authorized_at_creation":false}',
        }
        with mock.patch(
            "leshy.superagent_remote_maintenance._run_apt_update"
        ) as upgrade:
            status, report = run_remote_maintenance(
                self.app, ticket, still_active=lambda: True,
            )
        self.assertEqual(status, "blocked")
        self.assertIn("момент создания", report)
        upgrade.assert_not_called()

    def test_no_provider_login_cannot_create_background_ssh_actor(self):
        with self.assertRaises(PermissionError):
            _actor_for_ticket(self.app, self.owner)
        self._link_verified_telegram()
        policy, actor = _actor_for_ticket(self.app, self.owner)
        self.assertEqual(actor.owner_id, self.owner)
        self.assertEqual(actor.method, "web")
        policy._check_actor(actor)
        with self.storage.connection() as conn:
            conn.execute("DELETE FROM owner_web_sessions WHERE owner_id=?", (self.owner,))
        with self.assertRaises(PermissionError):
            _actor_for_ticket(self.app, self.owner)

    def test_node_selection_does_not_guess_another_ip(self):
        self._link_verified_telegram()
        self._enroll_node()
        policy, actor = _actor_for_ticket(self.app, self.owner)
        self.assertEqual(_choose_registered_node(policy, actor, ""), self.node)
        self.assertEqual(
            _choose_registered_node(policy, actor, "обнови 192.168.0.64"), self.node,
        )
        with self.assertRaises(PermissionError):
            _choose_registered_node(policy, actor, "обнови 192.168.0.65")
        with self.assertRaises(ValueError):
            _choose_registered_node(
                policy, actor, "обнови 192.168.0.64 и 192.168.0.65"
            )

    def test_trusted_credentials_used_internally_not_exposed(self):
        from leshy.superagent_credential_settings import set_credential_folder
        import json
        self._link_verified_telegram()
        self._enroll_node()
        policy, actor = _actor_for_ticket(self.app, self.owner)
        directory = self.brain / "secrets"
        directory.mkdir()
        (directory / "mini-pc.json").write_text(json.dumps({
            "host": self.node, "username": "jaster",
            "password": "TOP_SECRET_PASSWORD",
        }), encoding="utf-8")
        (directory / "other.json").write_text(json.dumps({
            "host": "192.168.0.20", "username": "root", "password": "HIDDEN",
        }), encoding="utf-8")
        set_credential_folder(self.storage, self.owner, str(directory))
        name = _matching_credential(policy, actor, self.node)
        self.assertEqual(name, "mini-pc.json")
        self.assertNotIn("TOP_SECRET_PASSWORD", name)

    def test_apt_requires_verified_operating_system(self):
        self._link_verified_telegram()
        policy, actor = _actor_for_ticket(self.app, self.owner)
        with mock.patch(
            "leshy.superagent_remote_maintenance.inspect_remote_os",
            return_value={"os_family": "Windows", "distribution": ""},
        ), mock.patch(
            "leshy.superagent_remote_maintenance._stream_remote_command"
        ) as run:
            result = _run_apt_update(
                policy, actor, self.node, "ignored.json", still_active=lambda: True,
            )
        self.assertEqual(result["status"], "blocked")
        run.assert_not_called()

    def test_apt_success_requires_new_read_only_postcheck(self):
        self._link_verified_telegram()
        policy, actor = _actor_for_ticket(self.app, self.owner)
        transport = mock.Mock()
        @contextmanager
        def connected(*args, **kwargs):
            yield transport, self.pin
        with mock.patch(
            "leshy.superagent_remote_maintenance.inspect_remote_os",
            return_value={"os_family": "Linux", "distribution": "ubuntu 25.10"},
        ), mock.patch(
            "leshy.superagent_remote_maintenance._credential",
            return_value=("root", "secret"),
        ), mock.patch(
            "leshy.superagent_remote_maintenance._registered_pin",
            return_value=self.pin,
        ), mock.patch(
            "leshy.superagent_remote_maintenance.verified_connection",
            side_effect=connected,
        ) as verify, mock.patch.object(
            policy, "require",
        ) as require, mock.patch(
            "leshy.superagent_remote_maintenance._stream_remote_command",
            return_value=0,
        ) as exec_command, mock.patch(
            "leshy.superagent_remote_maintenance._fixed_command",
            return_value=(0, "0"),
        ) as postcheck:
            result = _run_apt_update(
                policy, actor, self.node, "mock.json", still_active=lambda: True,
            )
        self.assertEqual(result["status"], "ready")
        self.assertIn("0 доступных", result["report"])
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(exec_command.call_args.args[1], _FIXED_UPGRADE)
        self.assertTrue(postcheck.called)
        self.assertEqual(require.call_count, 2)
        self.assertNotIn("secret", result["report"])

    def test_apt_exit_zero_with_remaining_updates_is_not_success(self):
        self._link_verified_telegram()
        policy, actor = _actor_for_ticket(self.app, self.owner)
        @contextmanager
        def connected(*args, **kwargs):
            yield mock.Mock(), self.pin
        with mock.patch(
            "leshy.superagent_remote_maintenance.inspect_remote_os",
            return_value={"os_family": "Linux", "distribution": "ubuntu 25.10"},
        ), mock.patch(
            "leshy.superagent_remote_maintenance._credential",
            return_value=("root", "secret"),
        ), mock.patch(
            "leshy.superagent_remote_maintenance._registered_pin",
            return_value=self.pin,
        ), mock.patch(
            "leshy.superagent_remote_maintenance.verified_connection",
            side_effect=connected,
        ), mock.patch.object(policy, "require"), mock.patch(
            "leshy.superagent_remote_maintenance._stream_remote_command",
            return_value=0,
        ), mock.patch(
            "leshy.superagent_remote_maintenance._fixed_command",
            return_value=(0, "8"),
        ):
            result = _run_apt_update(
                policy, actor, self.node, "mock.json", still_active=lambda: True,
            )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("8 доступных", result["report"])

    def test_connection_timeout_never_reports_success_or_auto_retry(self):
        self._link_verified_telegram()
        policy, actor = _actor_for_ticket(self.app, self.owner)
        @contextmanager
        def connected(*args, **kwargs):
            yield mock.Mock(), self.pin
        with mock.patch(
            "leshy.superagent_remote_maintenance.inspect_remote_os",
            return_value={"os_family": "Linux", "distribution": "ubuntu 25.10"},
        ), mock.patch(
            "leshy.superagent_remote_maintenance._credential",
            return_value=("root", "secret"),
        ), mock.patch(
            "leshy.superagent_remote_maintenance._registered_pin",
            return_value=self.pin,
        ), mock.patch(
            "leshy.superagent_remote_maintenance.verified_connection",
            side_effect=connected,
        ), mock.patch.object(policy, "require"), mock.patch(
            "leshy.superagent_remote_maintenance._stream_remote_command",
            side_effect=TimeoutError("mock network failure"),
        ):
            result = _run_apt_update(
                policy, actor, self.node, "mock.json", still_active=lambda: True,
            )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("неизвестно", result["report"])
        self.assertNotIn("обновлены:", result["report"])


if __name__ == "__main__":
    unittest.main()
