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

if __name__=='__main__':unittest.main()
