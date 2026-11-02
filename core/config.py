"""
Configuration management for the Portable Proxy.

Handles loading, saving, and manipulating proxy configurations
stored in ``%LOCALAPPDATA%/ProxyStation/config/proxies.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any

# ---------------------------------------------------------------------------
# Paths  —  все данные пользователя хранятся в %LOCALAPPDATA%/ProxyStation/
#           (portable-папка приложения остаётся чистой, без настроек)
# ---------------------------------------------------------------------------

_APP_NAME = 'ProxyStation'

def _get_data_root() -> str:
    """Возвращает корневую директорию для пользовательских данных.

    Windows: %LOCALAPPDATA%/ProxyStation
    Linux/macOS: ~/.local/share/ProxyStation
    """
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
    else:
        base = os.environ.get('XDG_DATA_HOME', os.path.join(os.path.expanduser('~'), '.local', 'share'))
    return os.path.join(base, _APP_NAME)


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # portable app folder (read-only)
DATA_DIR = _get_data_root()                                             # пользовательские данные

CONFIG_DIR = os.path.join(DATA_DIR, 'config')
CACHE_DIR = os.path.join(DATA_DIR, 'cache')
LOG_DIR = os.path.join(DATA_DIR, 'logs')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'proxies.json')
LOG_FILE = os.path.join(LOG_DIR, 'proxystation.log')

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ProxyEntry:
    """A single proxy endpoint configuration."""

    id: str
    name: str
    listen_host: str = '0.0.0.0'
    listen_port: int = 9000
    upstream_url: str = 'http://127.0.0.1:8080'
    upstream_api_key: str = ''
    translate_enabled: bool = True
    translate_to: str = 'Russian'
    model_aliases: dict = field(default_factory=dict)
    auto_start: bool = True
    protocols: list = field(default_factory=lambda: ['openai'])


@dataclass
class TranslationSettings:
    """Global translation LLM settings."""

    llm_endpoint: str = 'http://127.0.0.1:8045/v1/chat/completions'
    llm_api_key: str = ''
    primary_model: str = 'gemini-3.5-flash-lite'
    backup_model: str = 'gemini-3.8-flash'
    smooth_drip: bool = True   # плавная пословная выдача перевода (а-ля генерация)
    auto_prompt_cache: bool = True  # автоматическая расстановка точек кэширования (85-95% hit rate)


@dataclass
class AppConfig:
    """Top-level application configuration."""

    translation: TranslationSettings = field(default_factory=TranslationSettings)
    proxies: list[ProxyEntry] = field(default_factory=list)
    on_close_action: str = 'ask'  # 'ask' | 'tray' | 'exit'


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _proxy_from_dict(d: dict[str, Any]) -> ProxyEntry:
    """Create a ProxyEntry from a raw dict, ignoring unknown keys."""
    known = {f.name for f in ProxyEntry.__dataclass_fields__.values()}
    filtered = {k: v for k, v in d.items() if k in known}
    return ProxyEntry(**filtered)


def _translation_from_dict(d: dict[str, Any]) -> TranslationSettings:
    known = {f.name for f in TranslationSettings.__dataclass_fields__.values()}
    filtered = {k: v for k, v in d.items() if k in known}
    return TranslationSettings(**filtered)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config() -> AppConfig:
    """Load configuration from disk.  Returns defaults if file is missing.

    On first run after the portable → AppData migration, automatically
    copies old config/proxies.json and cache/* from the app folder.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    # ── Auto-migrate from old portable location ──
    _old_config = os.path.join(ROOT_DIR, 'config', 'proxies.json')
    if not os.path.isfile(CONFIG_FILE) and os.path.isfile(_old_config):
        import shutil
        shutil.copy2(_old_config, CONFIG_FILE)
        # Migrate cache files too
        _old_cache = os.path.join(ROOT_DIR, 'cache')
        if os.path.isdir(_old_cache):
            for fn in os.listdir(_old_cache):
                src = os.path.join(_old_cache, fn)
                dst = os.path.join(CACHE_DIR, fn)
                if os.path.isfile(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)

    if not os.path.isfile(CONFIG_FILE):
        return AppConfig()

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as fh:
            raw: dict[str, Any] = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return AppConfig()

    translation = _translation_from_dict(raw.get('translation', {}))
    proxies = [_proxy_from_dict(p) for p in raw.get('proxies', [])]
    on_close_action = str(raw.get('on_close_action', 'ask'))

    return AppConfig(translation=translation, proxies=proxies, on_close_action=on_close_action)


def save_config(config: AppConfig) -> None:
    """Persist configuration to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)

    data: dict[str, Any] = {
        'translation': asdict(config.translation),
        'proxies': [asdict(p) for p in config.proxies],
        'on_close_action': getattr(config, 'on_close_action', 'ask'),
    }

    tmp = CONFIG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    os.replace(tmp, CONFIG_FILE)


def get_proxy_by_id(config: AppConfig, proxy_id: str) -> ProxyEntry | None:
    """Find a proxy entry by its id.  Returns ``None`` if not found."""
    for p in config.proxies:
        if p.id == proxy_id:
            return p
    return None
