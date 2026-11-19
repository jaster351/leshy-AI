"""Двенадцать изолированных проверок привязок, остановки и LAN-профиля.

Исходник не разрешает запуск: требуется отдельное подтверждение владельца.
Сокеты, потоки, CA, сертификаты и секретные хранилища подменены или запрещены.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import errno
import hmac
from http.server import ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from leshy.cluster_orchestrator import ClusterNode
from leshy import cluster_knowledge_sync
from leshy.cluster_knowledge_sync import KnowledgeSyncService, _replication_skip_reason
from leshy.creator_consensus import summarize_creator_votes
from leshy import distributed_cluster, web_listeners
from leshy.distributed_cluster import DistributedClusterRuntime, LanPeerDiscovery
from leshy.web_transport_runtime import WebTransportRuntime
from leshy.web_transport_settings import WebTransportSettings


HOST = "leshy.example.org"
LOCAL_IP = "192.0.2.25"
DIGEST = "c" * 64


class FakeSocket:
    """Записывать параметры и UDP-пакеты, не создавая системного сокета."""

    def __init__(self):
        self.operations = []
        self.packets = []
        self.closed = False

    def setsockopt(self, level, option, value):
        self.operations.append(("option", level, option, value))

    def sendto(self, packet, address):
        self.packets.append((packet, address))
        return len(packet)

    def close(self):
        self.closed = True


class WebListenerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ))
        for name in ("LESHY_PORT", "LESHY_HTTPS_PORT", "LESHY_CLUSTER_ADVERTISE"):
            os.environ.pop(name, None)
        forbidden = AssertionError("Настоящая сеть, поток или секретное хранилище запрещены")
        for method in ("connect", "bind", "sendto"):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=forbidden))
        self.socket_constructor = self.stack.enter_context(patch("socket.socket", side_effect=forbidden))
        self.resolver = self.stack.enter_context(patch("socket.getaddrinfo", side_effect=forbidden))
        self.thread = self.stack.enter_context(patch("threading.Thread", side_effect=forbidden))
        self.stack.enter_context(patch("leshy.web_acme._AcmeWire", side_effect=forbidden))
        self.stack.enter_context(patch("leshy.distributed_cluster.ClusterIdentity", side_effect=forbidden))
        self.stack.enter_context(patch.object(DistributedClusterRuntime, "__init__", side_effect=forbidden))
        self.stack.enter_context(patch.object(KnowledgeSyncService, "__init__", side_effect=forbidden))
        self.stack.enter_context(patch("leshy.distributed_cluster.get_inference_scheduler", side_effect=forbidden))
        self.stack.enter_context(patch("cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key",
                                      side_effect=forbidden))
        self.stack.enter_context(patch("cryptography.hazmat.primitives.asymmetric.ec.generate_private_key",
                                      side_effect=forbidden))
        for module in ("secret_store", "web_certificates", "web_acme"):
            for operation in ("load_secret", "save_secret"):
                self.stack.enter_context(patch("leshy." + module + "." + operation, side_effect=forbidden))
        self.stack.enter_context(patch("leshy.web_transport_settings.private_path"))
        self.stack.enter_context(patch("leshy.web_transport_runtime.problem"))
        self.stack.enter_context(patch("leshy.distributed_cluster.problem"))
        self.stack.enter_context(patch("leshy.distributed_cluster.event"))
        self.stack.enter_context(patch("leshy.cluster_knowledge_sync.problem"))
        self.stack.enter_context(patch("leshy.cluster_knowledge_sync.event"))
        self.stack.enter_context(patch("leshy.cluster_knowledge_sync.Fernet", side_effect=forbidden))
        self.stack.enter_context(patch("leshy.cluster_knowledge_sync.request.build_opener", side_effect=forbidden))
        self.certificates = Mock()
        self.certificates.context.return_value = object()
        self.certificates.status.return_value = {"configured": True, "valid": True,
                                                 "dns_names": [HOST], "ip_addresses": []}
        self.stack.enter_context(patch("leshy.web_transport_runtime.WebCertificateStore",
                                      return_value=self.certificates))
        self.acme = Mock()
        self.acme.status.return_value = {}
        self.acme.configuration_guard.side_effect = lambda: nullcontext({"enabled": False, "in_progress": False})
        self.stack.enter_context(patch("leshy.web_acme.WebAcmeService", return_value=self.acme))

    def runtime(self, *, host="auto", saved=None):
        settings = WebTransportSettings(self.root)
        settings.save({"http_enabled": True, "http_port": 8765, "https_enabled": True,
                       "https_port": 8766, "hostname": HOST, **(saved or {}), "confirm": True})
        return WebTransportRuntime(self.root, host)

    @staticmethod
    def live_binding(runtime, name, address, port):
        protocol = runtime._protocol(name)
        runtime._bindings[name] = {"family": "IPv6" if ":" in address else "IPv4",
                                   "address": address, "port": port,
                                   "listening": True, "status": "listening"}
        runtime._threads[name] = Mock(is_alive=Mock(return_value=True))
        runtime._states[protocol].update(listening=True, status="listening", port=port)

    def test_auto_and_explicit_plans_do_not_expand_resolved_hosts(self):
        self.assertEqual(web_listeners.binding_addresses("auto", 8765), [
            (socket.AF_INET, ("0.0.0.0", 8765)), (socket.AF_INET6, ("::", 8765, 0, 0))])
        self.resolver.assert_not_called()
        addresses = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 8765, 0, 0)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8765)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8765)),
                     (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:127.0.0.1", 8765, 0, 0))]
        with patch("socket.getaddrinfo", return_value=addresses) as resolver:
            self.assertEqual(web_listeners.binding_addresses("localhost", 8765), [
                (socket.AF_INET6, ("::1", 8765, 0, 0)), (socket.AF_INET, ("127.0.0.1", 8765))])
            resolver.assert_called_once_with("localhost", 8765, socket.AF_UNSPEC, socket.SOCK_STREAM)
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Тестовое имя не разрешено")):
            with self.assertRaises(socket.gaierror):
                web_listeners.binding_addresses("missing.example.org", 8765)

    def test_windows_ipv6_options_precede_bind(self):
        server = object.__new__(web_listeners._IPv6WebServer)
        server.socket = FakeSocket()
        server.server_address = ("::", 8765, 0, 0)
        operations = server.socket.operations
        exclusive = 0x7F01
        with patch.object(web_listeners.os, "name", "nt"), \
                patch.object(socket, "SO_EXCLUSIVEADDRUSE", exclusive, create=True), \
                patch.object(ThreadingHTTPServer, "server_bind",
                             lambda instance: operations.append(("bind", instance.server_address))):
            server.server_bind()
        self.assertEqual(operations, [
            ("option", socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1),
            ("option", socket.SOL_SOCKET, exclusive, 1), ("bind", server.server_address)])
        self.assertFalse(server.allow_reuse_address)
        self.assertFalse(server.allow_reuse_port)
        self.socket_constructor.assert_not_called()

    def test_windows_exclusive_failure_never_reaches_bind(self):
        for option in (0x7F01, None):
            with self.subTest(option=option):
                server = object.__new__(web_listeners._WebServer)
                server.socket = Mock()
                server.socket.setsockopt.side_effect = OSError("Тестовый отказ exclusive")
                with patch.object(web_listeners.os, "name", "nt"), \
                        patch.object(socket, "SO_EXCLUSIVEADDRUSE", option, create=True), \
                        patch.object(ThreadingHTTPServer, "server_bind") as bind:
                    with self.assertRaises(OSError):
                        server.server_bind()
                    bind.assert_not_called()
                self.assertFalse(server.allow_reuse_address)
                self.assertFalse(server.allow_reuse_port)
                if option is None:
                    server.socket.setsockopt.assert_not_called()

    def test_mapped_peer_keeps_port_and_ipv6_tuple_tail(self):
        server = object.__new__(web_listeners._WebServer)
        for address, expected in [
                (("::ffff:127.0.0.1", 4321, 7, 9), ("127.0.0.1", 4321, 7, 9)),
                (("::ffff:192.0.2.20", 4321, 0, 0), ("192.0.2.20", 4321, 0, 0)),
                (("::1", 4321, 0, 0), ("::1", 4321, 0, 0)),
                (("2002:7f00:1::", 4321, 0, 0), ("2002:7f00:1::", 4321, 0, 0))]:
            with self.subTest(address=address):
                connection = Mock()
                with patch.object(ThreadingHTTPServer, "get_request", return_value=(connection, address)):
                    received, peer = server.get_request()
                self.assertIs(received, connection)
                self.assertEqual(peer, expected)
                connection.settimeout.assert_called_once_with(60)
        self.assertTrue(ipaddress.ip_address(web_listeners._peer_address(("::ffff:127.0.0.1", 1))[0]).is_loopback)
        self.assertFalse(ipaddress.ip_address(web_listeners._peer_address(("2002:7f00:1::", 1))[0]).is_loopback)

    def test_factory_selects_all_four_server_classes_without_construction(self):
        handler, certificate = object(), object()
        classes = {name: self.stack.enter_context(patch.object(web_listeners, name)) for name in (
            "_WebServer", "_TLSWebServer", "_IPv6WebServer", "_IPv6TLSWebServer")}
        for family, address, cert, expected in [
                (socket.AF_INET, ("127.0.0.1", 8765), None, "_WebServer"),
                (socket.AF_INET, ("127.0.0.1", 8766), certificate, "_TLSWebServer"),
                (socket.AF_INET6, ("::1", 8765, 0, 0), None, "_IPv6WebServer"),
                (socket.AF_INET6, ("::1", 8766, 0, 0), certificate, "_IPv6TLSWebServer")]:
            with self.subTest(server=expected):
                for factory in classes.values():
                    factory.reset_mock()
                result = web_listeners.create_listener(family, address, handler, cert)
                self.assertIs(result, classes[expected].return_value)
                arguments = (address, handler) if cert is None else (address, handler, cert)
                classes[expected].assert_called_once_with(*arguments)
                for name, factory in classes.items():
                    if name != expected:
                        factory.assert_not_called()
        with self.assertRaises(ValueError):
            web_listeners.create_listener(-1, ("127.0.0.1", 8765), handler)
        self.socket_constructor.assert_not_called()

    def test_partial_ipv4_failure_keeps_live_ipv6_status_and_actual_url(self):
        runtime = self.runtime(saved={"https_enabled": False})
        server = Mock(server_address=("::", 19000, 0, 0))
        def factory(family, address, handler, certificates):
            if family == socket.AF_INET:
                raise OSError(errno.EADDRINUSE, "Тестовый порт IPv4 занят")
            return server
        with patch("leshy.web_transport_runtime.create_listener", side_effect=factory) as create, \
                patch("threading.Thread") as thread, \
                patch.object(runtime._stopping, "wait", return_value=True):
            thread.return_value.is_alive.return_value = True
            runtime.bind(Mock())
            runtime.serve_forever()
        status = runtime.status()["runtime"]["http"]
        self.assertEqual(create.call_count, 2)
        self.assertEqual([item["status"] for item in status["bindings"]], ["bind_failed", "listening"])
        self.assertTrue(status["listening"])
        self.assertEqual(status["port"], 19000)
        self.assertEqual(runtime.listening_url(), "http://[::1]:19000/")
        server.serve_forever.assert_not_called()

    def test_tray_shutdown_before_start_skips_workers_and_acme(self):
        runtime = self.runtime()
        runtime._servers["http"] = Mock()
        runtime.shutdown()
        runtime.serve_forever()
        self.assertTrue(runtime._stopping.is_set())
        self.thread.assert_not_called()
        self.acme.start.assert_not_called()
        self.assertFalse(runtime._running)

        during_start = self.runtime()
        during_start._servers = {"http": Mock(), "http/1": Mock()}
        ready = Mock()
        during_start._on_listening = ready
        with patch.object(during_start, "_start_server", side_effect=lambda name: during_start.shutdown()) as start:
            during_start.serve_forever()
        self.assertEqual(start.call_count, 1)
        self.acme.start.assert_not_called()
        ready.assert_not_called()
        self.assertFalse(during_start._running)

    def test_cluster_profile_uses_live_http_or_valid_https_without_claiming_reachability(self):
        for protocol, address, valid_san, endpoint in [
                ("http", "0.0.0.0", True, "http://" + LOCAL_IP + ":19000"),
                ("https", "::", True, "https://" + HOST + ":19000"),
                ("http", "::", True, None), ("http", "127.0.0.1", True, None),
                ("https", "0.0.0.0", False, None)]:
            with self.subTest(protocol=protocol, address=address, san=valid_san):
                runtime = self.runtime()
                self.live_binding(runtime, protocol, address, 19000)
                self.certificates.status.return_value = {
                    "dns_names": [HOST if valid_san else "other.example.org"], "ip_addresses": []}
                profile = runtime.cluster_profile(LOCAL_IP)
                self.assertEqual(profile["endpoint"], endpoint)
                self.assertEqual(profile["accepts_remote"], endpoint is not None)
                self.assertFalse(profile["reachability_verified"])
                self.assertTrue(profile["reason"])

    def test_managed_unready_cluster_keeps_local_execution_and_honors_explicit_advertise(self):
        cluster = object.__new__(DistributedClusterRuntime)
        profile = Mock(return_value={"endpoint": None, "accepts_remote": False})
        cluster.app = SimpleNamespace(web_security=SimpleNamespace(cluster_profile=profile),
                                      settings=SimpleNamespace(ollama_model=""))
        cluster.identity = SimpleNamespace(node_id="local-node")
        scheduler = Mock()
        scheduler.status.return_value = {"queue_depth": 2, "active": True}
        with patch.object(distributed_cluster, "_memory_info", return_value=(16.0, 8.0)), \
                patch.object(distributed_cluster, "_local_ip", return_value=LOCAL_IP) as local_ip, \
                patch.object(distributed_cluster, "_load_ratio", return_value=0.25), \
                patch.object(distributed_cluster, "get_inference_scheduler", return_value=scheduler):
            os.environ["LESHY_PORT"] = "54321"
            node = cluster._build_local_node()
            self.assertEqual(node.endpoint, "")
            self.assertTrue(node.accepting_tasks)
            self.assertEqual(node.queue_depth, 3)
            profile.assert_called_once_with(LOCAL_IP)
            profile.reset_mock()
            local_ip.reset_mock()
            os.environ["LESHY_CLUSTER_ADVERTISE"] = "https://explicit.example.org:9443/"
            advertised = cluster._build_local_node()
            self.assertEqual(advertised.endpoint, "https://explicit.example.org:9443")
            self.assertTrue(advertised.accepting_tasks)
            profile.assert_not_called()
            local_ip.assert_not_called()
        self.socket_constructor.assert_not_called()

    def test_passive_signed_discovery_retains_votes_and_rejects_false_advertisements(self):
        secret = b"isolated-synthetic-cluster-key"
        sender = LanPeerDiscovery(SimpleNamespace(node_id="sender", cluster_id="test-cluster", secret=secret))
        receiver = LanPeerDiscovery(SimpleNamespace(node_id="receiver", cluster_id="test-cluster", secret=secret))
        local = ClusterNode(node_id="sender", endpoint="", cpu_threads=2, accepting_tasks=True)
        bot = "a" * 24
        sender.set_telegram_vote(bot, "sender", 42)
        datagram = FakeSocket()
        with patch.object(distributed_cluster.socket, "socket", return_value=datagram), \
                patch.object(distributed_cluster, "creator_identity_digest", return_value=DIGEST), \
                patch.object(distributed_cluster.time, "time", return_value=1000.0):
            sender.announce(local)
            self.assertTrue(datagram.closed)
            self.assertEqual(len(datagram.packets), 1)
            packet, destination = datagram.packets[0]
            self.assertEqual(destination, ("255.255.255.255", sender.port))
            public = json.loads(packet)
            self.assertFalse(public["node"]["accepting_tasks"])
            self.assertEqual(public["node"]["endpoint"], "")
            self.assertTrue(local.accepting_tasks)
            signature = public.pop("signature")
            canonical = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.assertEqual(signature, hmac.new(secret, canonical, "sha256").hexdigest())
            receiver._accept_packet(packet, LOCAL_IP)
            peer = receiver.peers()[0]
            self.assertEqual(peer.endpoint, "")
            self.assertFalse(peer.accepting_tasks)
            self.assertEqual(receiver.telegram_peer_votes(bot)["sender"]["ack"], 42)
            self.assertEqual(receiver.telegram_peer_votes(bot)["sender"]["vote"], "sender")
            votes = receiver.creator_peer_votes()
            self.assertEqual(votes, {"sender": DIGEST})
            self.assertEqual(summarize_creator_votes(DIGEST, votes)["status"], "consistent")
            tampered = json.loads(packet)
            tampered["node"]["node_id"] = "invalid-signature"
            receiver._accept_packet(json.dumps(tampered).encode("utf-8"), LOCAL_IP)
            self.assertEqual(receiver.diagnostics()["counts"]["invalid_signature"], 1)
            false_advertisement = json.loads(packet)
            false_advertisement.pop("signature")
            false_advertisement["node"].update(node_id="false-advertisement", accepting_tasks=True)
            canonical = json.dumps(false_advertisement, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")
            false_advertisement["signature"] = hmac.new(secret, canonical, "sha256").hexdigest()
            receiver._accept_packet(json.dumps(false_advertisement).encode("utf-8"), LOCAL_IP)
            self.assertEqual([item.node_id for item in receiver.peers()], ["sender"])
            self.assertEqual(receiver.diagnostics()["counts"]["accepted"], 1)
            self.assertTrue(local.accepting_tasks)
            with patch.object(distributed_cluster, "_local_ip", return_value=LOCAL_IP):
                for endpoint in ("https://" + HOST + ":8766", "https://[fd00::25]:8766"):
                    sender._last_node = ClusterNode(node_id="sender", endpoint=endpoint, cpu_threads=2)
                    sender._accept_packet(packet, LOCAL_IP)
                    self.assertEqual(sender.diagnostics()["counts"].get("same_node_id", 0), 0)
                sender._accept_packet(packet, "192.0.2.99")
                self.assertEqual(sender.diagnostics()["counts"]["same_node_id"], 1)
        self.thread.assert_not_called()

    def test_replication_classifier_keeps_dns_and_non_lan_addresses_unsupported(self):
        for endpoint, reason in [
                ("", "no_endpoint"),
                ("https://" + HOST + ":8766", "dns_not_supported"),
                ("http://192.168.1.25:8765", ""),
                ("https://[fd00::25]:8766", ""),
                ("http://8.8.8.8:8765", "outside_lan"),
                ("http://127.0.0.1:8765", "outside_lan"),
                ("http://[::ffff:127.0.0.1]:8765", "outside_lan"),
                ("http://[::ffff:192.168.1.25]:8765", ""),
                ("http://169.254.1.25:8765", "outside_lan"),
                ("http://[fe80::25]:8765", "outside_lan"),
                ("http://0.0.0.0:8765", "outside_lan"),
                ("http://[::]:8765", "outside_lan"),
                ("ftp://192.168.1.25", "invalid_endpoint")]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(_replication_skip_reason(endpoint), reason)
        self.resolver.assert_not_called()
        self.socket_constructor.assert_not_called()

    def test_replication_tick_skips_before_cursor_and_only_syncs_eligible_ip(self):
        previous_sync = "2026-11-19T01:02:03Z"
        current_sync = "2026-11-19T01:03:04Z"
        dns_peer = SimpleNamespace(node_id="dns-peer", endpoint="https://" + HOST + ":8766")
        empty_peer = SimpleNamespace(node_id="empty-peer", endpoint="")
        lan_peer = SimpleNamespace(node_id="lan-peer", endpoint="http://192.168.1.25:8765")
        page = {"items": [{"claim": "Тестовая общая запись"}], "cursor": 11}

        def service(peers):
            sync = object.__new__(KnowledgeSyncService)
            sync._lock = threading.RLock()
            sync._stop = Mock(wait=Mock(side_effect=[False, True]), is_set=Mock(return_value=False))
            sync._cursors = {"dns-peer": 5, "empty-peer": 8, "lan-peer": 2}
            sync._state = {"enabled": True, "state": "error", "last_sync": previous_sync,
                           "last_error": "ValueError (pull)", "received_total": 7, "sent_total": 0,
                           "last_received": 9, "peer_count": 0, "last_peer": "previous-peer",
                           "eligible_peer_count": 0, "skipped_peers": []}
            connection = Mock(side_effect=AssertionError("Настоящая база данных запрещена"))
            sync.app = SimpleNamespace(storage=SimpleNamespace(connection=connection))
            sync.cluster = SimpleNamespace(identity=SimpleNamespace(node_id="local-node"),
                                           _autodiscovery_configured=True,
                                           orchestrator=SimpleNamespace(peers=Mock(return_value=peers)))
            sync._cursor_for = Mock(return_value=2)
            sync._pull_peer = Mock(return_value=page)
            sync._import_page = Mock(return_value=3)
            sync._save_cursor = Mock(side_effect=lambda node_id, value: sync._cursors.update({node_id: value}))
            return sync

        with patch.object(cluster_knowledge_sync, "module_enabled", return_value=True), \
                patch.object(cluster_knowledge_sync, "utcnow", return_value=current_sync) as now:
            unsupported = service([dns_peer, empty_peer])
            cursors = dict(unsupported._cursors)
            unsupported._run()
            state = unsupported.status()
            self.assertEqual(state["state"], "unsupported_targets")
            self.assertEqual(state["last_sync"], previous_sync)
            self.assertEqual(state["last_error"], "")
            self.assertEqual(state["last_received"], 0)
            self.assertEqual(state["received_total"], 7)
            self.assertEqual(state["peer_count"], 2)
            self.assertEqual(state["eligible_peer_count"], 0)
            self.assertEqual(state["skipped_peers"], [
                {"node_id": "dns-peer", "reason": "dns_not_supported"},
                {"node_id": "empty-peer", "reason": "no_endpoint"}])
            self.assertEqual(unsupported._cursors, cursors)
            for method in (unsupported._cursor_for, unsupported._pull_peer,
                           unsupported._import_page, unsupported._save_cursor):
                method.assert_not_called()
            unsupported.app.storage.connection.assert_not_called()
            now.assert_not_called()

            mixed = service([dns_peer, lan_peer])
            mixed._run()
            mixed._cursor_for.assert_called_once_with("lan-peer")
            mixed._pull_peer.assert_called_once_with(lan_peer, 2)
            mixed._import_page.assert_called_once_with(page)
            mixed._save_cursor.assert_called_once_with("lan-peer", 11)
            self.assertEqual(mixed._cursors, {"dns-peer": 5, "empty-peer": 8, "lan-peer": 11})
            state = mixed.status()
            self.assertEqual(state["state"], "synchronized")
            self.assertEqual(state["last_sync"], current_sync)
            self.assertEqual(state["last_error"], "")
            self.assertEqual(state["received_total"], 10)
            self.assertEqual(state["last_received"], 3)
            self.assertEqual(state["peer_count"], 2)
            self.assertEqual(state["eligible_peer_count"], 1)
            self.assertEqual(state["skipped_peers"], [{"node_id": "dns-peer", "reason": "dns_not_supported"}])
            mixed.app.storage.connection.assert_not_called()
            now.assert_called_once_with()
        self.thread.assert_not_called()
        self.socket_constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
