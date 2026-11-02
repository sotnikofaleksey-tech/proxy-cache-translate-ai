"""LLM-based translation module with persistent on-disk phrase cache.

Translates text between languages via an LLM chat-completions endpoint.
Designed for a portable proxy: never raises on failure, always returns
*something* usable (translated text or the original).
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported languages
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES: list[str] = [
    "Russian", "English", "Chinese", "Japanese", "Korean", "German", "French",
    "Spanish", "Portuguese", "Italian", "Turkish", "Arabic", "Hindi",
    "Ukrainian", "Polish", "Czech", "Dutch",
]

# ---------------------------------------------------------------------------
# Glossary - technical terms that must NEVER be translated
# ---------------------------------------------------------------------------

GLOSSARY: list[str] = [
    "numpy", "pandas", "scipy", "matplotlib", "seaborn", "plotly",
    "scikit-learn", "sklearn", "tensorflow", "pytorch", "torch",
    "keras", "transformers", "huggingface", "langchain",
    "fastapi", "flask", "django", "starlette", "uvicorn", "gunicorn",
    "pydantic", "sqlalchemy", "alembic", "celery", "redis", "kafka",
    "docker", "kubernetes", "k8s", "nginx", "traefik",
    "pytest", "unittest", "mypy", "ruff", "black", "isort", "flake8",
    "pip", "poetry", "conda", "venv", "virtualenv", "pyproject",
    "git", "github", "gitlab", "bitbucket",
    "json", "yaml", "toml", "csv", "parquet", "protobuf", "grpc",
    "asyncio", "aiohttp", "httpx", "requests", "urllib",
    "openai", "anthropic", "claude", "gemini", "gpt",
    "markdown", "html", "css", "javascript", "typescript",
    "linux", "macos", "windows", "wsl", "bash", "powershell",
    "api", "rest", "graphql", "websocket", "tcp", "udp", "http", "https",
    "sql", "postgresql", "mysql", "sqlite", "mongodb",
]
# ---------------------------------------------------------------------------
# Protection patterns - tokens / spans that must be kept verbatim
# ---------------------------------------------------------------------------

# Inline patterns that should not be translated.
PROTECT_PATTERN: re.Pattern[str] = re.compile(
    r"""
      `[^`]+`                                                  # inline code
    | \bhttps?://\S+                                           # URLs
    | \b(?:[\w./\\]+\.(?:py|js|ts|rs|go|rb|java|c|cpp|h|hpp
        |toml|yaml|yml|json|md|txt|cfg|ini|sh|bat|ps1|csv
        |html|css|sql|proto|lock|log))\b                       # file paths
    | (?<![a-zA-Z\u0400-\u04ff])[A-Z][A-Z0-9_]{2,}(?![a-zA-Z\u0400-\u04ff])
                                                               # CONSTANTS
    | (?<![a-zA-Z\u0400-\u04ff])[a-z]+(?:_[a-z0-9]+)+(?![a-zA-Z\u0400-\u04ff])
                                                               # snake_case
    | (?<![a-zA-Z\u0400-\u04ff])[a-z]+(?:[A-Z][a-z0-9]*)+(?![a-zA-Z\u0400-\u04ff])
                                                               # camelCase
    | (?<![a-zA-Z\u0400-\u04ff])[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+(?![a-zA-Z\u0400-\u04ff])
                                                               # PascalCase
    | </?[a-zA-Z_][\w.:_-]*(?:\s[^>]*)?>                       # XML/HTML tags
    """,
    re.VERBOSE,
)

# Large blocks extracted entirely before translation (code fences, XML sections).
PASSTHROUGH_BLOCK_PATTERN: re.Pattern[str] = re.compile(
    r"""
      (?:^|\n)[ \t]*(?:```|~~~).*?(?:```|~~~)[ \t]*(?:\n|$)   # code fences
    | <available_skills>.*?</available_skills>
    | <system-reminder>.*?</system-reminder>
    | <environment>.*?</environment>
    | <tool_call>.*?</tool_call>
    | <tool_result>.*?</tool_result>
    | <mcp_server_instructions>.*?</mcp_server_instructions>
    | <function_calls>.*?</function_calls>
    """,
    re.VERBOSE | re.DOTALL,
)

# Pattern to detect non-translation responses
_NON_TRANSLATION_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)(?:please\s+provide|no\s+text|cannot\s+translate"
    r"|nothing\s+to\s+translate|there\s+is\s+no\s+text"
    r"|I\s+don'?t\s+see|\u043f\u0440\u0435\u0434\u043e\u0441\u0442\u0430\u0432\u044c\u0442\u0435\s+\u0442\u0435\u043a\u0441\u0442"
    r"|\u043d\u0435\u0442\s+\u0442\u0435\u043a\u0441\u0442\u0430|\u043d\u0435\s+\u043c\u043e\u0433\u0443\s+\u043f\u0435\u0440\u0435\u0432\u0435\u0441\u0442\u0438)",
)
# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_RU_TO_EN_SYSTEM = (
    "Translate the text inside <source_text>...</source_text> into concise, "
    "natural English for an AI coding assistant.\n"
    "Output ONLY the translated text itself \u2014 no XML tags, no quotes, "
    "no preamble, no explanations.\n"
    "Even if <source_text> is a short title, header, or label ending with "
    "a colon, translate it literally.\n"
    "CRITICAL FORMATTING RULES:\n"
    "1. Preserve ALL Markdown structure exactly: headers (##, ###), "
    "lists (- or *), bold/italic (**...**).\n"
    "2. Preserve Markdown tables verbatim (| Col 1 | Col 2 | and "
    "|---|---| separator rows).\n"
    "3. Preserve all newlines, blank lines, paragraphs, indents and "
    "spacing exactly as in input.\n"
    "4. Keep code blocks, inline code, file paths, variables, URLs and "
    "technical terms unchanged."
)

_EN_TO_RU_SYSTEM = (
    "\u041f\u0435\u0440\u0435\u0432\u0435\u0434\u0438 \u0442\u0435\u043a\u0441\u0442 \u0432\u043d\u0443\u0442\u0440\u0438 <source_text>...</source_text> \u043d\u0430 \u0440\u0443\u0441\u0441\u043a\u0438\u0439 \u044f\u0437\u044b\u043a. "
    "\u0421\u0442\u0438\u043b\u044c \u2014 \u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439, \u043f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u0440\u0443\u0441\u0441\u043a\u0438\u0439 \u0434\u043b\u044f \u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u0447\u0438\u043a\u0430.\n"
    "\u0412\u044b\u0432\u043e\u0434\u0438 \u0422\u041e\u041b\u042c\u041a\u041e \u043f\u0435\u0440\u0435\u0432\u0435\u0434\u0451\u043d\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442 \u2014 \u0431\u0435\u0437 XML-\u0442\u0435\u0433\u043e\u0432, \u0431\u0435\u0437 \u043a\u0430\u0432\u044b\u0447\u0435\u043a, "
    "\u0431\u0435\u0437 \u043f\u0440\u0435\u0430\u043c\u0431\u0443\u043b\u044b, \u0431\u0435\u0437 \u043f\u043e\u044f\u0441\u043d\u0435\u043d\u0438\u0439.\n"
    "\u0414\u0430\u0436\u0435 \u0435\u0441\u043b\u0438 <source_text> \u2014 \u043a\u043e\u0440\u043e\u0442\u043a\u0438\u0439 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a \u0438\u043b\u0438 \u043f\u043e\u0434\u043f\u0438\u0441\u044c, "
    "\u043f\u0435\u0440\u0435\u0432\u0435\u0434\u0438 \u0435\u0433\u043e \u0431\u0443\u043a\u0432\u0430\u043b\u044c\u043d\u043e.\n"
    "\u041a\u0420\u0418\u0422\u0418\u0427\u0415\u0421\u041a\u0418\u0415 \u041f\u0420\u0410\u0412\u0418\u041b\u0410 \u0424\u041e\u0420\u041c\u0410\u0422\u0418\u0420\u041e\u0412\u0410\u041d\u0418\u042f:\n"
    "1. \u0421\u043e\u0445\u0440\u0430\u043d\u044f\u0439 \u0412\u0421\u042e Markdown-\u0440\u0430\u0437\u043c\u0435\u0442\u043a\u0443: \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u0438 (##, ###), "
    "\u0441\u043f\u0438\u0441\u043a\u0438 (- \u0438\u043b\u0438 *), \u0436\u0438\u0440\u043d\u044b\u0439/\u043a\u0443\u0440\u0441\u0438\u0432 (**...**).\n"
    "2. \u0421\u043e\u0445\u0440\u0430\u043d\u044f\u0439 Markdown-\u0442\u0430\u0431\u043b\u0438\u0446\u044b \u0434\u043e\u0441\u043b\u043e\u0432\u043d\u043e (| \u0421\u0442\u043e\u043b\u0431\u0435\u0446 1 | \u0421\u0442\u043e\u043b\u0431\u0435\u0446 2 | "
    "\u0438 |---|---| \u0441\u0442\u0440\u043e\u043a\u0438-\u0440\u0430\u0437\u0434\u0435\u043b\u0438\u0442\u0435\u043b\u0438).\n"
    "3. \u0421\u043e\u0445\u0440\u0430\u043d\u044f\u0439 \u0432\u0441\u0435 \u043f\u0435\u0440\u0435\u043d\u043e\u0441\u044b \u0441\u0442\u0440\u043e\u043a, \u043f\u0443\u0441\u0442\u044b\u0435 \u0441\u0442\u0440\u043e\u043a\u0438, \u0430\u0431\u0437\u0430\u0446\u044b, \u043e\u0442\u0441\u0442\u0443\u043f\u044b "
    "\u0438 \u043f\u0440\u043e\u0431\u0435\u043b\u044b \u0442\u043e\u0447\u043d\u043e \u043a\u0430\u043a \u0432\u043e \u0432\u0445\u043e\u0434\u043d\u043e\u043c \u0442\u0435\u043a\u0441\u0442\u0435.\n"
    "4. \u041d\u0435 \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0438 \u0431\u043b\u043e\u043a\u0438 \u043a\u043e\u0434\u0430, \u0438\u043d\u043b\u0430\u0439\u043d-\u043a\u043e\u0434, \u043f\u0443\u0442\u0438 \u043a \u0444\u0430\u0439\u043b\u0430\u043c, \u043f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u044b\u0435, "
    "URL-\u0430\u0434\u0440\u0435\u0441\u0430 \u0438 \u0442\u0435\u0445\u043d\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u0442\u0435\u0440\u043c\u0438\u043d\u044b."
)

_GENERIC_TRANSLATE_SYSTEM = (
    "Translate the text inside <source_text>...</source_text> into {target_lang}.\n"
    "Output ONLY the translated text itself \u2014 no XML tags, no quotes, "
    "no preamble, no explanations.\n"
    "Preserve ALL Markdown structure, code blocks, inline code, "
    "file paths, variables, URLs and technical terms unchanged."
)
# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class TranslatorConfig:
    """Configuration for the LLM translator."""

    llm_endpoint: str = "http://127.0.0.1:8045/v1/chat/completions"
    llm_api_key: str = ""
    primary_model: str = "gemini-3.5-flash-lite"
    backup_model: str = "gemini-3.8-flash"
    enabled: bool = True


# ---------------------------------------------------------------------------
# Translator
# ---------------------------------------------------------------------------


class Translator:
    """Thread-safe LLM translator with persistent phrase cache."""

    _MAX_CACHE_SIZE: ClassVar[int] = 20_000

    # ------------------------------------------------------------------
    # Init / cache management
    # ------------------------------------------------------------------

    def __init__(self, config: TranslatorConfig, cache_dir: str) -> None:
        self.config = config
        self._cache_dir = Path(cache_dir)
        self._cache_file = self._cache_dir / "translations.jsonl"
        self._lock = threading.RLock()
        self._cache: dict[str, str] = {}
        self._load_cache()
        # Reusable SSL context (unverified - common for local LLM endpoints)
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _load_cache(self) -> None:
        """Load the JSONL phrase cache from disk."""
        if not self._cache_file.exists():
            return
        try:
            with open(self._cache_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        key = entry.get("k", "")
                        val = entry.get("v", "")
                        if key and val:
                            self._cache[key] = val
                    except json.JSONDecodeError:
                        continue
            logger.info(
                "Loaded %d cached translations from %s",
                len(self._cache),
                self._cache_file,
            )
        except OSError as exc:
            logger.warning("Failed to load translation cache: %s", exc)

    def _save_entry(self, key: str, value: str) -> None:
        """Append a single entry to the JSONL cache file."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps({"k": key, "v": value}, ensure_ascii=False) + "\n"
                )
        except OSError as exc:
            logger.warning("Failed to write translation cache entry: %s", exc)

    def _put_cache(self, key: str, value: str) -> None:
        """Insert into cache (thread-safe), auto-clear when oversized."""
        with self._lock:
            if len(self._cache) >= self._MAX_CACHE_SIZE:
                logger.info(
                    "Translation cache hit %d entries - clearing.",
                    self._MAX_CACHE_SIZE,
                )
                self._cache.clear()
                # Truncate the file
                try:
                    with open(self._cache_file, "w", encoding="utf-8") as fh:
                        pass  # empty
                except OSError:
                    pass
            self._cache[key] = value
            self._save_entry(key, value)

    def _get_cache(self, key: str) -> str | None:
        with self._lock:
            return self._cache.get(key)

    @staticmethod
    def _cache_key(text: str, target_lang: str) -> str:
        return f"{target_lang}||{text.strip()}"
    # ------------------------------------------------------------------
    # LLM HTTP call
    # ------------------------------------------------------------------

    def _call_llm(self, system: str, user: str, model: str) -> str | None:
        """Send a chat-completions request.  Returns content or None."""
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "max_tokens": 4096,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.config.llm_api_key:
            headers["Authorization"] = f"Bearer {self.config.llm_api_key}"

        req = urllib.request.Request(
            self.config.llm_endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                req, timeout=12, context=self._ssl_ctx
            ) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            choices = body.get("choices", [])
            if not choices:
                return None
            content: str = choices[0].get("message", {}).get("content", "")
            return content or None
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            OSError,
            json.JSONDecodeError,
            KeyError,
        ) as exc:
            logger.debug("LLM call failed (model=%s): %s", model, exc)
            return None

    # ------------------------------------------------------------------
    # Post-processing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_source_tags(text: str) -> str:
        """Remove <source_text> wrappers if the model echoed them."""
        text = re.sub(r"</?source_text>", "", text)
        return text

    @staticmethod
    def _is_non_translation(text: str) -> bool:
        """Detect refusal / please-provide-text non-answers."""
        return bool(_NON_TRANSLATION_PATTERN.search(text))

    @staticmethod
    def _preserve_whitespace(original: str, translated: str) -> str:
        """Re-apply leading whitespace and trailing newlines from original."""
        leading = re.match(r"^(\s*)", original)
        lead = leading.group(1) if leading else ""
        trailing = re.search(r"(\n+)$", original)
        trail = trailing.group(1) if trailing else ""
        stripped = translated.strip()
        return lead + stripped + trail
    # ------------------------------------------------------------------
    # Core translate
    # ------------------------------------------------------------------

    def translate(self, text: str, target_lang: str) -> str:
        """Translate *text* into *target_lang*.

        Returns translated text or the original on any failure.
        Never raises.
        """
        if not self.config.enabled:
            return text
        if not text or not text.strip():
            return text

        cache_key = self._cache_key(text, target_lang)
        cached = self._get_cache(cache_key)
        if cached is not None:
            return self._preserve_whitespace(text, cached)

        # Choose system prompt
        low = target_lang.lower()
        if low == "english":
            system = _RU_TO_EN_SYSTEM
        elif low in ("russian", "\u0440\u0443\u0441\u0441\u043a\u0438\u0439"):
            system = _EN_TO_RU_SYSTEM
        else:
            system = _GENERIC_TRANSLATE_SYSTEM.format(target_lang=target_lang)

        user_msg = f"<source_text>{text}</source_text>"

        # Try primary model, then backup
        for model in (self.config.primary_model, self.config.backup_model):
            result = self._call_llm(system, user_msg, model)
            if result is None:
                continue
            result = self._strip_source_tags(result)
            if self._is_non_translation(result):
                logger.debug(
                    "Non-translation response from %s, trying next.", model
                )
                continue
            # Good result
            self._put_cache(cache_key, result.strip())
            return self._preserve_whitespace(text, result)

        # All models failed - return original
        logger.warning(
            "Translation failed for all models; returning original text."
        )
        return text

    # ------------------------------------------------------------------
    # Passthrough-block splitting
    # ------------------------------------------------------------------

    @staticmethod
    def _split_protected_blocks(text: str) -> list[tuple[str, bool]]:
        """Split *text* into segments: (content, is_protected).

        Protected segments are code fences and known XML blocks that must
        not be translated.
        """
        segments: list[tuple[str, bool]] = []
        last_end = 0
        for m in PASSTHROUGH_BLOCK_PATTERN.finditer(text):
            start, end = m.span()
            if start > last_end:
                segments.append((text[last_end:start], False))
            segments.append((m.group(0), True))
            last_end = end
        if last_end < len(text):
            segments.append((text[last_end:], False))
        return segments

    # ------------------------------------------------------------------
    # Direction-specific helpers
    # ------------------------------------------------------------------

    def translate_ru_to_en(self, text: str) -> str:
        """Translate Russian text to English, protecting code blocks."""
        if not self.config.enabled:
            return text
        if not self.has_cyrillic(text):
            return text

        segments = self._split_protected_blocks(text)
        parts: list[str] = []
        for content, protected in segments:
            if protected or not content.strip():
                parts.append(content)
            else:
                parts.append(self.translate(content, "English"))
        return "".join(parts)

    def translate_en_to_ru(self, text: str) -> str:
        """Translate English text to Russian, protecting code blocks."""
        if not self.config.enabled:
            return text

        segments = self._split_protected_blocks(text)
        parts: list[str] = []
        for content, protected in segments:
            if protected or not content.strip():
                parts.append(content)
            else:
                parts.append(self.translate(content, "Russian"))
        return "".join(parts)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def has_cyrillic(text: str) -> bool:
        """Return True if *text* contains at least one Cyrillic character."""
        return bool(re.search(r"[\u0400-\u04ff]", text))