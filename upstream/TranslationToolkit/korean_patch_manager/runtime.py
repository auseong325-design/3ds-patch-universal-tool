"""Resolve a bundled bridge and launch helpers without changing development use."""
import os
import subprocess
from pathlib import Path


def bridge_path(app_dir):
    app_dir = Path(app_dir).resolve()
    portable = app_dir.parent
    if (portable / 'portable.manifest.json').is_file():
        return portable / 'bridge' / 'KpmBridge.exe'
    return app_dir.parents[1] / 'RomForge-reference' / 'KpmBridge' / 'bin' / 'Release' / 'net10.0' / 'KpmBridge.dll'


def bridge_command(path):
    path = Path(path)
    return [str(path)] if path.suffix.lower() == '.exe' else ['dotnet', str(path)]


def hidden_process_flags():
    return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
