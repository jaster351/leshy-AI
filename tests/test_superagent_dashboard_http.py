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

    def test_root_preserves_session_cookie_and_injects_panel(self):
        h = self.Handler(path='/', owner='web:alice')
        h.do_GET()
        self.assertEqual(h.result[0], 200)
        self.assertIn('superagent-dashboard.js', h.wfile.getvalue().decode('utf8'))
        self.assertIn('Set-Cookie', h.response_headers)


if __name__ == '__main__': unittest.main()
