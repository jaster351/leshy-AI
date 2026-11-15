import subprocess
import tempfile
import unittest
from pathlib import Path
from leshy.code_change_review import review_python_file,review_changed_python,ReviewError


def git(*args):
    return subprocess.run(('git',*map(str,args)),check=True,text=True,capture_output=True).stdout


class ReviewTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo=Path(tmp.name)/'repo'
        self.repo.mkdir()
        git('init',self.repo)
        git('-C',self.repo,'config','user.email','test@example.com')
        git('-C',self.repo,'config','user.name','Tester')
        (self.repo/'ok.py').write_text('def f():\n    return 3\n')
        git('-C',self.repo,'add','ok.py')
        git('-C',self.repo,'commit','-m','base')

    def test_syntax_error_detected_without_execution(self):
        (self.repo/'bad.py').write_text('def broken(:\n    ...\n')
        report=review_changed_python(self.repo)
        self.assertFalse(report['syntax_ok'])
        self.assertTrue(any(f['rule']=='syntax' for f in report['findings']))

    def test_risky_calls_are_annotated_not_executed(self):
        (self.repo/'flag.py').write_text('import os\nimport subprocess\nimport pickle\n'
             'def f(x):\n    os.system(x)\n    subprocess.run(x,shell=True)\n    pickle.loads(x)\n    eval(x)\n')
        report=review_changed_python(self.repo)
        self.assertTrue(report['review_passed'])
        self.assertEqual({f['rule'] for f in report['findings']},
                         {'shell-command','shell-true','unsafe-deserialization','dynamic-code'})

    def test_unmodified_python_not_scanned(self):
        report=review_changed_python(self.repo)
        self.assertEqual(report['files_checked'],[])

    def test_path_traversal_refused(self):
        with self.assertRaises(ReviewError):review_python_file(self.repo,'../outside.py')

    def test_symlink_refused(self):
        original=self.repo/'ok.py'
        link=self.repo/'link.py'
        try:link.symlink_to(original)
        except OSError:self.skipTest('Symlinks unavailable')
        with self.assertRaises(ReviewError):review_python_file(self.repo,'link.py')

    def test_modified_tracked_file_checked(self):
        (self.repo/'ok.py').write_text('def f():\n    return eval("1")\n')
        report=review_changed_python(self.repo)
        self.assertEqual(report['files_checked'],['ok.py'])
        self.assertTrue(any(f['rule']=='dynamic-code' for f in report['findings']))

if __name__=='__main__':unittest.main()
