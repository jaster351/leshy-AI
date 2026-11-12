from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from leshy.ssh_os_verifier import _mapping, credential_file_from_request, verify_ssh_os
from leshy.storage import Storage
from leshy.superagent_credential_settings import set_credential_folder


class SshVerifierTests(TestCase):
    def test_find_requested_filename(self):
        self.assertEqual(credential_file_from_request("Пароль хранится в файле server.txt"), "server.txt")
        self.assertIsNone(credential_file_from_request("Произвольный файл"))
        self.assertIsNone(credential_file_from_request("В файле a.txt и файле b.txt"))

    def test_credentials_parse(self):
        self.assertEqual(_mapping("IP=10.1.2.3\r\nlogin=admin\r\nпароль=P@ss", "10.1.2.3"), ("admin", "P@ss"))
        self.assertEqual(_mapping('{"host":"10.1.2.3","user":"svc","password":"secret"}', "10.1.2.3"), ("svc", "secret"))
        with self.assertRaises(ValueError):
            _mapping("login=admin\npassword=P@ss\npassword=other", "10.1.2.3")
        with self.assertRaises(ValueError):
            _mapping("server=10.1.2.4\nlogin=admin\npassword=P@ss", "10.1.2.3")

    def test_secret_not_in_result_and_host_key_is_strict(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            storage = Storage(home / "state.sqlite")
            folder = home / "accounts"
            folder.mkdir()
            (folder / "server.txt").write_text(
                "host=10.1.2.3\nlogin=admin\npassword=HIDDEN_SECRET", encoding="utf-8"
            )
            set_credential_folder(storage, "web:alice", str(folder))
            client = MagicMock()
            channel = MagicMock()
            channel.recv_exit_status.return_value = 0
            output = MagicMock()
            output.read.return_value = b"Linux\n"
            output.channel = channel
            client.exec_command.return_value = (MagicMock(), output, MagicMock())
            import sys
            fake = MagicMock()
            fake.SSHClient.return_value = client
            with patch.dict(sys.modules, {"paramiko": fake}):
                data = verify_ssh_os(storage, "web:alice", "10.1.2.3", "server.txt")
            self.assertEqual(data["os_family"], "Linux")
            self.assertTrue(data["verified"])
            self.assertNotIn("HIDDEN_SECRET", str(data))
            self.assertNotIn("admin", str(data))
            client.set_missing_host_key_policy.assert_called_once()
            self.assertEqual(client.exec_command.call_args.args[0], "uname -s")
            self.assertEqual(client.connect.call_args.kwargs["password"], "HIDDEN_SECRET")
            client.close.assert_called_once()
