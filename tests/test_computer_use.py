import unittest

from leshy.computer_use import (
    ComputerAction,
    ComputerUseController,
    ScreenFrame,
    ScreenUnderstanding,
)


class FakeObserver:
    def __init__(self):
        self.frames = [
            ScreenFrame(b"before", 100, 100, active_window="Browser"),
            ScreenFrame(b"after", 100, 100, active_window="Browser"),
        ]
        self.calls = 0

    def capture(self):
        frame = self.frames[min(self.calls, len(self.frames) - 1)]
        self.calls += 1
        return frame


class FakeAnalyzer:
    def analyze(self, frame):
        return ScreenUnderstanding(
            summary="Страница открыта",
            visible_text="Отправить",
            active_window=frame.active_window,
            confidence=0.95,
        )


class FakeExecutor:
    def __init__(self):
        self.actions = []

    def execute(self, action):
        self.actions.append(action)


class ComputerUseTests(unittest.TestCase):
    def test_action_is_never_blind_and_result_is_reobserved(self):
        observer = FakeObserver()
        executor = FakeExecutor()
        activity = []
        controller = ComputerUseController(
            observer,
            FakeAnalyzer(),
            executor,
            activity=lambda name, payload: activity.append((name, payload)),
            permission_check=lambda: True,
        )
        controller.start()
        result = controller.act(
            ComputerAction(
                kind="click",
                arguments={"x": 50, "y": 50},
                description="Нажимаю Отправить",
                expected_change="Сообщение должно появиться в чате",
            )
        )
        self.assertEqual(observer.calls, 2)
        self.assertEqual(len(executor.actions), 1)
        self.assertTrue(result.changed)
        self.assertTrue(result.verified)
        self.assertTrue(any(name == "computer.observing" for name, _ in activity))
        self.assertTrue(any(name == "computer.verified" for name, _ in activity))

    def test_high_risk_action_requires_approval(self):
        observer = FakeObserver()
        executor = FakeExecutor()
        controller = ComputerUseController(
            observer,
            FakeAnalyzer(),
            executor,
            approval=lambda action: False,
            permission_check=lambda: True,
        )
        controller.start()
        with self.assertRaises(PermissionError):
            controller.act(
                ComputerAction(
                    kind="submit_payment",
                    arguments={},
                    description="Подтверждаю оплату",
                    risk="high",
                )
            )
        self.assertEqual(observer.calls, 0)
        self.assertEqual(executor.actions, [])

    def test_computer_use_must_receive_explicit_permission_checker(self):
        controller = ComputerUseController(FakeObserver(), FakeAnalyzer(), FakeExecutor())
        with self.assertRaises(PermissionError):
            controller.start()

    def test_disabled_computer_use_cannot_observe_screen(self):
        controller = ComputerUseController(FakeObserver(), FakeAnalyzer(), FakeExecutor())
        with self.assertRaises(RuntimeError):
            controller.observe()

    def test_permission_revocation_blocks_next_desktop_action(self):
        allowed = {"value": True}
        executor = FakeExecutor()
        controller = ComputerUseController(
            FakeObserver(), FakeAnalyzer(), executor,
            permission_check=lambda: allowed["value"],
        )
        controller.start()
        allowed["value"] = False
        with self.assertRaises(RuntimeError):
            controller.act(ComputerAction(kind="click", arguments={}, description="click"))
        self.assertEqual(executor.actions, [])

    def test_custom_verifier_can_reject_wrong_visual_result(self):
        controller = ComputerUseController(
            FakeObserver(), FakeAnalyzer(), FakeExecutor(),
            permission_check=lambda: True,
        )
        controller.start()
        result = controller.act(
            ComputerAction(
                kind="type",
                arguments={"text": "Привет"},
                description="Ввожу текст",
                expected_change="Поле должно содержать Привет",
            ),
            verify=lambda before, after, action: (False, "Текст появился не в том поле"),
        )
        self.assertTrue(result.changed)
        self.assertFalse(result.verified)
        self.assertEqual(result.verification, "Текст появился не в том поле")


if __name__ == "__main__":
    unittest.main()
