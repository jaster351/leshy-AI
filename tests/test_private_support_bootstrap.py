"""Private owner profile import: no plaintext facts or keys enter repository."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from cryptography.fernet import Fernet
from leshy.private_support_bootstrap import bootstrap_private_support, support_key
from leshy.provider_support import support_draft_context


def test_bundle(home: Path):
    key = Fernet.generate_key()
    value = {"facts": [{"cues": ["скорость"], "text": "Сверить актуальный договор."}]}
    token = Fernet(key).encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    archive = home / "Local-Support-Profile.zip"
    with ZipFile(archive, "w") as z:
        z.writestr("support_profile.key", key)
        z.writestr("support_profile.enc", token)
    return archive, key


class PrivateSupportBootstrapTests(unittest.TestCase):
    def test_import_from_local_bundle_without_install_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, key = test_bundle(root)
            brain = root / "runtime" / "LeshyBrain"
            # Linux fixture supplies the user's private key; real Windows import
            # uses CurrentUser DPAPI to persist an archive-provided key.
            with patch.dict(os.environ, {"LESHY_SUPPORT_PROFILE_KEY": key.decode()}):
                self.assertTrue(bootstrap_private_support(root, brain))
                self.assertTrue((brain / "private" / "support_profile.enc").is_file())
                self.assertIn(
                    "Сверить актуальный договор.",
                    support_draft_context("/support скорость", brain_dir=brain),
                )
            self.assertFalse((brain / "private" / "support_profile.key").exists())
            self.assertTrue(archive.exists())

    def test_no_key_does_not_publish_or_infer_private_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_bundle(root)
            with patch.dict(os.environ, {"LESHY_SUPPORT_PROFILE_KEY": ""}):
                if os.name != "nt":
                    self.assertFalse(bootstrap_private_support(root, root / "brain"))
                    response = support_draft_context(
                        "/support скорость", brain_dir=root / "brain"
                    )
                    self.assertNotIn("Сверить актуальный договор.", response)

    def test_invalid_bundle_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_bundle(root)
            with patch.dict(os.environ, {"LESHY_SUPPORT_PROFILE_KEY": Fernet.generate_key().decode()}):
                self.assertFalse(bootstrap_private_support(root, root / "brain"))


if __name__ == "__main__":
    unittest.main()
