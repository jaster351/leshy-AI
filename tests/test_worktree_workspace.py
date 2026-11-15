import subprocess
import tempfile
import unittest
from pathlib import Path
from leshy.worktree_workspace import WorktreeWorkspace, WorkspaceError


def git(*args):
    return subprocess.run(('git', *map(str, args)),check=True,capture_output=True,text=True).stdout.strip()


class WorktreeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name)/'repo'
        self.repo.mkdir()
        git('init',self.repo)
        git('-C',self.repo,'config','user.email','test@example.com')
        git('-C',self.repo,'config','user.name','Tester')
        (self.repo/'app.py').write_text('print("original")\n')
        git('-C',self.repo,'add','app.py')
        git('-C',self.repo,'commit','-m','base')
        self.workspace=WorktreeWorkspace(self.repo)

    def test_two_isolated_worktrees_do_not_mutate_main(self):
        a=self.workspace.create('agent1')
        b=self.workspace.create('agent2')
        self.assertEqual(a['base_commit'],b['base_commit'])
        (Path(a['path'])/'app.py').write_text('print("changed")\n')
        self.assertTrue(self.workspace.status('agent1')['dirty'])
        self.assertFalse(self.workspace.status('agent2')['dirty'])
        self.assertEqual((self.repo/'app.py').read_text(),'print("original")\n')
        with self.assertRaises(WorkspaceError):self.workspace.remove_clean('agent1')
        self.workspace.remove_clean('agent2')
        self.assertFalse(Path(b['path']).exists())

    def test_path_traversal_and_unknown_ref_rejected(self):
        for name in ['../escape','/abs','x/y','.','..','']:
            with self.assertRaises(WorkspaceError):self.workspace.create(name)
        with self.assertRaises(WorkspaceError):self.workspace.create('safe',base_ref='--upload-pack=evil')
        self.assertFalse(self.workspace.root.exists())

    def test_untracked_files_survive_remove_attempt(self):
        x=self.workspace.create('agent-x')
        (Path(x['path'])/'private-data.txt').write_text('keep me')
        with self.assertRaises(WorkspaceError):self.workspace.remove_clean('agent-x')
        self.assertTrue((Path(x['path'])/'private-data.txt').exists())

    def test_symlink_replacement_rejected(self):
        outside=Path(self.tmp.name)/'outside'
        outside.mkdir()
        self.workspace.root.mkdir()
        try:
            (self.workspace.root/'agent').symlink_to(outside,target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest('No symlink permission on this platform')
        with self.assertRaises(WorkspaceError):self.workspace.create('agent')

if __name__=='__main__':unittest.main()
