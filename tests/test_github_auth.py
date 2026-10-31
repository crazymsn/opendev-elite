import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from worker.github_auth import configure_local_github


class GithubAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.addCleanup(self.env.stop)
        self.run=patch('worker.github_auth.subprocess.run');self.proc=self.run.start();self.addCleanup(self.run.stop)
        self.which=patch('worker.github_auth.shutil.which',return_value='C:/synthetic/gh.exe');self.which.start();self.addCleanup(self.which.stop)

    def opt_in(self):
        (self.root/'github.local.json').write_text(json.dumps({'credential_source':'github-cli'}))

    def test_anonymous_is_default_and_does_not_search_or_invoke_cli(self):
        self.assertEqual(configure_local_github(self.root),{'source':'anonymous','configured':False})
        self.proc.assert_not_called()

    def test_environment_has_priority_and_is_not_in_result(self):
        self.opt_in();os.environ['GITHUB_TOKEN']='synthetic-environment-token'
        result=configure_local_github(self.root)
        self.assertEqual(result['source'],'environment');self.assertNotIn('synthetic',json.dumps(result));self.proc.assert_not_called()

    def test_secret_file_is_bounded_and_never_written_by_setup(self):
        path=self.root/'private-token';path.write_bytes(b'synthetic-file-token\n');before=path.read_bytes()
        os.environ['GITHUB_TOKEN_FILE']=str(path);result=configure_local_github(self.root)
        self.assertEqual(result,{'source':'secret_file','configured':True});self.assertEqual(path.read_bytes(),before)
        self.assertEqual(os.environ['GITHUB_TOKEN'],'synthetic-file-token');self.proc.assert_not_called()

    def test_cli_explicit_opt_in_has_fixed_args_no_shell_and_no_secret_persistence(self):
        self.opt_in();before={p.name:p.read_bytes() for p in self.root.iterdir()}
        self.proc.return_value=subprocess.CompletedProcess([],0,b'synthetic-cli-token\n',b'private diagnostic')
        result=configure_local_github(self.root)
        self.assertEqual(result,{'source':'github-cli','configured':True});self.assertEqual(os.environ['GITHUB_TOKEN'],'synthetic-cli-token')
        args,kwargs=self.proc.call_args
        self.assertEqual(args[0],['C:/synthetic/gh.exe','auth','token','--hostname','github.com'])
        self.assertFalse(kwargs.get('shell',False));self.assertEqual(kwargs['stdin'],subprocess.DEVNULL);self.assertEqual(kwargs['timeout'],15)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        self.assertNotIn('synthetic-cli-token',json.dumps(result));self.assertNotIn('private diagnostic',json.dumps(result))

    def test_cli_failures_never_leak_output_or_fall_back_to_anonymous(self):
        self.opt_in()
        for failure in (subprocess.CompletedProcess([],1,b'secret-stdout',b'secret-stderr'),subprocess.TimeoutExpired('gh',15,output=b'secret-output')):
            if isinstance(failure,Exception):self.proc.side_effect=failure
            else:self.proc.side_effect=None;self.proc.return_value=failure
            with self.assertRaises(ValueError) as caught:configure_local_github(self.root)
            self.assertNotIn('secret',str(caught.exception));self.assertNotIn('GITHUB_TOKEN',os.environ)
        with patch('worker.github_auth.shutil.which',return_value=None),self.assertRaisesRegex(ValueError,'unavailable'):
            configure_local_github(self.root)

    def test_malformed_and_oversized_sources_fail_without_echoing_values(self):
        for config in ({'credential_source':'arbitrary-shell','token':'secret-data'},[],{'credential_source':'github-cli','args':['malicious']}):
            (self.root/'github.local.json').write_text(json.dumps(config))
            with self.assertRaises(ValueError) as caught:configure_local_github(self.root)
            self.assertNotIn('secret-data',str(caught.exception));self.proc.assert_not_called()
        self.opt_in()
        for raw in (b'',b'secret with spaces',b'\xff',b'x'*16385):
            self.proc.return_value=subprocess.CompletedProcess([],0,raw,b'')
            with self.assertRaises(ValueError):configure_local_github(self.root)
            self.assertNotIn('GITHUB_TOKEN',os.environ)


if __name__=='__main__':unittest.main()
