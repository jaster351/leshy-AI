"""Local SQLite/thread-only regressions for owner-scoped SSH plan jobs.

No test opens a network connection or runs an SSH command.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from leshy.storage import Storage
from leshy.superagent_remote_permissions import RemotePermissionPolicy
from leshy.superagent_ssh_jobs import (
    start_ssh_plan_job, list_ssh_plan_jobs, cancel_ssh_plan_job,
    acknowledge_ssh_plan_job, _INSTANCE, _job_can_continue,
)
from test_superagent_verified_ssh import HOST, PIN


def wait_for_job(policy, actor, job_id, statuses, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = list_ssh_plan_jobs(policy, actor, job_id=job_id)["jobs"][0]
        if job["status"] in statuses:
            return job
        time.sleep(0.025)
    raise AssertionError("SSH plan did not reach expected status: " + repr(job))


def wait_for_first_step(policy, actor, job_id, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = list_ssh_plan_jobs(policy, actor, job_id=job_id)["jobs"][0]
        if job["steps"] and job["steps"][0]["verdict"] == "in_progress":
            return job
        time.sleep(0.025)
    raise AssertionError("SSH plan did not persist first step start")


class PersistedSSHJobTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.folder.name) / "local-test.sqlite")
        self.identity = types.SimpleNamespace(
            storage=self.storage,
            owner_for_web_cookie=lambda cookie: "alice" if cookie == "verified" else "",
        )
        with self.storage.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS owner_web_sessions(
                    session_hash TEXT PRIMARY KEY, owner_id TEXT, expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS owner_identity_links(
                    owner_id TEXT, provider TEXT, provider_user_id TEXT
                );
            """)
            db.execute(
                "INSERT INTO owner_web_sessions VALUES (?,?,?)",
                (hashlib.sha256(b"verified").hexdigest(), "alice", time.time() + 3600),
            )
            db.execute("INSERT INTO owner_identity_links VALUES (?,?,?)",
                       ("alice", "github", "owner-id"))
        self.policy = RemotePermissionPolicy(
            self.identity, enrollment_verifier=lambda *args: True,
        )
        self.actor = self.policy.actor_from_web_cookie("verified")
        self.policy.register_verified_node(
            self.actor, HOST, PIN, enrollment_proof="out-of-band-test",
        )
        self.plan = [
            {"command": "sudo systemctl restart nginx",
             "verification_command": "systemctl is-active nginx",
             "expected_text": "active"},
            {"command": "sudo systemctl restart redis",
             "verification_command": "systemctl is-active redis",
             "expected_text": "active"},
        ]
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.folder.cleanup()

    def _fake_run(self, policy, actor, node, credentials, steps, *,
                  may_continue, on_step, **kwargs):
        confirmed = 0
        for i, step in enumerate(steps):
            if not may_continue():
                return {"status": "stopped", "confirmed_steps": confirmed}
            on_step(i, "started", None)
            if i == 0 and not self.release.wait(timeout=2):
                return {"status": "uncertain", "confirmed_steps": confirmed}
            outcome = {
                "verdict": "confirmed", "os_family": "Linux",
                "action": {"exit_code": 0, "stdout": "secret-ssh-output", "stderr": ""},
                "verification": {"exit_code": 0, "stdout": "active", "stderr": ""},
            }
            on_step(i, "finished", outcome)
            confirmed += 1
        return {"status": "completed", "confirmed_steps": confirmed}

    def test_progress_is_persisted_without_commands_or_secrets(self):
        with mock.patch(
            "leshy.superagent_ssh_jobs.execute_approved_ssh_plan",
            side_effect=self._fake_run,
        ):
            job = start_ssh_plan_job(self.policy, self.actor, HOST, "owner-secrets.json",
                                     self.plan, may_continue=lambda: True)
            self.assertEqual(job["node_id"], HOST)
            self.assertIn(job["status"], {"queued", "running"})
            current = wait_for_first_step(self.policy, self.actor, job["job_id"])
            self.assertEqual(current["status"], "running")
            self.assertEqual(current["steps"][0]["verdict"], "in_progress")
            self.release.set()
            finished = wait_for_job(self.policy, self.actor, job["job_id"], {"completed"})
        self.assertEqual(finished["confirmed_steps"], 2)
        self.assertEqual(finished["remaining_not_started"], 0)
        with self.storage.connection() as db:
            rows = db.execute(
                "SELECT * FROM superagent_ssh_plan_jobs WHERE job_id=?",
                (job["job_id"],),
            ).fetchone()
        data = json.dumps(dict(rows))
        for secret in ("sudo systemctl", "owner-secrets.json", "secret-ssh-output", "active"):
            self.assertNotIn(secret, data)
        self.assertEqual(len(finished["steps"]), 2)
        self.assertTrue(all(step["verdict"] == "confirmed" for step in finished["steps"]))

    def test_cancel_is_cooperative_and_disallows_next_step(self):
        with mock.patch(
            "leshy.superagent_ssh_jobs.execute_approved_ssh_plan",
            side_effect=self._fake_run,
        ):
            job = start_ssh_plan_job(self.policy, self.actor, HOST, "server.json",
                                     self.plan, may_continue=lambda: True)
            wait_for_first_step(self.policy, self.actor, job["job_id"])
            stopped = cancel_ssh_plan_job(self.policy, self.actor, job["job_id"])
            self.assertTrue(stopped["stop_requested"])
            self.release.set()
            stopped = wait_for_job(self.policy, self.actor, job["job_id"], {"stopped"})
        self.assertEqual(stopped["confirmed_steps"], 1)
        self.assertEqual(len(stopped["steps"]), 1)
        self.assertFalse(stopped["resumable"])

    def test_failed_worker_means_uncertain_not_auto_replay(self):
        with mock.patch(
            "leshy.superagent_ssh_jobs.execute_approved_ssh_plan",
            side_effect=RuntimeError("possible remote side effect"),
        ) as executor:
            job = start_ssh_plan_job(self.policy, self.actor, HOST, "server.json",
                                     self.plan, may_continue=lambda: True)
            outcome = wait_for_job(self.policy, self.actor, job["job_id"], {"uncertain"})
        self.assertEqual(executor.call_count, 1)
        self.assertFalse(outcome["auto_retry"])
        self.assertNotIn("possible remote side effect", json.dumps(outcome))

    def test_owner_isolation_and_invalid_ids(self):
        with mock.patch(
            "leshy.superagent_ssh_jobs.execute_approved_ssh_plan",
            side_effect=self._fake_run,
        ):
            job = start_ssh_plan_job(self.policy, self.actor, HOST, "server.json",
                                     self.plan, may_continue=lambda: True)
            wait_for_job(self.policy, self.actor, job["job_id"], {"running"})
            fake_actor = type(self.actor)("alice", "wrong session", "web")
            with self.assertRaises(PermissionError):
                list_ssh_plan_jobs(self.policy, fake_actor, job_id=job["job_id"])
            for value in ("garbage", "../etc/passwd", ""):
                if value:
                    with self.assertRaises(ValueError):
                        cancel_ssh_plan_job(self.policy, self.actor, value)
            self.release.set()
            wait_for_job(self.policy, self.actor, job["job_id"], {"completed"})

    def test_orphaned_job_never_resumes_until_owner_reviews(self):
        with mock.patch(
            "leshy.superagent_ssh_jobs.execute_approved_ssh_plan",
            return_value={"status": "completed", "confirmed_steps": 2},
        ):
            job = start_ssh_plan_job(self.policy, self.actor, HOST, "server.json",
                                     self.plan, may_continue=lambda: True)
            wait_for_job(self.policy, self.actor, job["job_id"], {"completed"})
        # Simulate a previous process whose state was lost after a restart.
        with self.storage.connection() as db:
            db.execute(
                "UPDATE superagent_ssh_plan_jobs SET status='running',instance_id=? "
                "WHERE job_id=?", ("previous-process-id", job["job_id"]),
            )
        stale = list_ssh_plan_jobs(self.policy, self.actor, job_id=job["job_id"])["jobs"][0]
        self.assertEqual(stale["status"], "unresolved")
        with mock.patch(
            "leshy.superagent_ssh_jobs.execute_approved_ssh_plan"
        ) as executor:
            with self.assertRaises(PermissionError):
                start_ssh_plan_job(self.policy, self.actor, HOST, "server.json",
                                   self.plan, may_continue=lambda: True)
            executor.assert_not_called()
        with self.assertRaises(PermissionError):
            acknowledge_ssh_plan_job(self.policy, self.actor, job["job_id"])
        requested = cancel_ssh_plan_job(self.policy, self.actor, job["job_id"])
        self.assertTrue(requested["stop_requested"])
        with self.assertRaises(PermissionError):
            acknowledge_ssh_plan_job(self.policy, self.actor, job["job_id"])
        # A prior worker must be quiescent for at least ten minutes.
        with self.storage.connection() as db:
            db.execute(
                "UPDATE superagent_ssh_plan_jobs SET updated_at=? WHERE job_id=?",
                (time.time() - 610, job["job_id"]),
            )
        reviewed = acknowledge_ssh_plan_job(self.policy, self.actor, job["job_id"])
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertFalse(reviewed["resumable"])

    def test_only_one_active_plan_even_when_request_races(self):
        with mock.patch(
            "leshy.superagent_ssh_jobs.execute_approved_ssh_plan",
            side_effect=self._fake_run,
        ):
            job = start_ssh_plan_job(self.policy, self.actor, HOST, "server.json",
                                     self.plan, may_continue=lambda: True)
            wait_for_job(self.policy, self.actor, job["job_id"], {"running"})
            with self.assertRaises(PermissionError):
                start_ssh_plan_job(self.policy, self.actor, HOST, "server.json",
                                   self.plan, may_continue=lambda: True)
            self.release.set()
            wait_for_job(self.policy, self.actor, job["job_id"], {"completed"})


if __name__ == "__main__":
    unittest.main()
