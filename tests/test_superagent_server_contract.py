"""Keep dashboard paths owner-scoped and avoid adding implicit chat commands."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / 'src' / 'leshy'


class ContractTests(unittest.TestCase):
    def test_dashboard_has_server_cookie_authentication(self):
        text = (ROOT / 'web_server_impl14.py').read_text(encoding='utf-8')
        self.assertIn('_existing_web_owner', text)
        self.assertIn('if not owner or owner == "web:local":', text)
        self.assertIn('X-Leshy-UI', text)
        self.assertIn('module_enabled(app.storage, "superagent")', text)
        self.assertNotIn('body.get("owner_id")', text)
        self.assertNotIn('query.get("owner_id")', text)

    def test_web_server_installs_outer_handler(self):
        wrapper = (ROOT / 'web_server_impl14.py').read_text(encoding='utf-8')
        entry = (ROOT / 'web_server.py').read_text(encoding='utf-8')
        self.assertIn('from . import web_server_impl13 as previous', wrapper)
        self.assertIn('from .web_server_impl14 import main, make_handler, serve', entry)

    def test_dashboard_only_injects_script_into_existing_runtime_ui(self):
        text = (ROOT.parent.parent / 'web' / 'superagent-dashboard.js').read_text(encoding='utf-8')
        wrapper = (ROOT / 'web_server_impl14.py').read_text(encoding='utf-8')
        self.assertIn('pane-tasks', text)
        self.assertIn('Что делают кнопки и команды?', text)
        self.assertIn('/superagent-dashboard.js', wrapper)

if __name__ == '__main__': unittest.main()
