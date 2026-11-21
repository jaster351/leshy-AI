"""Девять изолированных регрессий срока памяти и источников GraphRAG.

Запуск требует отдельного разрешения владельца. Приложение не импортируется:
реальные определения читаются из AST. SQLite создаётся только при запуске
сценария и только как ':memory:'; Storage/LeshyBrain, файлы пользователя,
модели, сеть и системные потоки не используются.
"""
from __future__ import annotations

import ast
import builtins
from contextlib import contextmanager
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import islice
import json
import math
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from typing import Any
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "leshy"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def denied(*_args, **_kwargs):
    raise AssertionError("Импорт приложения или внешнее действие запрещены")


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


class FakeRLock:
    """Одиночный сценарий не создаёт системную блокировку или поток."""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def declarations(filename, namespace, *, selected=None):
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8-sig"))
    nodes = [node for node in tree.body if isinstance(
        node, (ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.ClassDef))]
    if selected is not None:
        nodes = [node for node in nodes if getattr(node, "name", None) in selected]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec", dont_inherit=True), namespace)


def selected_class(filename, name, methods, namespace):
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8-sig"))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    node = copy.copy(original)
    node.body = [item for item in original.body if isinstance(item, ast.FunctionDef) and item.name in methods]
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec", dont_inherit=True), namespace)
    return namespace[name]


def selected_method(filename, parent, name, namespace):
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == parent)
    node = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec", dont_inherit=True), namespace)
    return namespace[name]


class RecordingCursor:
    def __init__(self, cursor, operation):
        self.cursor, self.operation = cursor, operation

    def fetchall(self):
        rows = self.cursor.fetchall()
        self.operation["rows"] = len(rows)
        return rows

    def fetchone(self):
        row = self.cursor.fetchone()
        self.operation["rows"] = int(row is not None)
        return row

    def fetchmany(self, size):
        rows = self.cursor.fetchmany(size)
        self.operation["rows"] += len(rows)
        self.operation.setdefault("batches", []).append(len(rows))
        return rows

    def close(self):
        self.operation["closed"] = True
        self.cursor.close()


class RecordingConnection:
    def __init__(self, storage):
        self.storage = storage

    def execute(self, sql, parameters=()):
        operation = {"sql": " ".join(sql.split()), "parameters": tuple(parameters), "rows": 0}
        self.storage.operations.append(operation)
        return RecordingCursor(self.storage.db.execute(sql, parameters), operation)

    def executescript(self, sql):
        return self.storage.db.executescript(sql)


class SyntheticStorage:
    """Единственное соединение SQLite целиком живёт в оперативной памяти теста."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE memories(
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
                kind TEXT NOT NULL, content TEXT NOT NULL, importance REAL NOT NULL,
                source TEXT, created_at TEXT NOT NULL, expires_at TEXT, fingerprint TEXT);
            CREATE TABLE knowledge(
                id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL,
                claim TEXT NOT NULL, source_url TEXT, source_title TEXT,
                confidence REAL NOT NULL, status TEXT NOT NULL,
                data_class TEXT NOT NULL, checked_at TEXT, ttl_seconds INTEGER);
        """)
        self.operations = []
        self.fail_next_commit = False

    @contextmanager
    def connection(self):
        try:
            yield RecordingConnection(self)
            if self.fail_next_commit:
                self.fail_next_commit = False
                raise RuntimeError("Синтетическая ошибка commit")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise


class Fixture:
    def __init__(self, case):
        self.storage = SyntheticStorage()
        case.addCleanup(self.storage.db.close)
        self.tick = 100.0
        self.events, self.problems = [], []

        def namespace():
            return {
                "__name__": __name__,
                "__builtins__": dict(vars(builtins), __import__=denied, open=denied),
                "datetime": FrozenDatetime, "timezone": timezone, "Any": Any,
                "dataclass": dataclass, "re": re, "json": json, "math": math,
                "sha256": sha256, "Storage": object, "islice": islice,
                "threading": SimpleNamespace(RLock=FakeRLock),
                "time": SimpleNamespace(monotonic=lambda: self.tick),
                "utcnow": lambda: NOW.isoformat(),
                "event": lambda *args, **kw: self.events.append((args, kw)),
                "problem": lambda *args, **kw: self.problems.append((args, kw)),
            }

        freshness = namespace()
        declarations("memory_freshness.py", freshness)
        self.fresh = freshness["memory_is_fresh"]
        integrity = namespace()
        declarations("text_integrity.py", integrity)
        knowledge = namespace()
        knowledge["corrupted_knowledge_row"] = integrity["corrupted_knowledge_row"]
        declarations("knowledge.py", knowledge, selected={"is_fresh"})
        self.knowledge_type = selected_class(
            "knowledge.py", "KnowledgeBase", {"__init__", "_search_rows"}, knowledge)

        memory = namespace()
        memory["memory_is_fresh"] = self.fresh
        declarations("intelligent_memory.py", memory)
        self.memory_type = memory["IntelligentMemoryEngine"]
        graph = namespace()
        graph.update(memory_is_fresh=self.fresh, KnowledgeBase=self.knowledge_type,
                     is_fresh=knowledge["is_fresh"],
                     corrupted_knowledge_row=integrity["corrupted_knowledge_row"])
        declarations("knowledge_graph.py", graph)
        self.graph = graph["KnowledgeGraph"](self.storage)
        base = namespace()
        base.update(memory_is_fresh=self.fresh,
                    persistent_rule_context=lambda storage, owner: "- Постоянное правило: " + owner)
        self.base_context = selected_method("app.py", "LeshyApp", "_memory_context", base)

    def memory(self, text, *, owner="owner-a", expiry=None, importance=0.8, kind="explicit", source="user"):
        cursor = self.storage.db.execute(
            "INSERT INTO memories(owner_id,kind,content,importance,source,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
            (owner, kind, text, importance, source, NOW.isoformat(), expiry))
        self.storage.db.commit()
        return int(cursor.lastrowid)

    def knowledge(self, text, *, status="trusted", topic="Python", confidence=0.9,
                  data_class="static", checked_at=None, ttl=None, identifier=None):
        cursor = self.storage.db.execute(
            "INSERT INTO knowledge(id,topic,claim,source_url,source_title,confidence,status,data_class,checked_at,ttl_seconds) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (identifier, topic, text, "https://docs.python.org/source", "Синтетический источник",
             confidence, status, data_class, checked_at, ttl))
        self.storage.db.commit()
        return int(cursor.lastrowid)

    def link(self, kind, identifier, text, *, owner="owner-a", source="cached-source"):
        with self.storage.connection() as conn:
            self.graph._link_fact(conn, owner_id="shared" if kind == "knowledge" else owner,
                                  fact_kind=kind, fact_key=f"{kind}:{identifier}",
                                  text=text, source=source, weight=0.8)

    def no_sync(self):
        self.graph.sync = lambda **_kwargs: {"knowledge": 0, "memories": 0}


class MemoryFreshnessGraphTests(unittest.TestCase):
    def test_expiry_utc_boundaries_malformed_values_and_retrieve_share_one_rule(self):
        fixture = Fixture(self)
        cases = [(None, True), ("", True), (" \t ", True),
                 ((NOW + timedelta(seconds=1)).isoformat(), True),
                 ("2026-11-19T12:00:01", True), ("2026-11-19T15:00:01+03:00", True),
                 (NOW.isoformat(), False), ("2026-11-19T11:59:59Z", False),
                 ("сломанная дата", False), (False, False), (12, False), ({}, False)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertIs(fixture.fresh(value, now=NOW), expected)
        self.assertTrue(fixture.fresh("2026-11-19T12:00:01", now=NOW.replace(tzinfo=None)))
        fixture.memory("Python просрочено", expiry=NOW.isoformat())
        fixture.memory("Python неверный срок", expiry="bad")
        good = fixture.memory("Python действующая память", expiry="2026-11-19T12:00:01")
        fixture.memory("Python чужая память", owner="owner-b")
        matches = fixture.memory_type(fixture.storage).retrieve("owner-a", "Python", limit=8)
        self.assertEqual([item.memory_id for item in matches], [good])

    def test_actual_base_context_skips_expired_first_eight_without_reading_thousand_payloads(self):
        fixture = Fixture(self)
        for index in range(1005):
            fixture.memory(f"Истёкшая запись {index}", expiry=NOW.isoformat(), importance=1.0)
        for index in range(10):
            fixture.memory(f"Живая запись {index}", importance=0.8)
        fixture.memory("Чужой контент", owner="owner-b", importance=1.0)
        fixture.memory("Отдельный документ", kind="owner_knowledge_document", importance=1.0)
        before = [tuple(row) for row in fixture.storage.db.execute("SELECT * FROM memories ORDER BY id")]
        fixture.storage.operations.clear()
        result = fixture.base_context(SimpleNamespace(storage=fixture.storage), "owner-a",
                                      query="тема", capture_text="исходные слова")
        self.assertEqual(result.count("Постоянное правило"), 1)
        self.assertEqual(result.count("- Живая запись"), 8)
        self.assertNotIn("Истёкшая", result)
        self.assertNotIn("Чужой", result)
        self.assertNotIn("Отдельный документ", result)
        selects = [item for item in fixture.storage.operations if item["sql"].startswith("SELECT")]
        self.assertEqual(len(selects), 2)
        self.assertIn("SELECT id,expires_at", selects[0]["sql"])
        self.assertNotIn("LIMIT", selects[0]["sql"])
        self.assertTrue(selects[0]["closed"])
        self.assertGreater(selects[0]["rows"], 1000)
        self.assertLessEqual(max(selects[0]["batches"]), 128)
        self.assertIn("SELECT content,expires_at", selects[1]["sql"])
        self.assertIn("LIMIT 8", selects[1]["sql"])
        self.assertLessEqual(selects[1]["rows"], 8)
        self.assertEqual(before, [tuple(row) for row in fixture.storage.db.execute("SELECT * FROM memories ORDER BY id")])

    def test_graph_revalidates_deleted_edited_expired_and_live_memory_during_cooldown(self):
        fixture = Fixture(self)
        deleted = fixture.memory("Резервирование удалённая память")
        edited = fixture.memory("Резервирование прежняя редакция")
        expired = fixture.memory("Резервирование ограниченная память")
        live = fixture.memory("Резервирование действующая память", source="old-source")
        self.assertEqual(fixture.graph.sync(owner_id="owner-a")["memories"], 4)
        fixture.tick += 1
        fixture.storage.db.execute("DELETE FROM memories WHERE id=?", (deleted,))
        fixture.storage.db.execute("UPDATE memories SET content='Другая изменённая тема' WHERE id=?", (edited,))
        fixture.storage.db.execute("UPDATE memories SET expires_at=? WHERE id=?", (NOW.isoformat(), expired))
        fixture.storage.db.execute("UPDATE memories SET source='current-source',importance=0 WHERE source='old-source'")
        fixture.storage.db.commit()
        rows = fixture.graph.search("owner-a", "Резервирование")
        self.assertEqual([(row.text, row.source) for row in rows],
                         [("Резервирование действующая память", "current-source")])
        self.assertAlmostEqual(rows[0].score, 0.2 + 0.22 * len(rows[0].related_concepts),
                               msg="Нулевой current importance не восстанавливается из высокого cached weight")
        stale = fixture.storage.db.execute(
            "SELECT label FROM knowledge_graph_nodes WHERE kind='memory' AND node_key=?", (f"memory:{edited}",)).fetchone()
        self.assertEqual(stale["label"], "Резервирование прежняя редакция",
                         "Защита должна работать без нового sync, поверх старого индекса")
        fixture.graph.sync(owner_id="owner-a", force=True)
        weights = fixture.storage.db.execute(
            "SELECT DISTINCT e.weight FROM knowledge_graph_edges e "
            "JOIN knowledge_graph_nodes n ON n.id=e.target_id "
            "WHERE n.owner_id='owner-a' AND n.node_key=? AND e.relation='mentions'", (f"memory:{live}",)).fetchall()
        self.assertEqual([row["weight"] for row in weights], [0.2])

    def test_knowledge_hits_obey_current_trust_ttl_integrity_exact_id_and_version(self):
        fixture = Fixture(self)
        texts = ["Уязвимость CVE-2026-1000 затрагивает Python версии 3.11",
                 "Уязвимость CVE-2026-2000 затрагивает Python версии 3.11",
                 "Уязвимость CVE-2026-1000 затрагивает Python версии 3.12",
                 "Уязвимость CVE-2026-10000 затрагивает Python версии 3.11",
                 "Уязвимость CVE-2026-1000suffix затрагивает Python версии 3.11",
                 "Уязвимость CVE-2026-1000суффикс затрагивает Python версии 3.11"]
        identifiers = []
        for text in texts:
            identifier = fixture.knowledge(text)
            identifiers.append(identifier)
            fixture.link("knowledge", identifier, text)
        same = texts[0]
        for options in ({"status": "candidate"},
                        {"data_class": "dynamic", "checked_at": (NOW - timedelta(hours=1)).isoformat(), "ttl": 1},
                        {}):
            text = same + (" \ufffd" if not options else "")
            identifier = fixture.knowledge(text, **options)
            fixture.link("knowledge", identifier, text)
        fixture.no_sync()
        fixture.storage.db.execute("UPDATE knowledge SET confidence=0 WHERE id=?", (identifiers[0],))
        fixture.storage.db.commit()
        query = "Объясни уязвимость CVE-2026-1000 для Python версии 3.11"
        rows = fixture.graph.search("owner-a", query, limit=30)
        self.assertEqual([item.text for item in rows], [texts[0]])
        self.assertEqual(rows[0].source, "https://docs.python.org/source")
        self.assertAlmostEqual(rows[0].score, 0.2 + 0.22 * len(rows[0].related_concepts),
                               msg="Нулевой current confidence не заменяется default или cached weight")
        trusted = fixture.storage.db.execute("SELECT * FROM knowledge WHERE status='trusted'").fetchall()
        expected = fixture.knowledge_type(fixture.storage, vector_index=None)._search_rows(query, trusted, 30)
        self.assertIn(texts[0], [item["claim"] for item in expected])
        self.assertIn(texts[3], [item["claim"] for item in expected],
                      msg="Graph обязан усилить подстрочный фильтр KnowledgeBase для целого CVE")
        for text in ("Уязвимость GHSA-aaaa-bbbb-cccc затрагивает Python",
                     "Уязвимость GHSA-aaaa-bbbb-ccccd затрагивает Python"):
            identifier = fixture.knowledge(text)
            fixture.link("knowledge", identifier, text)
        ghsa = fixture.graph.search("owner-a", "Уязвимость GHSA-aaaa-bbbb-cccc Python", limit=30)
        self.assertEqual([item.text for item in ghsa], ["Уязвимость GHSA-aaaa-bbbb-cccc затрагивает Python"])
        # Синхронизация также не индексирует заведомо непригодные источники.
        sync = Fixture(self)
        sync.knowledge("Python актуальная запись", confidence=0)
        sync.knowledge("Python неподтверждённая запись", status="candidate")
        sync.knowledge("Python истёкшая запись", data_class="dynamic",
                       checked_at=(NOW - timedelta(hours=1)).isoformat(), ttl=1)
        sync.knowledge("Python повреждённая запись \ufffd")
        sync.memory("Истёкшая память", expiry=NOW.isoformat())
        sync.memory("Живая память", expiry="2026-11-19T12:00:01Z")
        # Более 1200/1000 непригодных источников не скрывают живой хвост.
        for index in range(1205):
            sync.knowledge(f"Python истёкший источник {index}", confidence=1.0,
                           data_class="dynamic", checked_at=(NOW - timedelta(hours=1)).isoformat(), ttl=1)
        for index in range(1005):
            sync.memory(f"Истёкший источник памяти {index}", importance=1.0, expiry=NOW.isoformat())
        self.assertEqual(sync.graph.sync(owner_id="owner-a"), {"knowledge": 1, "memories": 1})
        source_scans = [operation for operation in sync.storage.operations if
                        operation["sql"].startswith(("SELECT * FROM knowledge WHERE status=",
                                                     "SELECT id,content,importance,source,expires_at FROM memories"))]
        self.assertEqual(len(source_scans), 2)
        for operation in source_scans:
            self.assertNotIn("LIMIT", operation["sql"])
            self.assertTrue(operation["closed"])
            self.assertGreater(operation["rows"], 1000)
            self.assertLessEqual(max(operation["batches"]), 128)
        indexed = sync.storage.db.execute(
            "SELECT label FROM knowledge_graph_nodes WHERE kind IN ('knowledge','memory')").fetchall()
        self.assertEqual({row["label"] for row in indexed}, {"Python актуальная запись", "Живая память"})
        weights = sync.storage.db.execute(
            "SELECT DISTINCT e.weight FROM knowledge_graph_edges e "
            "JOIN knowledge_graph_nodes n ON n.id=e.target_id WHERE n.kind='knowledge'").fetchall()
        self.assertEqual([row["weight"] for row in weights], [0.2])
        self.assertEqual(sync.storage.db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0], 1209)
        self.assertEqual(sync.storage.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1007)

    def test_owner_b_is_synchronized_without_waiting_for_owner_a_cooldown(self):
        fixture = Fixture(self)
        fixture.knowledge("Python справочное знание")
        fixture.memory("Резервирование профиль альфа", owner="owner-a")
        fixture.memory("Резервирование профиль бета", owner="owner-b")
        first = fixture.graph.sync(owner_id="owner-a")
        self.assertEqual(first, {"knowledge": 1, "memories": 1})
        fixture.tick += 1
        self.assertEqual(fixture.graph.sync(owner_id="owner-b"), {"knowledge": 0, "memories": 1})
        self.assertEqual([row.text for row in fixture.graph.search("owner-b", "Резервирование")],
                         ["Резервирование профиль бета"])
        self.assertEqual(fixture.graph.sync(owner_id="owner-a"), {"knowledge": 0, "memories": 0})

    def test_failed_commit_does_not_delay_retry_or_publish_partial_index(self):
        fixture = Fixture(self)
        fixture.knowledge("Python справочное знание")
        fixture.memory("Резервирование личная память")
        original_link = fixture.graph._link_fact
        writes = []

        def link_after_source_close(*args, **kwargs):
            # Проверяем порядок на одном SQLite-соединении, не конкуренцию WAL.
            scans = [operation for operation in fixture.storage.operations if
                     operation["sql"].startswith(("SELECT * FROM knowledge WHERE status=",
                                                  "SELECT id,content,importance,source,expires_at FROM memories"))]
            self.assertTrue(scans, "До записи должен быть прочитан источник")
            self.assertTrue(all(operation.get("closed", False) for operation in scans),
                            "Курсоры источников должны быть закрыты до записи графа")
            writes.append(kwargs["fact_kind"])
            return original_link(*args, **kwargs)

        fixture.graph._link_fact = link_after_source_close
        fixture.storage.fail_next_commit = True
        self.assertEqual(fixture.graph.sync(owner_id="owner-a"), {"knowledge": 0, "memories": 0})
        self.assertEqual(fixture.storage.db.execute("SELECT COUNT(*) FROM knowledge_graph_nodes").fetchone()[0], 0)
        self.assertEqual(fixture.graph.sync(owner_id="owner-a"), {"knowledge": 1, "memories": 1})
        self.assertEqual(len(fixture.problems), 1)
        self.assertEqual(len(fixture.events), 1)
        self.assertEqual(writes, ["knowledge", "memory", "knowledge", "memory"])

    def test_private_shared_malformed_and_cross_owner_graph_hits_are_isolated(self):
        fixture = Fixture(self)
        owner_a, owner_b = "owner-" + "x" * 205 + "a", "owner-" + "x" * 205 + "b"
        a = fixture.memory("Профиль альфа закрытая память", owner=owner_a)
        b = fixture.memory("Профиль бета закрытая память", owner=owner_b)
        shared = fixture.memory("Профиль shared закрытая память", owner="shared")
        for owner, identifier, text in ((owner_a, a, "Профиль альфа закрытая память"),
                                        (owner_b, b, "Профиль бета закрытая память"),
                                        ("shared", shared, "Профиль shared закрытая память")):
            fixture.link("memory", identifier, text, owner=owner)
        db = fixture.storage.db
        concept = db.execute("SELECT id FROM knowledge_graph_nodes WHERE owner_id=? AND kind='concept' AND node_key='профиль'",
                             (owner_a,)).fetchone()[0]
        other = db.execute("SELECT id FROM knowledge_graph_nodes WHERE owner_id=? AND kind='memory'", (owner_b,)).fetchone()[0]
        db.execute("INSERT INTO knowledge_graph_edges(owner_id,source_id,target_id,relation,weight,evidence,updated_at) "
                   "VALUES(?,?,?,'mentions',9,'подставленная связь',?)", (owner_a, concept, other, NOW.isoformat()))
        for key, label in ((f"memory:+{a}", "Профиль альфа закрытая память"),
                           (f"knowledge:{a}", "Профиль альфа закрытая память"),
                           (f"memory:{b}", "Профиль бета закрытая память"),
                           ("memory:9223372036854775808", "Профиль ложный ID"),
                           ("memory:999999999999999999999999", "Профиль ложный ID")):
            cursor = db.execute("INSERT INTO knowledge_graph_nodes(owner_id,kind,node_key,label,payload_json,updated_at) "
                                "VALUES(?,'memory',?,?, '{}',?)", (owner_a, key, label, NOW.isoformat()))
            db.execute("INSERT INTO knowledge_graph_edges(owner_id,source_id,target_id,relation,weight,evidence,updated_at) "
                       "VALUES(?,?,?,'mentions',9,'подставленный узел',?)",
                       (owner_a, concept, cursor.lastrowid, NOW.isoformat()))
        db.commit()
        fixture.no_sync()
        self.assertEqual([row.text for row in fixture.graph.search(owner_a, "Профиль")], ["Профиль альфа закрытая память"])
        self.assertEqual([row.text for row in fixture.graph.search(owner_b, "Профиль")], ["Профиль бета закрытая память"])
        self.assertEqual([row.text for row in fixture.graph.search("shared", "Профиль")], ["Профиль shared закрытая память"])
        for owner, expected in ((owner_a, a), (owner_b, b)):
            self.assertEqual([item.memory_id for item in fixture.memory_type(fixture.storage).retrieve(owner, "Профиль")], [expected])

    def test_reindex_replaces_only_derived_mentions_and_keeps_source_rows(self):
        fixture = Fixture(self)
        identifier = fixture.memory("Староеслово резервирование")
        fixture.knowledge("Python неизменённая запись")
        fixture.graph.sync(owner_id="owner-a")
        db = fixture.storage.db
        fact = db.execute("SELECT id FROM knowledge_graph_nodes WHERE owner_id='owner-a' AND kind='memory'").fetchone()[0]
        old = db.execute("SELECT id FROM knowledge_graph_nodes WHERE owner_id='owner-a' AND kind='concept' "
                         "AND node_key='староеслово'").fetchone()[0]
        db.execute("INSERT INTO knowledge_graph_edges(owner_id,source_id,target_id,relation,weight,evidence,updated_at) "
                   "VALUES('owner-a',?,?,'custom',0.2,'сохранённая связь',?)", (old, fact, NOW.isoformat()))
        db.execute("UPDATE memories SET content='Новоеслово архивирование' WHERE id=?", (identifier,))
        db.commit()
        fixture.graph.sync(owner_id="owner-a", force=True)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge_graph_edges WHERE source_id=? AND target_id=? "
                                    "AND relation='mentions'", (old, fact)).fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge_graph_edges WHERE source_id=? AND target_id=? "
                                    "AND relation='custom'", (old, fact)).fetchone()[0], 1)
        self.assertEqual(fixture.graph.search("owner-a", "Староеслово"), [])
        self.assertEqual([row.text for row in fixture.graph.search("owner-a", "Архивирование")], ["Новоеслово архивирование"])
        self.assertEqual(db.execute("SELECT content FROM memories WHERE id=?", (identifier,)).fetchone()[0], "Новоеслово архивирование")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0], 1)

    def test_live_tail_source_is_not_lost_and_neighbor_source_reads_are_bounded(self):
        fixture = Fixture(self)
        db = fixture.storage.db
        db.executemany("INSERT INTO knowledge(topic,claim,source_url,source_title,confidence,status,data_class) "
                       "VALUES('фон','Нерелевантная запись','https://example.org','Источник',1,'trusted','static')",
                       [() for _ in range(1200)])
        db.commit()
        tail = fixture.knowledge("Маякхвоста живая запись", confidence=0.2)
        self.assertGreater(tail, 1200)
        fixture.link("knowledge", tail, "Маякхвоста живая запись")
        fixture.no_sync()
        self.assertEqual([row.text for row in fixture.graph.search("owner-a", "Маякхвоста")], ["Маякхвоста живая запись"])
        concepts = [f"concept{index:04d}" for index in range(16)]
        for concept in concepts:
            for index in range(24):
                text = f"{concept} запись{index:04d}"
                kid = fixture.knowledge(text)
                mid = fixture.memory(text)
                fixture.link("knowledge", kid, text)
                fixture.link("memory", mid, text)
        fixture.storage.operations.clear()
        rows = fixture.graph.search("owner-a", " ".join(concepts), limit=30)
        self.assertEqual(len(rows), 30)
        sources = [item for item in fixture.storage.operations if " id IN (" in item["sql"]
                   and ("FROM memories " in item["sql"] or "FROM knowledge " in item["sql"])]
        self.assertGreater(len(sources), 2, "Проверка должна действительно пройти несколько batch")
        self.assertTrue(all(len(item["parameters"]) <= 128 for item in sources))
        self.assertLessEqual(sum(item["rows"] for item in sources), 768)
        self.assertEqual(sum(item["rows"] for item in sources), 768)
        neighbors = [item for item in fixture.storage.operations if "FROM knowledge_graph_edges e" in item["sql"]]
        self.assertTrue(all(item["rows"] <= 24 for item in neighbors))
        self.assertLessEqual(sum(item["rows"] for item in neighbors), 768)


if __name__ == "__main__":
    unittest.main()
