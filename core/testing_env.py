import os
from pathlib import Path


_ENV_CACHE = None


def _load_env_file():
    env_data = {}
    env_path = Path(__file__).resolve().parents[1] / '.env'
    if not env_path.exists():
        return env_data

    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        env_data[key.strip()] = value.strip().strip('"').strip("'")
    return env_data


def get_env(name, default):
    global _ENV_CACHE

    if name in os.environ:
        return os.environ[name]

    if _ENV_CACHE is None:
        _ENV_CACHE = _load_env_file()

    return _ENV_CACHE.get(name, default)


def get_int_env(name, default):
    try:
        return int(get_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_float_env(name, default):
    try:
        return float(get_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_bounded_int_env(name, default, minimum=None, maximum=None):
    value = get_int_env(name, default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value
