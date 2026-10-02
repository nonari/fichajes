"""Exercise the service migration with administrative commands isolated from the host."""
import json
import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ServiceUserTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bot = self.root / 'bot'
        (self.bot / 'fichaxebot').mkdir(parents=True)
        (self.bot / 'fichaxebot/bot.py').touch()
        (self.bot / '.venv/bin').mkdir(parents=True)
        (self.bot / '.venv/bin/python3').touch()
        (self.bot / 'config.json').write_text('{}')
        (self.bot / '.schedule.data').write_text('keep state')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.events = self.root / 'events'
        self.unit = self.root / 'override.conf'
        # These are the administrative boundary; no real systemd or ownership changes run.
        stub = '''#!/usr/bin/python3
import json,os,pathlib,shutil,sys
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['TEST_EVENTS'],'a') as f: f.write(json.dumps([name,*args])+'\\n')
if name=='id':
    if args==['-u']: print(0)
    else: os.execv('/usr/bin/id',['id',*args])
elif name=='install' and '-d' not in args: shutil.copyfile(args[-2],os.environ['TEST_UNIT'])
elif name=='systemctl' and args[0]=='stop' and os.environ.get('FAIL_STOP'): sys.exit(1)
'''
        for name in ('id', 'systemctl', 'find', 'runuser', 'install'):
            script = self.bin / name
            script.write_text(stub)
            script.chmod(0o755)
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.env = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH'],
                    'TEST_EVENTS': str(self.events), 'TEST_UNIT': str(self.unit)}

    def run_migration(self, user=None, **env):
        return subprocess.run(['bash', str(ROOT / 'devtools/configure_service_user.sh'),
                               str(self.bot), user or self.user],
                              env={**self.env, **env}, capture_output=True, text=True)

    def test_migration_stops_before_repair_and_starts_as_selected_user(self):
        result = self.run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in self.events.read_text().splitlines()]
        stop = events.index(['systemctl', 'stop', 'fichaxe.service'])
        repairs = [i for i, event in enumerate(events) if event[0] == 'find']
        start = events.index(['systemctl', 'start', 'fichaxe.service'])
        self.assertTrue(repairs and all(stop < i < start for i in repairs))
        self.assertIn('User=' + self.user, self.unit.read_text())
        self.assertIn('Group=' + str(pwd.getpwnam(self.user).pw_gid), self.unit.read_text())
        self.assertEqual((self.bot / '.schedule.data').read_text(), 'keep state')

    def test_root_is_rejected_without_touching_service(self):
        result = self.run_migration('root')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('systemctl', self.events.read_text())

    def test_stop_failure_does_not_change_ownership_or_service_identity(self):
        result = self.run_migration(FAIL_STOP='1')
        self.assertNotEqual(result.returncode, 0)
        events = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertFalse(any(event[0] in ('find', 'install') for event in events))
        self.assertFalse(self.unit.exists())
