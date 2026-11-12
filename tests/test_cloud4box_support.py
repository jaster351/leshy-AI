"""Behavioral tests for employee-style Cloud4box drafts and grounded facts."""
import unittest

from leshy.cloud4box_support import (
    CHECKED_ON,
    FACTS,
    is_support_draft_request,
    relevant_support_facts,
    support_draft_context,
)


class Cloud4boxSupportPolicyTests(unittest.TestCase):
    def test_explicit_forwarded_ticket_activates_support_mode(self):
        for request in (
            "/support У клиента не открывается консоль VPS",
            "Ответь клиенту: можно ли увеличить канал?",
            "Сообщение клиента: Cloud4box VPS недоступен",
            "Здравствуйте! У вас можно взять выделенный сервер?",
        ):
            with self.subTest(request=request):
                self.assertTrue(is_support_draft_request(request))

    def test_regular_chat_and_plain_ip_not_hijacked(self):
        for request in ("Привет, как дела?", "1.1.1.1", "Расскажи о музыке",
                        "Расскажи про VLESS в целом"):
            with self.subTest(request=request):
                self.assertFalse(is_support_draft_request(request))
                self.assertEqual(support_draft_context(request), "")

    def test_short_edit_inherits_only_nearby_support_context(self):
        current = "Убери приветствие"
        newest_first = [
            {"role": "user", "content": current},
            {"role": "assistant", "content": "Подготовил ответ."},
            {"role": "user", "content": "/support Клиент не может оплатить с лицевого счета"},
        ]
        self.assertTrue(is_support_draft_request(current, newest_first))
        self.assertFalse(is_support_draft_request(current, [
            {"role": "user", "content": current},
            {"role": "user", "content": "Как ухаживать за домашними растениями?"},
        ]))

    def test_reply_is_single_draft_no_phantom_operations(self):
        prompt = support_draft_context("/support Вопрос по гарантированному каналу")
        self.assertIn("ОДИН готовый ответ", prompt)
        self.assertIn("Не говори, что что-то проверено", prompt)
        self.assertIn("не добавляй подпись", prompt)
        self.assertIn("гарантирован", prompt)

    def test_outgoing_ports_exclude_old_unconfirmed_3389(self):
        rules = relevant_support_facts("ограничения исходящих портов 25 2525 5060")
        entry = next(x for x in rules if x.key == "egress_ports")
        self.assertIn("2525", entry.statement)
        self.assertIn("5060", entry.statement)
        self.assertIn("Не добавлять 3389", entry.statement)

    def test_refund_uses_official_policy_not_old_incorrect_rule(self):
        rules = relevant_support_facts("возврат денег при досрочном отказе")
        entry = next(x for x in rules if x.key == "refund")
        self.assertIn("неиспользованного периода", entry.statement)
        self.assertIn("суточный", entry.statement)
        self.assertTrue(entry.source.startswith("https://wiki.cloud4box.com/"))

    def test_domain_and_dynamic_status_facts_are_source_labeled(self):
        for fact in FACTS:
            self.assertTrue(fact.source)
            self.assertTrue(fact.level in {"official", "operator"})
            if fact.level == "official":
                self.assertTrue(fact.source.startswith("https://"))
        self.assertEqual(CHECKED_ON, "2026-11-12")
        self.assertTrue(next(x for x in FACTS if x.key == "vmmanager").temporary)
        self.assertTrue(next(x for x in FACTS if x.key == "starting_prices").temporary)

    def test_no_customer_credentials_or_private_ip_in_static_policy(self):
        text = "\n".join(x.statement for x in FACTS)
        for secret in ("QU4JI0", "jaster@", "vm762739", "139.28.169.217", "@yandex.ru"):
            self.assertNotIn(secret, text)

    def test_temporary_operational_notes_not_misrepresented_as_official(self):
        prompt = support_draft_context("/support Скажите, почему сейчас VMmanager не работает?")
        self.assertIn("оператор", prompt)
        self.assertIn("временные/меняющиеся данные — перепроверь", prompt)


if __name__ == "__main__":
    unittest.main()
