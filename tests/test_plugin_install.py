import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'devtools/install_plugins.py'


class PluginInstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='plugin install ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.events = self.root / 'events.jsonl'

    def plugin(self, name, body=None):
        folder = self.root / 'plugins' / name
        folder.mkdir(parents=True)
        (folder / '__init__.py').write_text('raise RuntimeError("Do not import plugin during installation")')
        if body is not None:
            (folder / 'install.py').write_text(body)

    def run_hooks(self, plugins):
        (self.root / 'config.json').write_text(json.dumps({'plugins': plugins}))
        return subprocess.run([sys.executable, str(RUNNER), str(self.root)], capture_output=True, text=True)

    def recorder(self, name):
        return ("import json, pathlib, sys\n"
                f"with pathlib.Path({str(self.events)!r}).open('a') as stream:\n"
                f" stream.write(json.dumps([{name!r}, sys.executable, str(pathlib.Path.cwd())]) + '\\n')\n")

    def test_runs_only_enabled_hooks_in_order_without_importing_plugins(self):
        self.plugin('first', self.recorder('first'))
        self.plugin('second', self.recorder('second'))
        self.plugin('disabled', 'raise RuntimeError("disabled")')
        self.plugin('no_hook')
        result = self.run_hooks(['second', 'no_hook', 'first'])
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertEqual(events, [[name, sys.executable, str(self.root / 'plugins' / name)]
                                  for name in ('second', 'first')])

    def test_invalid_names_duplicates_and_missing_packages_fail_before_any_hook(self):
        self.plugin('good', self.recorder('good'))
        for names in (['good', '../bad'], ['good', 'good'], ['good', 'missing'], ['class'], 'good', [None]):
            with self.subTest(names=names):
                result = self.run_hooks(names)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.events.exists())

    def test_failure_names_plugin_and_stops_remaining_hooks(self):
        self.plugin('broken', 'raise SystemExit(23)')
        self.plugin('later', self.recorder('later'))
        result = self.run_hooks(['broken', 'later'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('broken', result.stderr)
        self.assertIn('23', result.stderr)
        self.assertFalse(self.events.exists())

    def test_missing_or_empty_plugins_need_no_hooks(self):
        result = self.run_hooks([])
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.root / 'config.json').write_text('{}')
        result = subprocess.run([sys.executable, str(RUNNER), str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_main_installer_stops_before_service_changes_when_hook_fails(self):
        self.plugin('broken', 'raise SystemExit(23)')
        (self.root / 'config.json').write_text(json.dumps({'plugins': ['broken']}))
        (self.root / 'fichaxebot').mkdir()
        (self.root / 'fichaxebot/bot.py').touch()
        (self.root / 'requirements.txt').touch()
        (self.root / 'devtools').mkdir()
        shutil.copyfile(RUNNER, self.root / 'devtools/install_plugins.py')
        executable = self.root / '.venv/bin/python3'
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/bash\n'
                              'if [[ "$1" == "-m" && "$2" == "pip" ]]; then exit 0; fi\n'
                              f'exec {shlex.quote(sys.executable)} "$@"\n')
        executable.chmod(0o755)
        fake_bin = self.root / 'bin'
        fake_bin.mkdir()
        sudo = fake_bin / 'sudo'
        sudo.write_text('#!/bin/bash\n' + f'touch {shlex.quote(str(self.events))}\nexit 99\n')
        sudo.chmod(0o755)
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), str(self.root)],
                                env={**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ['PATH']},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Plugin 'broken'", result.stderr)
        self.assertFalse(self.events.exists(), 'The installer must not change systemd after a failed hook')


class CongresoDependencyTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('congreso_install', ROOT / 'plugins/congreso_dieta/install.py')
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_repairs_existing_venv_and_verifies_uno_with_a_new_python_process(self):
        cfg = self.root / 'pyvenv.cfg'
        cfg.write_text('home = /usr/bin\ninclude-system-site-packages = false\nversion = 3.12\n')
        def probe(command, **kwargs):
            enabled = 'include-system-site-packages = true' in cfg.read_text()
            return subprocess.CompletedProcess(command, 0 if enabled else 1, '', "No module named 'uno'")
        with patch.object(self.installer.subprocess, 'run', side_effect=probe):
            self.installer.ensure_uno(str(self.root / 'bin/python'), self.root)
            self.installer.ensure_uno(str(self.root / 'bin/python'), self.root)
        self.assertEqual(cfg.read_text(), 'home = /usr/bin\ninclude-system-site-packages = true\nversion = 3.12\n')

    def test_unavailable_uno_after_repair_stops_with_clear_diagnostic(self):
        (self.root / 'pyvenv.cfg').write_text('include-system-site-packages = false\n')
        with patch.object(self.installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'incompatible Python')):
            with self.assertRaisesRegex(RuntimeError, 'UNO.*incompatible Python'):
                self.installer.ensure_uno('/test/python', self.root)

    def test_missing_packages_use_apt_and_existing_packages_are_skipped(self):
        installed = set()
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            if command[0] == 'dpkg-query':
                present = command[-1] in installed
                return subprocess.CompletedProcess(command, 0 if present else 1,
                                                   'install ok installed' if present else '', '')
            if command[:3] == ['sudo', 'apt-get', 'install']:
                installed.update(command[4:])
            return subprocess.CompletedProcess(command, 0)
        with patch.object(self.installer.subprocess, 'run', side_effect=run), \
             patch.object(self.installer.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), \
             patch.object(self.installer.os, 'geteuid', return_value=1000):
            self.installer.ensure_packages()
            self.installer.ensure_packages()
        installs = [cmd for cmd in commands if cmd[:3] == ['sudo', 'apt-get', 'install']]
        self.assertEqual(installs, [['sudo', 'apt-get', 'install', '-y', 'libreoffice-calc', 'python3-uno', 'poppler-utils']])

    def test_missing_autofirma_stops_with_installation_guidance(self):
        with patch.object(self.installer.shutil, 'which', side_effect=lambda name: None if name == 'autofirma' else name):
            with self.assertRaisesRegex(RuntimeError, 'Install AutoFirma separately'):
                self.installer.ensure_tools()
