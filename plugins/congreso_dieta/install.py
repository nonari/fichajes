"""Host dependency setup. Run with the bot's Python; safe to rerun on an existing venv."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PACKAGES = ('libreoffice-calc', 'python3-uno', 'poppler-utils')


def ensure_packages() -> None:
    if not shutil.which('dpkg-query') or not shutil.which('apt-get'):
        raise RuntimeError('congreso_dieta installation requires a Debian/Ubuntu host with apt-get')
    missing = []
    for package in PACKAGES:
        result = subprocess.run(['dpkg-query', '-W', '-f=${Status}', package], capture_output=True, text=True)
        if result.returncode or not result.stdout.strip().endswith('ok installed'):
            missing.append(package)
    if missing:
        prefix = [] if os.geteuid() == 0 else ['sudo']
        print(f'Installing system packages: {", ".join(missing)}', flush=True)
        subprocess.run(prefix + ['apt-get', 'update'], check=True)
        subprocess.run(prefix + ['apt-get', 'install', '-y', *missing], check=True)


def ensure_uno(python: str, prefix: Path) -> None:
    command = [python, '-c', 'import uno; from com.sun.star.beans import PropertyValue; uno.getComponentContext()']
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return
    cfg = prefix / 'pyvenv.cfg'
    if cfg.is_file():
        text = cfg.read_text(encoding='utf-8')
        pattern = r'^include-system-site-packages\s*=.*$'
        if re.search(pattern, text, flags=re.MULTILINE):
            text = re.sub(pattern, 'include-system-site-packages = true', text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + '\ninclude-system-site-packages = true\n'
        cfg.write_text(text, encoding='utf-8')
        print(f'Enabled system packages in {cfg}', flush=True)
        # The current interpreter has already initialized sys.path; verify in a fresh process.
        result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'UNO is unavailable in {python}: {result.stderr.strip()}\n'
                           'Use a virtualenv based on /usr/bin/python3, compatible with python3-uno.')


def ensure_tools() -> None:
    missing = [name for name in ('soffice', 'pdfunite', 'autofirma') if not shutil.which(name)]
    if missing:
        raise RuntimeError(f'Missing commands: {", ".join(missing)}. '
                           'Install AutoFirma separately if autofirma is missing, then rerun installation.')


def main() -> None:
    ensure_packages()
    ensure_uno(sys.executable, Path(sys.prefix))
    ensure_tools()
    print('congreso_dieta dependencies verified (LibreOffice/UNO, pdfunite, AutoFirma)', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'congreso_dieta dependency error: {exc}', file=sys.stderr)
        sys.exit(1)
