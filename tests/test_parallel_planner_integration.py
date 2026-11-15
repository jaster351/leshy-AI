import json
import unittest
from leshy.planner import Planner
from leshy.llm import ChatMessage

class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []
    def chat(self, messages):
        self.seen.append(list(messages))
        return next(self.responses)

class FakeTools:
    def __init__(self):
        self.calls = []
    def specs(self): return []
    def has(self, name): return False
    def execute(self, name, args, *, context):
        self.calls.append((name,args,context.owner_id,context.session_id))
        return json.dumps({"ok": True})

class PlannerIntegrationTests(unittest.TestCase):
    def test_parallel_batch_consumed_and_model_can_summarize(self):
        batch={"action":"parallel", "tasks":[
            {"id":"a","tool":"knowledge.search","arguments":{"query":"a"}},
            {"id":"b","tool":"knowledge.search","arguments":{"query":"b"}},
        ]}
        llm=FakeLLM([json.dumps(batch),'{"action":"final","answer":"Готово"}'])
        tools=FakeTools()
        answer=Planner(llm,tools).run('system',[ChatMessage('user','request')],owner_id='web:owner',session_id='s1')
        self.assertEqual(answer, 'Готово')
        self.assertEqual(len(tools.calls),2)
        self.assertTrue(all(c[2:]==('web:owner','s1') for c in tools.calls))
        self.assertIn('Результаты параллельных',llm.seen[1][-1].content)

    def test_unsafe_parallel_call_aborts_before_any_tool(self):
        llm=FakeLLM([json.dumps({'action':'parallel','tasks':[
            {'id':'a','tool':'workspace.write','arguments':{'path':'x','content':'bad'}}
        ]})])
        tools=FakeTools()
        answer=Planner(llm,tools).run('system',[ChatMessage('user','write')])
        self.assertIn('Некорректный параллельный план',answer)
        self.assertEqual(tools.calls,[])

    def test_same_tool_sequential_then_parallel_deduplicates(self):
        llm=FakeLLM([
            '{"action":"tool","tool":"knowledge.search","arguments":{"query":"a"}}',
            json.dumps({'action':'parallel','tasks':[{'id':'a','tool':'knowledge.search','arguments':{'query':'a'}}]}),
        ])
        tools=FakeTools()
        answer=Planner(llm,tools).run('system',[ChatMessage('user','look')])
        self.assertIn('Повторяющийся',answer)
        self.assertEqual(len(tools.calls),1)

    def test_sequential_tool_behavior_still_works(self):
        llm=FakeLLM(['{"action":"tool","tool":"knowledge.search","arguments":{"query":"one"}}',
                     '{"action":"final","answer":"OK"}'])
        tools=FakeTools()
        self.assertEqual(Planner(llm,tools).run('system',[ChatMessage('user','hello')]),'OK')
        self.assertEqual(len(tools.calls),1)

if __name__ == '__main__':
    unittest.main()
