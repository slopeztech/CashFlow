import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from shutil import which

from django.conf import settings


UPDATE_SERVICE_NAME = 'cashflow.service'


def _resolve_executable(binary_name, *, extra_paths=None):
    resolved = which(binary_name)
    if resolved:
        return resolved

    candidates = extra_paths or []
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def get_git_executable():
    return _resolve_executable(
        'git',
        extra_paths=[
            '/usr/bin/git',
            '/bin/git',
            '/usr/local/bin/git',
        ],
    )


def get_systemctl_executable():
    return _resolve_executable(
        'systemctl',
        extra_paths=[
            '/usr/bin/systemctl',
            '/bin/systemctl',
            '/usr/local/bin/systemctl',
        ],
    )


def get_update_log_path() -> Path:
    base_dir = Path(str(getattr(settings, 'BASE_DIR', os.getcwd())))
    return base_dir / 'last_update.log'


def _append_log(log_file, message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_file.write(f"[{timestamp}] {message}\n")
    log_file.flush()


def _run_step(log_file, *, step_name, command, cwd, timeout=900):
    _append_log(log_file, f"STEP: {step_name}")
    _append_log(log_file, f"CMD: {' '.join(command)}")

    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _append_log(log_file, f"ERROR: {step_name} failed to execute: {exc}")
        return False

    if result.stdout:
        _append_log(log_file, 'STDOUT:')
        for line in result.stdout.splitlines():
            _append_log(log_file, line)

    if result.stderr:
        _append_log(log_file, 'STDERR:')
        for line in result.stderr.splitlines():
            _append_log(log_file, line)

    if result.returncode != 0:
        _append_log(log_file, f"ERROR: {step_name} exited with code {result.returncode}")
        return False

    _append_log(log_file, f"OK: {step_name}")
    return True


def run_platform_update(*, initiated_by='manual'):
    base_dir = Path(str(getattr(settings, 'BASE_DIR', os.getcwd())))
    log_path = get_update_log_path()
    os_name = platform.system().lower()
    python_cmd = [sys.executable, 'manage.py']

    with open(log_path, 'w', encoding='utf-8') as log_file:
        _append_log(log_file, 'CashFlow update started')
        _append_log(log_file, f'Initiated by: {initiated_by}')
        _append_log(log_file, f'Base directory: {base_dir}')
        _append_log(log_file, f'Detected OS: {os_name}')

        git_cmd = get_git_executable()
        if not git_cmd:
            _append_log(log_file, 'ERROR: Git executable not found. Ensure git is installed and available to the service user.')
            _append_log(log_file, 'CashFlow update finished with errors')
            return {
                'success': False,
                'log_path': str(log_path),
            }

        steps = [
            ('Fetch remote changes', [git_cmd, 'fetch', '--all', '--prune']),
            ('Pull latest changes', [git_cmd, 'pull', '--ff-only']),
            ('Install dependencies', [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt']),
            ('Apply database migrations', python_cmd + ['migrate', '--noinput']),
            ('Collect static files', python_cmd + ['collectstatic', '--noinput']),
            ('Run Django checks', python_cmd + ['check']),
        ]

        for step_name, command in steps:
            if not _run_step(log_file, step_name=step_name, command=command, cwd=base_dir):
                _append_log(log_file, 'CashFlow update finished with errors')
                return {
                    'success': False,
                    'log_path': str(log_path),
                }

        if os_name == 'linux':
            systemctl_cmd = get_systemctl_executable()
            if not systemctl_cmd:
                _append_log(log_file, 'ERROR: systemctl executable not found on Linux environment.')
                _append_log(log_file, 'CashFlow update finished with errors')
                return {
                    'success': False,
                    'log_path': str(log_path),
                }

            linux_steps = [
                ('Reload systemd units', [systemctl_cmd, 'daemon-reload']),
                ('Restart CashFlow service', [systemctl_cmd, 'restart', UPDATE_SERVICE_NAME]),
                ('Verify CashFlow service status', [systemctl_cmd, 'is-active', UPDATE_SERVICE_NAME]),
            ]
            for step_name, command in linux_steps:
                if not _run_step(log_file, step_name=step_name, command=command, cwd=base_dir):
                    _append_log(log_file, 'CashFlow update finished with errors')
                    return {
                        'success': False,
                        'log_path': str(log_path),
                    }
        else:
            _append_log(log_file, 'Skipping systemctl steps because OS is not Linux')

        _append_log(log_file, 'CashFlow update finished successfully')

    return {
        'success': True,
        'log_path': str(log_path),
    }
