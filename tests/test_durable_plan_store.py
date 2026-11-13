import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from leshy.durable_plan_store import DurablePlanStore, PlanError


class DurableStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'brain.sqlite3'
        self.store = DurablePlanStore(self.path)
        self.steps = [{'id':'research','title':'Read docs'},
                      {'id':'design','title':'Design solution'},
                      {'id':'implement','title':'Write code','depends_on':['research','design']},
                      {'id':'verify','title':'Review and test','depends_on':['implement']}]

    def test_parallel_claims_obey_dependencies_after_restart(self):
        key = self.store.create('alice', self.steps)
        first = self.store.claim(key,'alice',limit=8,now=10)
        self.assertEqual({x['id'] for x in first}, {'research','design'})
        self.assertEqual(DurablePlanStore(self.path).claim(key,'alice',now=11), [])
        for c in first:
            self.assertTrue(self.store.complete(key,'alice',c['id'],c['token'],success=True,now=12))
        next_step = DurablePlanStore(self.path).claim(key,'alice',now=20)
        self.assertEqual([x['id'] for x in next_step], ['implement'])
        self.store.complete(key,'alice','implement',next_step[0]['token'],success=True,now=21)
        last = self.store.claim(key,'alice',now=22)
        self.store.complete(key,'alice','verify',last[0]['token'],success=True,now=23)
        self.assertEqual(DurablePlanStore(self.path).snapshot(key,'alice')['state'], 'completed')

    def test_atomic_concurrent_claim_across_store_instances(self):
        key = self.store.create('alice',[{'id':'s1','title':'One'}])
        def claim():
            return DurablePlanStore(self.path).claim(key,'alice',now=100)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            picks = list(pool.map(lambda _: claim(), range(12)))
        self.assertEqual(sum(len(x) for x in picks),1)

    def test_expired_lease_retries_but_rejects_stale_worker(self):
        key=self.store.create('alice',[{'id':'s','title':'Step','max_attempts':2}])
        first=self.store.claim(key,'alice',now=0,lease_seconds=10)[0]
        second=DurablePlanStore(self.path).claim(key,'alice',now=11,lease_seconds=10)[0]
        self.assertNotEqual(first['token'],second['token'])
        self.assertFalse(self.store.complete(key,'alice','s',first['token'],success=True,now=12))
        self.assertTrue(self.store.complete(key,'alice','s',second['token'],success=True,now=12))

    def test_repeated_crashes_fail_after_budget(self):
        key=self.store.create('alice',[{'id':'s','title':'Step','max_attempts':2},
                                       {'id':'dep','title':'Dependent','depends_on':['s']}])
        self.store.claim(key,'alice',now=0,lease_seconds=10)
        self.store.claim(key,'alice',now=11,lease_seconds=10)
        self.assertEqual(self.store.claim(key,'alice',now=22), [])
        snapshot=self.store.snapshot(key,'alice')
        self.assertEqual(snapshot['state'],'failed')
        self.assertEqual([x['state'] for x in snapshot['steps']], ['failed','blocked'])

    def test_heartbeat_extends_and_expired_token_does_not(self):
        key=self.store.create('alice',[{'id':'s','title':'S'}])
        item=self.store.claim(key,'alice',now=0,lease_seconds=10)[0]
        self.assertTrue(self.store.heartbeat(key,'alice','s',item['token'],now=5,lease_seconds=20))
        self.assertFalse(self.store.heartbeat(key,'alice','s','bad-token',now=6))
        self.assertTrue(self.store.complete(key,'alice','s',item['token'],success=True,now=24))

    def test_failure_branch_does_not_stop_independent_work(self):
        key=self.store.create('alice',[
            {'id':'first','title':'First','max_attempts':1},
            {'id':'independent','title':'Independent'},
            {'id':'blocked','title':'Blocked','depends_on':['first']},
        ])
        claims=self.store.claim(key,'alice',limit=1,now=0)
        self.assertEqual([x['id'] for x in claims],['first'])
        self.store.complete(key,'alice','first',claims[0]['token'],success=False,now=1)
        self.assertEqual(self.store.snapshot(key,'alice')['state'],'active')
        remaining=self.store.claim(key,'alice',now=2)
        self.assertEqual([x['id'] for x in remaining],['independent'])
        self.store.complete(key,'alice','independent',remaining[0]['token'],success=True,now=3)
        result=self.store.snapshot(key,'alice')
        self.assertEqual(result['state'],'failed')
        self.assertEqual([x['state'] for x in result['steps']],['failed','done','blocked'])

    def test_cancel_rejects_late_complete(self):
        key=self.store.create('alice',[{'id':'s','title':'Step'}])
        claim=self.store.claim(key,'alice',now=0)[0]
        self.store.control(key,'alice','cancel')
        self.assertFalse(self.store.complete(key,'alice','s',claim['token'],success=True,now=1))
        self.assertEqual(self.store.snapshot(key,'alice')['state'],'cancelled')

    def test_pause_resume_and_owner_isolation(self):
        key=self.store.create('alice',[{'id':'s','title':'Step'}])
        with self.assertRaises(PermissionError):self.store.snapshot(key,'bob')
        self.store.control(key,'alice','pause')
        self.assertEqual(self.store.claim(key,'alice'),[])
        self.store.control(key,'alice','resume')
        self.assertEqual(len(self.store.claim(key,'alice')),1)

    def test_validate_cycles_unknown_dependencies_and_duplicate_ids(self):
        for steps in (
            [{'id':'a','title':'a','depends_on':['b']},{'id':'b','title':'b','depends_on':['a']}],
            [{'id':'a','title':'a','depends_on':['missing']}],
            [{'id':'a','title':'a'},{'id':'a','title':'other'}],
        ):
            with self.assertRaises(PlanError): self.store.create('alice', steps)

    def test_failed_step_retry_does_not_rerun_completed_peers(self):
        key=self.store.create('alice',[{'id':'a','title':'A'},{'id':'b','title':'B'}])
        a,b=self.store.claim(key,'alice',now=0)
        self.store.complete(key,'alice',a['id'],a['token'],success=True,now=1)
        self.store.complete(key,'alice',b['id'],b['token'],success=False,now=1)
        next_claim=self.store.claim(key,'alice',now=2)
        self.assertEqual([x['id'] for x in next_claim],[b['id']])


    def test_wide_plan_can_claim_more_than_eight_independent_steps(self):
        count = 96
        steps = [{'id': f'job{i}', 'title': f'Parallel job {i}'} for i in range(count)]
        key = self.store.create('alice', steps)
        claims = self.store.claim(key, 'alice', limit=count, now=10)
        self.assertEqual(len(claims), count)
        self.assertEqual(len({item['token'] for item in claims}), count)
        # All independent work may finish; no artificial plan size or claim ceiling.
        for item in claims:
            self.assertTrue(self.store.complete(
                key, 'alice', item['id'], item['token'], success=True, now=11))
        snapshot = DurablePlanStore(self.path).snapshot(key, 'alice')
        self.assertEqual(snapshot['state'], 'completed')
        self.assertEqual(len(snapshot['steps']), count)

    def test_large_dependency_chain_is_validated_without_recursive_walk(self):
        count = 512
        steps = [
            {'id': f's{i}', 'title': f'Step {i}',
             'depends_on': [f's{i - 1}'] if i else []}
            for i in range(count)
        ]
        key = self.store.create('alice', steps)
        claims = self.store.claim(key, 'alice', limit=count, now=10)
        self.assertEqual([item['id'] for item in claims], ['s0'])
        first = claims[0]
        self.assertTrue(self.store.complete(
            key, 'alice', first['id'], first['token'], success=True, now=11))
        self.assertEqual(
            [item['id'] for item in self.store.claim(key, 'alice', limit=count, now=12)],
            ['s1'],
        )

    def test_unbounded_claim_count_still_rejects_invalid_types(self):
        key = self.store.create('alice', [{'id': 'one', 'title': 'One'}])
        for limit in (0, -1, True, 1.0, '8'):
            with self.subTest(limit=limit):
                with self.assertRaises(PlanError):
                    self.store.claim(key, 'alice', limit=limit)


    def test_deep_failure_blocks_descendants_but_preserves_independent_step(self):
        count = 1536
        steps = [
            {'id': f'n{i}', 'title': f'Node {i}',
             'max_attempts': 1 if i == 0 else 3,
             'depends_on': [f'n{i - 1}'] if i else []}
            for i in range(count)
        ] + [{'id': 'independent', 'title': 'Independent'}]
        key = self.store.create('alice', steps)
        first = self.store.claim(key, 'alice', limit=1, now=10)[0]
        self.assertEqual(first['id'], 'n0')
        self.assertTrue(self.store.complete(
            key, 'alice', 'n0', first['token'], success=False, now=11))
        snapshot = self.store.snapshot(key, 'alice')
        self.assertEqual(snapshot['state'], 'active')
        self.assertEqual(snapshot['steps'][-2]['state'], 'blocked')
        self.assertEqual(snapshot['steps'][-1]['state'], 'queued')
        claimed = self.store.claim(key, 'alice', now=12)
        self.assertEqual([item['id'] for item in claimed], ['independent'])
        self.store.complete(key, 'alice', 'independent',
                            claimed[0]['token'], success=True, now=13)
        self.assertEqual(self.store.snapshot(key, 'alice')['state'], 'failed')

    def test_expired_lease_propagates_failure_through_large_chain(self):
        count = 512
        steps = [
            {'id': f'n{i}', 'title': f'Node {i}',
             'max_attempts': 1 if i == 0 else 3,
             'depends_on': [f'n{i - 1}'] if i else []}
            for i in range(count)
        ]
        key = self.store.create('alice', steps)
        first = self.store.claim(key, 'alice', now=0, lease_seconds=5)[0]
        self.assertEqual(self.store.claim(key, 'alice', now=6), [])
        snapshot = self.store.snapshot(key, 'alice')
        self.assertEqual(snapshot['state'], 'failed')
        self.assertEqual(snapshot['steps'][-1]['state'], 'blocked')
        self.assertFalse(self.store.complete(
            key, 'alice', 'n0', first['token'], success=True, now=7))


    def test_explicit_safe_read_specs_survive_restart_and_dependency_claims(self):
        steps = [
            {'id': 'version', 'title': 'Read version',
             'tool': 'runtime.version', 'arguments': {}},
            {'id': 'search', 'title': 'Trusted knowledge',
             'tool': 'knowledge.search', 'arguments': {'query': '  useful   knowledge   facts  '},
             'depends_on': ['version']},
        ]
        key = self.store.create('alice', steps)
        first = DurablePlanStore(self.path).claim(key, 'alice', now=10)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['tool'], 'runtime.version')
        self.assertEqual(first[0]['arguments'], {})
        self.assertTrue(self.store.complete(key, 'alice', 'version',
                         first[0]['token'], success=True, now=11))
        second = DurablePlanStore(self.path).claim(key, 'alice', now=12)
        self.assertEqual(second[0]['tool'], 'knowledge.search')
        self.assertEqual(second[0]['arguments'], {'query': 'useful knowledge facts'})
        self.assertEqual(DurablePlanStore(self.path).snapshot(key, 'alice')['steps'][1]['tool'],
                         'knowledge.search')

    def test_manual_descriptions_stay_inert_and_unapproved_tools_are_denied(self):
        key = self.store.create('alice', [{'id': 'manual', 'title': 'Run arbitrary command'}])
        claim = self.store.claim(key, 'alice', now=10)[0]
        self.assertEqual((claim['tool'], claim['arguments']), ('', {}))
        for tool, args in (
            ('workspace.write', {'path': 'file'}),
            ('shell.exec', {'command': 'echo hi'}),
            ('runtime.version', {'command': 'echo hi'}),
            ('knowledge.search', {'query': 'ab'}),
            ('knowledge.search', {'query': 'some query', 'action': 'write'}),
        ):
            with self.subTest(tool=tool, arguments=args):
                with self.assertRaises(PlanError):
                    self.store.create('alice', [
                        {'id': 'bad', 'title': 'Unsafe', 'tool': tool, 'arguments': args},
                    ])

    def test_migrates_legacy_plan_schema_without_resetting_lease(self):
        import sqlite3
        legacy = Path(self.temp.name) / 'old-plan.sqlite'
        with sqlite3.connect(legacy) as conn:
            conn.executescript("""
                CREATE TABLE durable_plans(
                    plan_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    state TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE durable_plan_steps(
                    plan_id TEXT NOT NULL, step_id TEXT NOT NULL,
                    title TEXT NOT NULL, deps_json TEXT NOT NULL,
                    state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL, lease_token TEXT,
                    lease_until REAL NOT NULL DEFAULT 0, output TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (plan_id,step_id));
                INSERT INTO durable_plans VALUES('p','alice','active',1);
                INSERT INTO durable_plan_steps VALUES(
                    'p','old','Historical job','[]','running',1,3,'old-lease',9999999999,'previous output');
            """)
        migrated = DurablePlanStore(legacy)
        snapshot = migrated.snapshot('p', 'alice')
        self.assertEqual(snapshot['steps'][0]['state'], 'running')
        self.assertEqual(snapshot['steps'][0]['output'], 'previous output')
        self.assertEqual(snapshot['steps'][0]['tool'], '')
        self.assertEqual(snapshot['steps'][0]['arguments'], {})
        self.assertTrue(migrated.complete(
            'p', 'alice', 'old', 'old-lease', success=True, now=10))


if __name__=='__main__':unittest.main()
