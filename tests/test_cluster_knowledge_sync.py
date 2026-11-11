from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from leshy.cluster_knowledge_sync import KnowledgeSyncService, _public_source, _safe_public_record
from leshy.distributed_cluster import ClusterIdentity
from leshy.knowledge import KnowledgeBase
from leshy.storage import Storage


class _Cluster:
    def __init__(self, path):
        self.identity = ClusterIdentity(path)

    def verify_request(self, body, headers):
        if headers != {"valid": True}:
            raise PermissionError("Неверная подпись")


class KnowledgeSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.storage_a = Storage(base / "a.sqlite3")
        self.storage_b = Storage(base / "b.sqlite3")
        self.knowledge_a = KnowledgeBase(self.storage_a)
        self.knowledge_b = KnowledgeBase(self.storage_b)
        self.cluster_a = _Cluster(base / "identity-a")
        self.cluster_b = _Cluster(base / "identity-b")
        # Тестовые узлы уже прошли однократное доверенное сопряжение.
        self.cluster_b.identity.secret = self.cluster_a.identity.secret
        self.cluster_b.identity.cluster_id = self.cluster_a.identity.cluster_id
        self.sync_a = KnowledgeSyncService(
            SimpleNamespace(storage=self.storage_a, knowledge=self.knowledge_a),
            self.cluster_a,
        )
        self.sync_b = KnowledgeSyncService(
            SimpleNamespace(storage=self.storage_b, knowledge=self.knowledge_b),
            self.cluster_b,
        )

    def test_public_source_disallows_secrets_and_local_addresses(self):
        self.assertTrue(_public_source("https://security.example.org/CVE-2026-12345"))
        self.assertFalse(_public_source("http://localhost/internal"))
        self.assertFalse(_public_source("http://192.168.0.12/private"))
        self.assertFalse(_public_source("https://example.org/?token=topsecret"))
        self.assertFalse(_public_source("https://login:pass@example.org/article"))

    def test_export_encrypted_and_import_as_candidate_only(self):
        claim = "Опубликована техническая статья с подробным описанием исправления уязвимости."
        self.knowledge_a.add_candidate(
            topic="Обновление ПО", claim=claim,
            source_url="https://security.example.org/advisory",
            source_type="web",
        )
        with self.storage_a.connection() as conn:
            conn.execute("UPDATE knowledge SET status='trusted'")
        self.knowledge_a.add_candidate(
            topic="Локальный текст", claim="Пользовательская конфиденциальная заметка для себя",
            source_url="file:///C:/my-secrets.txt", source_type="material-local-text",
        )
        encrypted_req = self.sync_a._cipher.encrypt(b'{"cursor": 0}').decode("ascii")
        reply = self.sync_a.handle_pull({"payload": encrypted_req}, {"valid": True})
        self.assertNotIn(claim, str(reply))
        page = __import__("json").loads(self.sync_b._cipher.decrypt(reply["payload"].encode("ascii")))
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["claim"], claim)
        self.assertEqual(self.sync_b._import_page(page), 1)
        self.assertEqual(self.sync_b._import_page(page), 0)
        with self.storage_b.connection() as conn:
            rows = conn.execute("SELECT claim,status FROM knowledge").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "candidate")

    def test_sensitive_content_is_not_shared_even_from_public_source(self):
        self.assertTrue(_safe_public_record("Научная публикация о настройке кластерного узла."))
        self.assertFalse(_safe_public_record("Напишите support@example.org"))
        self.assertFalse(_safe_public_record("пароль: secret1234"))
        self.assertFalse(_safe_public_record("token=123xyz"))
        self.assertFalse(_safe_public_record("Телефон +7 999 123 45 67"))
        self.knowledge_a.add_candidate(
            topic="Внешняя статья",
            claim="Сведения о поставщике: support@example.org и личные контакты.",
            source_url="https://security.example.org/article",
            source_type="web",
        )
        self.knowledge_a.add_candidate(
            topic="Внешняя статья",
            claim="Публичное техническое описание алгоритма обмена знаниями.",
            source_url="https://security.example.org/article2",
            source_type="web",
        )
        page = self.sync_a._export(0)
        self.assertEqual(len(page["items"]), 1)
        self.assertIn("Публичное техническое", page["items"][0]["claim"])
        imported = self.sync_b._import_page({
            "items": [{
                "topic": "Данные участника",
                "claim": "В записи присутствует личный контакт worker@example.org",
                "source_url": "https://security.example.org/article3",
                "source_title": "Официальная статья",
            }]
        })
        self.assertEqual(imported, 0)

    def test_invalid_secret_cannot_decrypt(self):
        bad = _Cluster(Path(self.temp.name) / "identity-stranger")
        wrong_sync = KnowledgeSyncService(
            SimpleNamespace(storage=self.storage_b, knowledge=self.knowledge_b), bad,
        )
        payload = self.sync_a._cipher.encrypt(b'{"cursor": 0}').decode("ascii")
        with self.assertRaises(Exception):
            wrong_sync._cipher.decrypt(payload.encode("ascii"))

    def test_reexport_imported_candidate_is_idempotent(self):
        self.knowledge_a.add_candidate(
            topic="Процессоры", claim="Это длинное техническое утверждение для проверки обмена знаниями.",
            source_url="https://docs.example.org/cpu", source_type="web",
        )
        page = self.sync_a._export(0)
        self.assertEqual(self.sync_b._import_page(page), 1)
        self.assertEqual(self.sync_b._import_page(page), 0)


if __name__ == "__main__":
    unittest.main()
