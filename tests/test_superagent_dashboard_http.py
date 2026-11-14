"""HTTP routing checks without loading the real LLM, Telegram or browser."""
import importlib
import io
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_superagent_dashboard import FakeService


class BaseHandler:
    def __init__(self, *, path, owner='web:alice', authorized=True, headers=None, body=None):
        self.path = path
        self.owner = owner
        self.authorized = authorized
        self.headers = headers or {}
        self.request_body = body or {}
        self.result = None
        self.response_headers = {}
        self.wfile = io.BytesIO()

    def _authorized(self):
        return self.authorized

    def _cookie_value(self, name):
        return getattr(self, "cookie_value", "")

    def _existing_web_owner(self):
        return self.owner

    def _json(self, status, payload):
        self.result = (int(status), payload)

    def _body(self):
        return self.request_body

    def _identity_context(self, owner):
        return ('web:alice', 'cookie-secret', 'hash')

    def _identity_cookie_header(self, cookie):
        return 'leshy=' + cookie if cookie else ''

    def send_response(self, status):
        self.result = (int(status), None)

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass

    def do_GET(self):
        self.result = (404, {'error':'parent GET'})

    def do_POST(self):
        self.result = (404, {'error':'parent POST'})


class HttpRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stubs = {
            'leshy.web_server_impl13': types.ModuleType('leshy.web_server_impl13'),
            'leshy.web_server_impl11': types.ModuleType('leshy.web_server_impl11'),
            'leshy.module_settings': types.ModuleType('leshy.module_settings'),
            'leshy.diagnostics': types.ModuleType('leshy.diagnostics'),
        }
        stubs['leshy.web_server_impl13'].make_handler = lambda *args: BaseHandler
        stubs['leshy.web_server_impl13'].serve = lambda **kwargs: None
        stubs['leshy.web_server_impl11']._runtime_html = lambda settings, owner='': b'<html><body><h1>Leshy</h1></body></html>'
        stubs['leshy.module_settings'].module_enabled = lambda storage, name: True
        stubs['leshy.diagnostics'].problem = lambda *args: None
        cls.patch = patch.dict(sys.modules, stubs)
        cls.patch.start()
        cls.module = importlib.import_module('leshy.web_server_impl14')
        cls.app = SimpleNamespace(storage=object(), superagent=FakeService())
        cls.Handler = cls.module.make_handler(cls.app, None, None, None)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop('leshy.web_server_impl14', None)
        cls.patch.stop()

    def test_owner_cookie_required_even_if_api_token_not_configured(self):
        h = self.Handler(path='/v1/superagent/tasks', owner='')
        h.do_GET()
        self.assertEqual(h.result[0], 401)

    def test_get_returns_only_session_owner_tickets(self):
        h = self.Handler(path='/v1/superagent/tasks', owner='web:alice')
        h.do_GET()
        self.assertEqual(h.result[0], 200)
        self.assertEqual([x['id'] for x in h.result[1]['tasks']], ['SA-123'])

    def test_post_rejects_missing_custom_header_and_cross_origin(self):
        for headers in ({'Content-Type':'application/json'},
                        {'Content-Type':'application/json','X-Leshy-UI':'superagent',
                         'Origin':'https://attacker.invalid','Host':'localhost:8080'}):
            h = self.Handler(path='/v1/superagent/task', owner='web:alice', headers=headers,
                             body={'action':'stop','id':'SA-123'})
            h.do_POST()
            self.assertEqual(h.result[0], 403)

    def test_post_requires_web_session_and_server_owner(self):
        headers={'Content-Type':'application/json','X-Leshy-UI':'superagent','Host':'localhost'}
        h = self.Handler(path='/v1/superagent/task', owner='', headers=headers,
                         body={'action':'stop','id':'SA-123','owner_id':'web:alice'})
        h.do_POST()
        self.assertEqual(h.result[0], 401)
        h = self.Handler(path='/v1/superagent/task', owner='web:bob', headers=headers,
                         body={'action':'stop','id':'SA-123','owner_id':'web:alice'})
        h.do_POST()
        self.assertEqual(h.result[0], 400)

    def test_authorized_stop_and_get_details(self):
        headers={'Content-Type':'application/json','X-Leshy-UI':'superagent','Host':'localhost'}
        h = self.Handler(path='/v1/superagent/task', owner='web:alice', headers=headers,
                         body={'action':'stop','id':'SA-123'})
        h.do_POST()
        self.assertEqual(h.result[0], 200)
        self.assertEqual(h.result[1]['task']['state'], 'stopped')
        h2 = self.Handler(path='/v1/superagent/tasks?id=SA-123', owner='web:alice')
        h2.do_GET()
        self.assertEqual(h2.result[0], 200)

    def test_archive_listing_mode_and_invalid_filter(self):
        archived = self.Handler(path='/v1/superagent/tasks?archived=1', owner='web:alice')
        archived.do_GET()
        self.assertEqual(archived.result[0], 200)
        self.assertEqual(archived.result[1]['tasks'], [])

        invalid = self.Handler(path='/v1/superagent/tasks?archived=unknown', owner='web:alice')
        invalid.do_GET()
        self.assertEqual(invalid.result[0], 400)

    def test_remote_console_requires_session_and_provider_login(self):
        anonymous = self.Handler(path='/v1/superagent/remote-access', owner='')
        anonymous.do_GET()
        self.assertEqual(anonymous.result[0], 401)
        unverified = self.Handler(path='/v1/superagent/remote-access', owner='web:alice')
        unverified.do_GET()
        self.assertEqual(unverified.result[0], 403)

    def test_remote_revoke_deny_by_default_no_identity_or_cross_site(self):
        denied = self.Handler(
            path='/v1/superagent/remote-access/revoke', owner='web:alice',
            headers={'X-Leshy-UI': 'superagent', 'Content-Type': 'application/json'},
            body={'node_id': '192.168.1.9', 'grantee_id': 'bob', 'capability': 'inspect'})
        denied.do_POST()
        self.assertEqual(denied.result[0], 403)

    def test_remote_inventory_and_owner_revoke_http_routes(self):
        import hashlib
        import time
        from unittest.mock import Mock
        from test_superagent_remote_permissions import Storage
        from leshy.superagent_remote_permissions import RemotePermissionPolicy

        original_storage = self.app.storage
        original_identity = getattr(self.app, 'owner_identity', None)
        storage = Storage()
        identity = Mock(storage=storage, cookie_name='leshy')
        identity.owner_for_web_cookie.side_effect = (
            lambda cookie: 'web:alice' if cookie == 'session-a' else '')
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO owner_web_sessions VALUES (?,?,?)",
                (hashlib.sha256(b'session-a').hexdigest(), 'web:alice', time.time() + 600))
            conn.execute(
                "INSERT INTO owner_identity_links VALUES (?,?,?)",
                ('github', 'web:alice', 'verified-alice'))
        policy = RemotePermissionPolicy(
            identity, enrollment_verifier=lambda *args: True)
        actor = policy.actor_from_web_cookie('session-a')
        policy.register_verified_node(
            actor, '192.168.100.22', 'a' * 64, enrollment_proof='test-only')
        policy.grant(actor, '192.168.100.22', 'web:bob', 'inspect')
        self.app.storage = storage
        self.app.owner_identity = identity
        try:
            get = self.Handler(path='/v1/superagent/remote-access', owner='web:alice')
            get.cookie_value = 'session-a'
            get.do_GET()
            self.assertEqual(get.result[0], 200)
            self.assertEqual(get.result[1]['nodes'][0]['node_id'], '192.168.100.22')
            self.assertEqual(len(get.result[1]['active_grants']), 1)

            from unittest.mock import patch
            with patch('leshy.web_server_impl14.list_credential_file_names',
                       return_value={'configured':True,'available':True,
                                     'files':['host.json']}) as picker:
                files = self.Handler(
                    path='/v1/superagent/ssh/credentials', owner='web:alice')
                files.cookie_value = 'session-a'
                files.do_GET()
                self.assertEqual(files.result[0], 200)
                self.assertEqual(files.result[1]['files'], ['host.json'])
                picker.assert_called_once_with(storage, 'web:alice')
            ssh_payload = {'node_id':'192.168.100.22',
                           'credential_file':'host.json','confirm':True}
            with patch('leshy.web_server_impl14.inspect_remote_os',
                       return_value={'node_id':'192.168.100.22',
                           'os_family':'Linux','verified':True,
                           'method':'ssh:uname','host_key_sha256':'a'*64}) as check:
                wrong_origin = self.Handler(
                    path='/v1/superagent/ssh/inspect', owner='web:alice',
                    headers={'X-Leshy-UI':'superagent','Content-Type':'application/json',
                             'Origin':'https://evil.invalid','Host':'localhost'},
                    body=ssh_payload)
                wrong_origin.cookie_value = 'session-a'
                wrong_origin.do_POST()
                self.assertEqual(wrong_origin.result[0], 403)
                no_confirmation = self.Handler(
                    path='/v1/superagent/ssh/inspect', owner='web:alice',
                    headers={'X-Leshy-UI':'superagent','Content-Type':'application/json'},
                    body={**ssh_payload,'confirm':False})
                no_confirmation.cookie_value = 'session-a'
                no_confirmation.do_POST()
                self.assertEqual(no_confirmation.result[0], 403)
                check.assert_not_called()
                inspect = self.Handler(
                    path='/v1/superagent/ssh/inspect', owner='web:alice',
                    headers={'X-Leshy-UI':'superagent','Content-Type':'application/json'},
                    body=ssh_payload)
                inspect.cookie_value = 'session-a'
                inspect.do_POST()
                self.assertEqual(inspect.result[0], 200)
                self.assertEqual(inspect.result[1]['inspection']['os_family'], 'Linux')
                self.assertNotIn('host_key_sha256',
                                 inspect.result[1]['inspection'])
                check.assert_called_once()
                self.assertEqual(check.call_args.args[1].owner_id, 'web:alice')

            from unittest.mock import patch
            headers = {'X-Leshy-UI':'superagent', 'Content-Type':'application/json',
                       'Host':'localhost'}
            with patch('leshy.web_server_impl14.enroll_verified_ssh_node',
                       return_value={'node_id':'192.168.100.23','enrolled':True}) as enroll:
                invalid = self.Handler(
                    path='/v1/superagent/ssh/enroll', owner='web:alice',
                    headers=headers,
                    body={'node_id':'192.168.100.23','credential_file':'server.json',
                          'expected_fingerprint':'a'*64,'confirm':False})
                invalid.cookie_value = 'session-a'
                invalid.do_POST()
                self.assertEqual(invalid.result[0], 400)
                enroll.assert_not_called()
                valid = self.Handler(
                    path='/v1/superagent/ssh/enroll', owner='web:alice',
                    headers=headers,
                    body={**invalid.request_body, 'confirm':True})
                valid.cookie_value = 'session-a'
                valid.do_POST()
                self.assertEqual(valid.result[0], 200)
                enroll.assert_called_once()
                self.assertEqual(enroll.call_args.args[1].owner_id, 'web:alice')
            preview = {'node_id':'192.168.100.22',
                       'credential_file':'server.json','command':'sudo systemctl status demo'}
            with patch('leshy.web_server_impl14.prepare_ssh_command',
                       return_value={'approval_token':'test-token', 'sha256':'a'*64}) as prepare:
                pre = self.Handler(
                    path='/v1/superagent/ssh/prepare', owner='web:alice',
                    headers=headers, body=preview)
                pre.cookie_value = 'session-a'
                pre.do_POST()
                self.assertEqual(pre.result[0], 200)
                prepare.assert_called_once()
                self.assertEqual(prepare.call_args.args[1].owner_id, 'web:alice')
            execution = {**preview, 'approval_token':'test-token','confirm':True}
            with patch('leshy.web_server_impl14.consume_ssh_approval') as consume, patch(
                'leshy.web_server_impl14.run_ssh_terminal_command',
                return_value={'node_id':'192.168.100.22','os_family':'Linux',
                              'exit_code':0,'stdout':'active','stderr':'','truncated':False}
            ) as run:
                bad_origin = self.Handler(
                    path='/v1/superagent/ssh/execute', owner='web:alice',
                    headers={**headers,'Origin':'https://evil.invalid'}, body=execution)
                bad_origin.cookie_value = 'session-a'
                bad_origin.do_POST()
                self.assertEqual(bad_origin.result[0], 403)
                consume.assert_not_called()
                no_approval = self.Handler(
                    path='/v1/superagent/ssh/execute', owner='web:alice',
                    headers=headers,body={**execution,'confirm':False})
                no_approval.cookie_value = 'session-a'
                no_approval.do_POST()
                self.assertEqual(no_approval.result[0], 400)
                consume.assert_not_called()
                allowed = self.Handler(
                    path='/v1/superagent/ssh/execute', owner='web:alice',
                    headers=headers, body=execution)
                allowed.cookie_value = 'session-a'
                allowed.do_POST()
                self.assertEqual(allowed.result[0], 200)
                self.assertEqual(allowed.result[1]['execution']['stdout'], 'active')
                consume.assert_called_once()
                run.assert_called_once()
                self.assertEqual(run.call_args.args[1].owner_id, 'web:alice')

            checked = {
                'node_id':'192.168.100.22',
                'credential_file':'server.json',
                'command':'systemctl restart nginx',
                'verification_command':'systemctl is-active nginx',
                'expected_text':'active',
            }
            with patch('leshy.web_server_impl14.prepare_ssh_checked_step',
                       return_value={
                           'approval_token':'one-time','step_sha256':'a'*64
                       }) as prepare_checked:
                checked_prepare = self.Handler(
                    path='/v1/superagent/ssh/check/prepare', owner='web:alice',
                    headers=headers, body=checked)
                checked_prepare.cookie_value = 'session-a'
                checked_prepare.do_POST()
                self.assertEqual(checked_prepare.result[0], 200)
                self.assertEqual(prepare_checked.call_args.args[1].owner_id, 'web:alice')
                foreign = self.Handler(
                    path='/v1/superagent/ssh/check/prepare', owner='web:alice',
                    headers={**headers,'Origin':'https://evil.invalid'},
                    body=checked)
                foreign.cookie_value = 'session-a'
                foreign.do_POST()
                self.assertEqual(foreign.result[0], 403)
                prepare_checked.assert_called_once()

            completed = {
                'node_id':'192.168.100.22', 'os_family':'Linux',
                'verdict':'confirmed',
                'action':{'exit_code':0,'stdout':'','stderr':'','truncated':False},
                'verification':{'exit_code':0,'stdout':'active','stderr':'','truncated':False},
            }
            with patch('leshy.web_server_impl14.consume_ssh_checked_approval') as consume_checked, patch(
                'leshy.web_server_impl14.run_ssh_checked_step',
                return_value=completed,
            ) as execute_checked:
                no_confirm = self.Handler(
                    path='/v1/superagent/ssh/check/execute', owner='web:alice',
                    headers=headers,
                    body={**checked,'approval_token':'one-time','confirm':False})
                no_confirm.cookie_value = 'session-a'
                no_confirm.do_POST()
                self.assertEqual(no_confirm.result[0], 400)
                consume_checked.assert_not_called()
                checked_execute = self.Handler(
                    path='/v1/superagent/ssh/check/execute', owner='web:alice',
                    headers=headers,
                    body={**checked,'approval_token':'one-time','confirm':True})
                checked_execute.cookie_value = 'session-a'
                checked_execute.do_POST()
                self.assertEqual(checked_execute.result[0], 200)
                self.assertEqual(
                    checked_execute.result[1]['checked_step']['verdict'], 'confirmed'
                )
                consume_checked.assert_called_once()
                execute_checked.assert_called_once()
                self.assertEqual(execute_checked.call_args.args[1].owner_id, 'web:alice')

            # Entire-plan authorization is separate from single command approvals.
            ssh_plan = {
                "node_id": "192.168.100.22", "credential_file": "server.json",
                "steps": [
                    {"command": "systemctl restart nginx",
                     "verification_command": "systemctl is-active nginx",
                     "expected_text": "active"},
                    {"command": "systemctl restart redis",
                     "verification_command": "systemctl is-active redis",
                     "expected_text": "active"},
                ],
            }
            with patch(
                'leshy.web_server_impl14.prepare_ssh_plan',
                return_value={'approval_token':'single-use',
                              'plan_sha256':'f'*64,'steps':2}
            ) as plan_prepare:
                bad = self.Handler(
                    path='/v1/superagent/ssh/plan/prepare', owner='web:alice',
                    headers={**headers, 'Origin':'https://evil.invalid'},
                    body=ssh_plan)
                bad.cookie_value = 'session-a'
                bad.do_POST()
                self.assertEqual(bad.result[0], 403)
                plan_prepare.assert_not_called()
                ready = self.Handler(
                    path='/v1/superagent/ssh/plan/prepare', owner='web:alice',
                    headers=headers, body=ssh_plan)
                ready.cookie_value = 'session-a'
                ready.do_POST()
                self.assertEqual(ready.result[0], 200)
                self.assertEqual(ready.result[1]['approval']['steps'], 2)
                self.assertEqual(plan_prepare.call_args.args[1].owner_id, 'web:alice')

            plan_payload = {**ssh_plan, 'approval_token':'single-use', 'confirm':True}
            result_plan = {
                "job_id":"123e4567-e89b-12d3-a456-426614174000",
                "node_id":"192.168.100.22", "status":"queued",
                "total":2, "confirmed_steps":0,
                "remaining_not_started":2, "steps":[], "auto_retry":False,
            }
            with patch('leshy.web_server_impl14.consume_ssh_plan_approval') as consume_plan, patch(
                'leshy.web_server_impl14.start_ssh_plan_job',
                return_value=result_plan
            ) as execute_plan:
                missing_consent = self.Handler(
                    path='/v1/superagent/ssh/plan/execute', owner='web:alice',
                    headers=headers, body={**plan_payload, 'confirm':False})
                missing_consent.cookie_value = 'session-a'
                missing_consent.do_POST()
                self.assertEqual(missing_consent.result[0], 400)
                consume_plan.assert_not_called()

                other_site = self.Handler(
                    path='/v1/superagent/ssh/plan/execute', owner='web:alice',
                    headers={**headers, 'Origin':'https://evil.invalid'},
                    body=plan_payload)
                other_site.cookie_value = 'session-a'
                other_site.do_POST()
                self.assertEqual(other_site.result[0], 403)
                consume_plan.assert_not_called()

                accepted = self.Handler(
                    path='/v1/superagent/ssh/plan/execute', owner='web:alice',
                    headers=headers, body=plan_payload)
                accepted.cookie_value = 'session-a'
                accepted.do_POST()
                self.assertEqual(accepted.result[0], 202)
                self.assertEqual(accepted.result[1]['ssh_job']['status'], 'queued')
                consume_plan.assert_called_once()
                execute_plan.assert_called_once()
                self.assertEqual(execute_plan.call_args.args[1].owner_id, 'web:alice')
                self.assertTrue(callable(execute_plan.call_args.kwargs['may_continue']))

            # Status and cancellation must remain provider-bound even if
            # the remote-admin module is switched off during execution.
            with patch('leshy.web_server_impl14.list_ssh_plan_jobs',
                       return_value={'jobs':[result_plan]}) as status_list:
                query = self.Handler(
                    path='/v1/superagent/ssh/plan/jobs', owner='web:alice')
                query.cookie_value = 'session-a'
                query.do_GET()
                self.assertEqual(query.result[0], 200)
                self.assertEqual(query.result[1]['jobs'][0]['status'], 'queued')
                status_list.assert_called_once()
                status_list.reset_mock()
                outsider = self.Handler(
                    path='/v1/superagent/ssh/plan/jobs',
                    owner='web:bob')
                outsider.do_GET()
                self.assertEqual(outsider.result[0], 403)
                status_list.assert_not_called()

            with patch('leshy.web_server_impl14.cancel_ssh_plan_job',
                       return_value={**result_plan,'stop_requested':True}) as cancel_job:
                job_payload = {
                    'job_id':'123e4567-e89b-12d3-a456-426614174000',
                    'confirm': True,
                }
                wrong_site = self.Handler(
                    path='/v1/superagent/ssh/plan/jobs/cancel',
                    owner='web:alice',
                    headers={**headers,'Origin':'https://evil.invalid'},
                    body=job_payload)
                wrong_site.cookie_value = 'session-a'
                wrong_site.do_POST()
                self.assertEqual(wrong_site.result[0], 403)
                cancel_job.assert_not_called()
                no_confirm = self.Handler(
                    path='/v1/superagent/ssh/plan/jobs/cancel',
                    owner='web:alice', headers=headers,
                    body={**job_payload,'confirm':False})
                no_confirm.cookie_value = 'session-a'
                no_confirm.do_POST()
                self.assertEqual(no_confirm.result[0], 403)
                cancel_job.assert_not_called()
                stop = self.Handler(
                    path='/v1/superagent/ssh/plan/jobs/cancel',
                    owner='web:alice', headers=headers, body=job_payload)
                stop.cookie_value = 'session-a'
                stop.do_POST()
                self.assertEqual(stop.result[0], 200)
                self.assertTrue(stop.result[1]['ssh_job']['stop_requested'])
                cancel_job.assert_called_once()

            with patch('leshy.web_server_impl14.acknowledge_ssh_plan_job',
                       return_value={**result_plan,'status':'reviewed'}) as acknowledge:
                reviewed = self.Handler(
                    path='/v1/superagent/ssh/plan/jobs/review',
                    owner='web:alice', headers=headers,
                    body={'job_id':result_plan['job_id'],'confirm':True})
                reviewed.cookie_value = 'session-a'
                reviewed.do_POST()
                self.assertEqual(reviewed.result[0], 200)
                self.assertEqual(reviewed.result[1]['ssh_job']['status'], 'reviewed')
                acknowledge.assert_called_once()

            request = dict(node_id='192.168.100.22', grantee_id='web:bob',
                           capability='inspect')
            hostile = self.Handler(
                path='/v1/superagent/remote-access/revoke', owner='web:alice',
                headers={'Host': 'localhost', 'Origin': 'https://evil.example',
                         'X-Leshy-UI': 'superagent', 'Content-Type': 'application/json'},
                body=request)
            hostile.cookie_value = 'session-a'
            hostile.do_POST()
            self.assertEqual(hostile.result[0], 403)

            revoke = self.Handler(
                path='/v1/superagent/remote-access/revoke', owner='web:alice',
                headers={'Host': 'localhost', 'X-Leshy-UI': 'superagent',
                         'Content-Type': 'application/json'}, body=request)
            revoke.cookie_value = 'session-a'
            revoke.do_POST()
            self.assertEqual(revoke.result[0], 200)
            self.assertTrue(revoke.result[1]['revoked'])
            get2 = self.Handler(path='/v1/superagent/remote-access', owner='web:alice')
            get2.cookie_value = 'session-a'
            get2.do_GET()
            self.assertEqual(get2.result[1]['active_grants'], [])
        finally:
            self.app.storage = original_storage
            if original_identity is None:
                del self.app.owner_identity
            else:
                self.app.owner_identity = original_identity

    def test_native_rdp_route_requires_owner_and_exact_node(self):
        actor = SimpleNamespace(owner_id="web:alice")
        policy = object()
        profile = {
            "node_id": "192.168.5.12",
            "filename": "Leshy-RDP-192-168-5-12.rdp",
            "rdp": "full address:s:192.168.5.12:3389\r\n",
        }
        with patch.object(self.Handler, "_remote_context", return_value=(policy, actor)):
            with patch("leshy.web_server_impl14.rdp_connection_profile",
                       return_value=profile) as make:
                allowed = self.Handler(path="/v1/superagent/rdp/profile?node_id=192.168.5.12")
                allowed.do_GET()
                self.assertEqual(allowed.result[0], 200)
                self.assertEqual(allowed.result[1]["profile"]["filename"], profile["filename"])
                make.assert_called_once_with(policy, actor, "192.168.5.12")
                make.reset_mock()
                bad = self.Handler(path="/v1/superagent/rdp/profile?node_id=x&extra=y")
                bad.do_GET()
                self.assertEqual(bad.result[0], 400)
                make.assert_not_called()
        with patch.object(self.Handler, "_remote_context", return_value=None):
            with patch("leshy.web_server_impl14.rdp_connection_profile") as make:
                denied = self.Handler(path="/v1/superagent/rdp/profile?node_id=192.168.5.12")
                denied.do_GET()
                make.assert_not_called()

    def test_root_preserves_session_cookie_and_injects_panel(self):
        h = self.Handler(path='/', owner='web:alice')
        h.do_GET()
        self.assertEqual(h.result[0], 200)
        self.assertIn('superagent-dashboard.js', h.wfile.getvalue().decode('utf8'))
        self.assertIn('Set-Cookie', h.response_headers)


if __name__ == '__main__': unittest.main()
