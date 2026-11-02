"""
shadow_cache.py — Теневой кэш сессий и сообщений для прокси-переводчика.

Хранит пары (RU-оригинал, EN-перевод) для каждого хода диалога.
При повторном запросе подставляет ранее переведённый EN-текст байт-в-байт,
активируя аппаратный prompt-cache модели (KV-cache hit).

Два уровня кэширования:
  1. Session prefix cache  — точное совпадение префикса диалога по session_id.
  2. MessageShadowCache    — поэлементный fallback по нормализованному содержимому.

Персистенция: JSONL-файлы в ../cache/ относительно этого модуля.
Потокобезопасность: threading.RLock на каждом Store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# ─── Константы ──────────────────────────────────────────────────────────────────────────────

from core.config import CACHE_DIR as _CACHE_DIR_STR

_CACHE_DIR = Path(_CACHE_DIR_STR)

DEFAULT_MAX_SESSIONS:    int   = 100
DEFAULT_SESSION_TTL:     float = 72 * 3600          # 72 часа
DEFAULT_MAX_MSG_ENTRIES: int   = 50_000
DEFAULT_MAX_CANDIDATES:  int   = 5
DEFAULT_COMPACT_EVERY:   int   = 2_000

# Оценки символов/токен для подсчёта экономии
_RU_CHARS_PER_TOKEN: float = 1.8
_EN_CHARS_PER_TOKEN: float = 4.0

# Паттерн для <system-reminder>…</system-reminder> и окружающих пробелов
_SYSREM_RE = re.compile(
    r"\s*<system-reminder>.*?</system-reminder>\s*",
    re.DOTALL | re.IGNORECASE,
)

log = logging.getLogger("shadow_cache")



# ─── Утилиты ───────────────────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Нормализация текста для сравнения: убираем system-reminder,
    лишние пробелы, приводим к lowercase.  Динамические теги не ломают кэш."""
    text = _SYSREM_RE.sub("", text)
    text = " ".join(text.split())
    return text.strip().lower()


def _text_hash(text: str) -> str:
    """Короткий SHA-256-hex от нормализованной строки (первые 16 символов)."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


def content_to_text(content: Any) -> str:
    """Извлекает текстовую часть из content сообщения.

    Обрабатывает:
      - str  — Google-стиль, просто строка.
      - list — Google/Anthropic-стиль, список блоков-словарей.
        Пропускаем блоки ``thinking`` — они не влияют на матчинг.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type", "")
            if btype == "thinking":
                continue
            if btype == "text":
                parts.append(block.get("text", ""))
            elif "text" in block and btype == "":
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def merge_restored_content(original_content: Any, en_text: str) -> Any:
    """Подставляет EN-текст обратно в структуру content,
    НЕ трогая tool_use / tool_result / image / thinking блоки.

    Если content — строка, просто возвращаем en_text.
    Если list — заменяем только text-блоки (по порядку, разделяя en_text
    по '\n' между ними).  Остальные блоки остаются нетронутыми.
    """
    if isinstance(original_content, str):
        return en_text

    if not isinstance(original_content, list):
        return en_text

    # Собираем индексы текстовых блоков (не-thinking)
    text_indices: list[int] = []
    for i, block in enumerate(original_content):
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text" or (btype == "" and "text" in block):
            text_indices.append(i)

    if not text_indices:
        return original_content

    # Разбиваем en_text по переводам строки на столько частей,
    # сколько текстовых блоков (последний получает остаток)
    parts = en_text.split("\n", maxsplit=len(text_indices) - 1)

    result = []
    part_idx = 0
    for i, block in enumerate(original_content):
        if i in text_indices and part_idx < len(parts):
            new_block = dict(block)
            new_block["text"] = parts[part_idx]
            result.append(new_block)
            part_idx += 1
        else:
            result.append(block)
    return result



# ─── Модели данных ────────────────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    """Один ход диалога в теневой сессии."""
    role: str                  # "user" | "assistant" | "system"
    ru: str                    # оригинал (RU), показанный пользователю
    en: str                    # перевод (EN), отправленный модели
    kind: str = "text"         # "text" | "tool_use" | "tool_result" | ...
    ts: float = 0.0            # UNIX-время записи

    def __post_init__(self) -> None:
        if self.ts == 0.0:
            self.ts = time.time()


@dataclass
class Session:
    """Теневая сессия: упорядоченная последовательность ходов."""
    session_id: str
    turns: list[Turn] = field(default_factory=list)
    created: float = 0.0
    last_access: float = 0.0

    def __post_init__(self) -> None:
        now = time.time()
        if self.created == 0.0:
            self.created = now
        if self.last_access == 0.0:
            self.last_access = now

    def touch(self) -> None:
        self.last_access = time.time()

    def prefix_matches(self, turns: list[Turn]) -> int:
        """Сколько ходов из ``turns`` совпадают с началом сессии
        (по нормализованному RU)."""
        matched = 0
        for i, incoming in enumerate(turns):
            if i >= len(self.turns):
                break
            if (incoming.role == self.turns[i].role
                    and normalize(incoming.ru) == normalize(self.turns[i].ru)):
                matched += 1
            else:
                break
        return matched



# ─── Message-level fallback cache ─────────────────────────────────────────────

class MessageShadowCache:
    """Кэш на уровне отдельных сообщений.

    Ключ — нормализованный хэш (role + content_ru).
    Значение — список кандидатов EN-перевода (до max_candidates).
    Позволяет находить перевод даже без полного совпадения сессии.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_MSG_ENTRIES,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ) -> None:
        self._store: dict[str, list[str]] = {}
        self._access: dict[str, float] = {}
        self.max_entries = max_entries
        self.max_candidates = max_candidates
        self.hits: int = 0
        self.misses: int = 0

    def put(self, role: str, ru_text: str, en_text: str) -> None:
        """Записать пару RU->EN."""
        key = self._key(role, ru_text)
        candidates = self._store.get(key, [])
        if en_text not in candidates:
            candidates.append(en_text)
            if len(candidates) > self.max_candidates:
                candidates = candidates[-self.max_candidates:]
        self._store[key] = candidates
        self._access[key] = time.time()
        self._maybe_evict()

    def get(self, role: str, ru_text: str) -> str | None:
        """Найти EN-перевод по RU-тексту.  Возвращает последний (свежий) кандидат."""
        key = self._key(role, ru_text)
        candidates = self._store.get(key)
        if candidates:
            self._access[key] = time.time()
            self.hits += 1
            return candidates[-1]
        self.misses += 1
        return None

    def size(self) -> int:
        return len(self._store)

    def stats(self) -> dict[str, Any]:
        return {
            "entries": self.size(),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (
                round(self.hits / (self.hits + self.misses), 4)
                if (self.hits + self.misses) > 0 else 0.0
            ),
        }

    @staticmethod
    def _key(role: str, ru_text: str) -> str:
        norm = normalize(f"{role}:{ru_text}")
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:20]

    def _maybe_evict(self) -> None:
        """Вытесняем самые старые записи, если превышен лимит."""
        if len(self._store) <= self.max_entries:
            return
        target = self.max_entries * 3 // 4
        by_time = sorted(self._access.items(), key=lambda kv: kv[1])
        to_remove = len(self._store) - target
        for key, _ in by_time[:to_remove]:
            self._store.pop(key, None)
            self._access.pop(key, None)



# ─── JSONL persistence helpers ──────────────────────────────────────────────────

def _jsonl_path(name: str | None) -> Path:
    """Путь к JSONL-файлу.  None/''  -> sessions.jsonl,
    иначе sessions_{name}.jsonl."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not name:
        return _CACHE_DIR / "sessions.jsonl"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return _CACHE_DIR / f"sessions_{safe}.jsonl"


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Загружаем все записи из JSONL, пропуская битые строки."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                log.warning("jsonl parse error at %s:%d — skipped", path.name, lineno)
    return records


def _compact_jsonl(path: Path, sessions: dict[str, Session]) -> None:
    """Перезаписываем файл: одна запись на сессию (snapshot)."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for sess in sessions.values():
            rec = {
                "op": "snapshot",
                "session_id": sess.session_id,
                "created": sess.created,
                "last_access": sess.last_access,
                "turns": [asdict(t) for t in sess.turns],
            }
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    tmp.replace(path)
    log.info("compacted %s -> %d sessions", path.name, len(sessions))



# ─── ShadowStore — основной класс ────────────────────────────────────────────────────────

class ShadowStore:
    """Полный теневой кэш: сессионный префикс + message fallback.

    Потокобезопасен (RLock).  Хранит данные в JSONL-файле.
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        session_ttl: float = DEFAULT_SESSION_TTL,
        max_msg_entries: int = DEFAULT_MAX_MSG_ENTRIES,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        compact_every: int = DEFAULT_COMPACT_EVERY,
    ) -> None:
        self._name = name
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._msg_cache = MessageShadowCache(
            max_entries=max_msg_entries,
            max_candidates=max_candidates,
        )
        self._path = _jsonl_path(name)
        self._max_sessions = max_sessions
        self._session_ttl = session_ttl
        self._compact_every = compact_every
        self._writes_since_compact: int = 0

        # Статистика
        self._requests: int = 0
        self._prefix_hits: int = 0
        self._prefix_misses: int = 0
        self._turns_reused: int = 0
        self._turns_recorded: int = 0
        self._saved_chars: int = 0
        self._saved_tokens_est: float = 0.0

        # Загружаем ранее сохранённые сессии
        self._load()

    # ── Session management ──

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess:
                sess.touch()
            return sess

    def get_or_create_session(self, session_id: str) -> Session:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                sess = Session(session_id=session_id)
                self._sessions[session_id] = sess
                self._evict_sessions()
            sess.touch()
            return sess

    # ── Core operations ──

    def record_turn(
        self,
        session_id: str,
        role: str,
        ru_content: Any,
        en_content: Any,
        kind: str = "text",
    ) -> None:
        """Записать ход: оригинал RU и перевод EN."""
        ru_text = content_to_text(ru_content)
        en_text = content_to_text(en_content)
        turn = Turn(role=role, ru=ru_text, en=en_text, kind=kind)

        with self._lock:
            sess = self.get_or_create_session(session_id)
            sess.turns.append(turn)
            self._turns_recorded += 1

            # Дублируем в message cache (fallback)
            self._msg_cache.put(role, ru_text, en_text)

            # Персистенция (append)
            self._persist_turn(session_id, turn)

    def restore_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Попытаться подставить ранее переведённые EN-тексты в messages.

        Возвращает (новый список messages, кол-во reused ходов).

        Стратегия:
          1. Ищем сессию, считаем совпадение префикса.
          2. Для совпавших — подставляем EN из сессии.
          3. Для остальных — fallback в MessageShadowCache.
        """
        with self._lock:
            self._requests += 1
            sess = self._sessions.get(session_id)

            incoming_turns = self._messages_to_turns(messages)

            prefix_len = 0
            if sess and incoming_turns:
                prefix_len = sess.prefix_matches(incoming_turns)

            if prefix_len > 0:
                self._prefix_hits += 1
            else:
                self._prefix_misses += 1

            reused = 0
            restored: list[dict[str, Any]] = []

            for i, msg in enumerate(messages):
                role = msg.get("role", "")
                content = msg.get("content", "")
                ru_text = content_to_text(content)

                en_text: str | None = None

                # 1) Prefix hit — берём EN из сессии
                if sess and i < prefix_len:
                    en_text = sess.turns[i].en

                # 2) Fallback — MessageShadowCache
                if en_text is None:
                    en_text = self._msg_cache.get(role, ru_text)

                if en_text is not None and normalize(en_text) != normalize(ru_text):
                    new_msg = dict(msg)
                    new_msg["content"] = merge_restored_content(content, en_text)
                    restored.append(new_msg)
                    reused += 1
                else:
                    restored.append(msg)

            self._turns_reused += reused
            return restored, reused

    def record_token_savings(self, ru_chars: int, en_chars: int) -> None:
        """Фиксируем экономию: сколько символов RU не пришлось переводить заново,
        и оцениваем разницу в токенах."""
        with self._lock:
            self._saved_chars += ru_chars
            ru_tokens = ru_chars / _RU_CHARS_PER_TOKEN
            en_tokens = en_chars / _EN_CHARS_PER_TOKEN
            saved = ru_tokens - en_tokens
            if saved > 0:
                self._saved_tokens_est += saved

    def reset(self) -> None:
        """Полный сброс сессий, кэша сообщений и счётчиков."""
        with self._lock:
            self._sessions.clear()
            self._msg_cache._store.clear()
            self._msg_cache._access.clear()
            self._msg_cache.hits = 0
            self._msg_cache.misses = 0
            self._requests = 0
            self._prefix_hits = 0
            self._prefix_misses = 0
            self._turns_reused = 0
            self._turns_recorded = 0
            self._saved_chars = 0
            self._saved_tokens_est = 0.0
            self._writes_since_compact = 0
            if self._path.exists():
                try:
                    self._path.unlink()
                except OSError:
                    pass

    def stats_dict(self) -> dict[str, Any]:
        """Полная статистика кэша."""
        with self._lock:
            return {
                "store_name": self._name or "default",
                "sessions": len(self._sessions),
                "requests": self._requests,
                "prefix_hits": self._prefix_hits,
                "prefix_misses": self._prefix_misses,
                "prefix_hit_rate": (
                    round(self._prefix_hits / self._requests, 4)
                    if self._requests > 0 else 0.0
                ),
                "turns_reused": self._turns_reused,
                "turns_recorded": self._turns_recorded,
                "saved_chars": self._saved_chars,
                "saved_tokens_est": round(self._saved_tokens_est),
                "msg_cache": self._msg_cache.stats(),
                "writes_since_compact": self._writes_since_compact,
            }

    # ── Internal helpers ──

    @staticmethod
    def _messages_to_turns(messages: list[dict[str, Any]]) -> list[Turn]:
        """Конвертируем list[message-dict] -> list[Turn] для сравнения."""
        turns: list[Turn] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            ru_text = content_to_text(content)
            turns.append(Turn(role=role, ru=ru_text, en=""))
        return turns

    def _persist_turn(self, session_id: str, turn: Turn) -> None:
        """Дописываем ход в JSONL."""
        rec = {
            "op": "turn",
            "session_id": session_id,
            "turn": asdict(turn),
        }
        try:
            _append_jsonl(self._path, rec)
            self._writes_since_compact += 1
            if self._writes_since_compact >= self._compact_every:
                self._compact()
        except OSError as exc:
            log.error("jsonl write failed: %s", exc)

    def _compact(self) -> None:
        """Уплотняем JSONL до snapshot-формата."""
        try:
            _compact_jsonl(self._path, self._sessions)
            self._writes_since_compact = 0
        except OSError as exc:
            log.error("compaction failed: %s", exc)

    def _load(self) -> None:
        """Восстанавливаем сессии из JSONL при старте."""
        records = _load_jsonl(self._path)
        if not records:
            return

        now = time.time()
        for rec in records:
            op = rec.get("op", "")
            sid = rec.get("session_id", "")
            if not sid:
                continue

            if op == "snapshot":
                sess = Session(
                    session_id=sid,
                    created=rec.get("created", now),
                    last_access=rec.get("last_access", now),
                )
                for td in rec.get("turns", []):
                    sess.turns.append(Turn(
                        role=td.get("role", ""),
                        ru=td.get("ru", ""),
                        en=td.get("en", ""),
                        kind=td.get("kind", "text"),
                        ts=td.get("ts", now),
                    ))
                self._sessions[sid] = sess

            elif op == "turn":
                td = rec.get("turn", {})
                if not td:
                    continue
                sess = self._sessions.get(sid)
                if sess is None:
                    sess = Session(session_id=sid)
                    self._sessions[sid] = sess
                turn = Turn(
                    role=td.get("role", ""),
                    ru=td.get("ru", ""),
                    en=td.get("en", ""),
                    kind=td.get("kind", "text"),
                    ts=td.get("ts", now),
                )
                sess.turns.append(turn)

        # Заполняем message cache из загруженных сессий
        for sess in self._sessions.values():
            for t in sess.turns:
                if t.ru and t.en:
                    self._msg_cache.put(t.role, t.ru, t.en)

        # Удаляем протухшие
        self._evict_sessions()
        log.info(
            "loaded %d sessions (%d msg_cache entries) from %s",
            len(self._sessions), self._msg_cache.size(), self._path.name,
        )

    def _evict_sessions(self) -> None:
        """Удаляем протухшие и самые старые сессии если лимит превышен."""
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if (now - s.last_access) > self._session_ttl
        ]
        for sid in expired:
            del self._sessions[sid]

        if len(self._sessions) > self._max_sessions:
            by_access = sorted(
                self._sessions.items(),
                key=lambda kv: kv[1].last_access,
            )
            to_remove = len(self._sessions) - self._max_sessions
            for sid, _ in by_access[:to_remove]:
                del self._sessions[sid]



# ─── Factory ──────────────────────────────────────────────────────────────────────────

# Глобальный реестр store-ов (по имени), чтобы не дублировать
_stores_lock = threading.Lock()
_stores: dict[str | None, ShadowStore] = {}


def create_store(
    name: str | None = None,
    **kwargs: Any,
) -> ShadowStore:
    """Фабрика: создаёт (или возвращает существующий) ShadowStore.

    - ``create_store()``         -> default store  (sessions.jsonl)
    - ``create_store("openai")`` -> sessions_openai.jsonl
    - ``create_store("gemini")`` -> sessions_gemini.jsonl
    """
    with _stores_lock:
        if name in _stores:
            return _stores[name]
        store = ShadowStore(name=name, **kwargs)
        _stores[name] = store
        return store
