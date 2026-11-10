"""Regression: rotating to a healthy seed must not inherit exponential backoff.

These tests are intentionally not executed locally by automation.
"""
import threading

from leshy.swarm_membership import SwarmMembership


class _Wake:
    def __init__(self, stop):
        self.stop = stop
        self.delays = []

    def wait(self, delay):
        self.delays.append(delay)
        self.stop.set()

    def clear(self):
        pass


def _membership(monkeypatch, *, rotated):
    member = SwarmMembership.__new__(SwarmMembership)
    member._lock = threading.RLock()
    member._stop = threading.Event()
    member._wake = _Wake(member._stop)
    member._advertise_error = ""
    member._bootstrap_error = ""
    member._manual_configured = False
    member._endpoint = "https://seed.example/v1/swarm/hello"
    member._seed_urls = (member._endpoint,)
    member._directory = type("Directory", (), {"last_error": ""})()
    member.enabled = lambda: True
    member._hello = lambda: (_ for _ in ()).throw(ConnectionError("offline"))
    member._try_next_seed = lambda: rotated
    member._discover_seeds = lambda: None
    monkeypatch.setattr("leshy.swarm_membership.problem", lambda *args: None)
    return member


def test_rotated_seed_retries_without_exponential_backoff(monkeypatch):
    member = _membership(monkeypatch, rotated=True)
    member._run()
    assert member._wake.delays == [1.0]


def test_single_failed_seed_keeps_backoff(monkeypatch):
    member = _membership(monkeypatch, rotated=False)
    member._run()
    assert member._wake.delays == [20.0]
