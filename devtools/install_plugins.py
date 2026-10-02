"""Run optional dependency hooks for configured plugins, without importing them."""
import json
import keyword
from pathlib import Path
import subprocess
import sys


def install_plugins(root: Path) -> None:
    root = root.resolve()
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('config.json must contain an object')
    names = config.get('plugins', [])
    if not isinstance(names, list) or any(
            not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) for name in names):
        raise ValueError('plugins must be a list of Python package names without paths or dots')
    if len(names) != len(set(names)):
        raise ValueError('plugins contains duplicate names')
    hooks = []
    for name in names:
        folder = root / 'plugins' / name
        if not (folder / '__init__.py').is_file():
            raise ValueError(f'Plugin {name!r}: package not found in {folder}')
        hook = folder / 'install.py'
        if hook.is_file():
            hooks.append((name, hook))
    for name, hook in hooks:
        print(f'Installing dependencies for plugin {name}', flush=True)
        result = subprocess.run([sys.executable, str(hook)], cwd=hook.parent)
        if result.returncode:
            raise RuntimeError(f'Plugin {name!r}: dependency installation failed (exit {result.returncode})')


if __name__ == '__main__':
    try:
        install_plugins(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Plugin installation stopped: {exc}', file=sys.stderr)
        sys.exit(1)
