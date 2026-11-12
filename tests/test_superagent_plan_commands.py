"""Plan commands keep the owner's ticket and never trigger automatic tools."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from leshy.superagent_plan_bridge import SuperAgentPlanBridge
from leshy.superagent_plan_commands import execute_plan_command


class FakeService:
    def __init__(self, path):
        self.storage=SimpleNamespace(path=path)
        self.tickets={'SA-test':{'id':'SA-test','owner_id':'web:alice','state':'queued'}}
        self.plans=SuperAgentPlanBridge(self)
    def get(self, ticket_id, *, owner_id):
        value=self.tickets[ticket_id]
        if value['owner_id'] != owner_id: raise PermissionError('Wrong owner')
        return value


class PlanCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service=FakeService(Path(self.tmp.name)/'brain.db')
        self.steps='[{"id":"inspect","title":"Review docs"},{"id":"check","title":"Run checks","depends_on":["inspect"]}]'

    def test_create_status_pause_resume_cancel(self):
        created=execute_plan_command(self.service,'plan_create','SA-test','web:alice',self.steps)
        self.assertIn('не запущены автоматически',created)
        status=execute_plan_command(self.service,'plan_status','SA-test','web:alice')
        self.assertIn('inspect: queued',status)
        self.assertIn('check: queued',status)
        for action,expected in [('plan_pause','paused'),('plan_resume','active'),('plan_cancel','cancelled')]:
            answer=execute_plan_command(self.service,action,'SA-test','web:alice')
            self.assertIn(expected,answer)

    def test_wrong_owner_can_neither_create_nor_read(self):
        with self.assertRaises(PermissionError):
            execute_plan_command(self.service,'plan_create','SA-test','web:other',self.steps)
        execute_plan_command(self.service,'plan_create','SA-test','web:alice',self.steps)
        with self.assertRaises(PermissionError):
            execute_plan_command(self.service,'plan_status','SA-test','web:other')

    def test_invalid_payload_does_not_create_plan(self):
        for value in ['not-json','{"steps":"bad"}','[]']:
            with self.assertRaises((ValueError, TypeError)):
                execute_plan_command(self.service,'plan_create','SA-test','web:alice',value)
        with self.assertRaises(KeyError):
            execute_plan_command(self.service,'plan_status','SA-test','web:alice')

    def test_unrecognized_command_is_not_executed(self):
        with self.assertRaisesRegex(ValueError,'Неизвестная'):
            execute_plan_command(self.service,'plan_delete','SA-test','web:alice')

if __name__ == '__main__':unittest.main()
