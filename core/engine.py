"""
FastAPI gateway engine for the portable proxy.

Each proxy instance owns its own upstream, ShadowStore, and Translator.
Call ``create_proxy_app(proxy_cfg, translator, store)`` to build an
independent FastAPI application ready to be served by Uvicorn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from core.shadow_cache import ShadowStore, content_to_text, create_store
from core.translator import Translator, TranslatorConfig

# -- constants ----------------------------------------------------------------
ROLE_NEVER_TRANSLATE: set[str] = {"system", "developer", "tool"}

_TIMEOUT_NORMAL = 300.0
_TIMEOUT_STREAM = 600.0

_FLUSH_SENTENCE_RE = re.compile(r"[.!?]\s$")
_CODE_FENCE = "```"

logger = logging.getLogger("proxy.engine")


# -- public factory -----------------------------------------------------------
def create_proxy_app(
    proxy_cfg: dict[str, Any],
    translator: Translator | None,
    store: ShadowStore | None,
    *,
    smooth_drip: bool = True,
) -> FastAPI:
    """Build a self-contained FastAPI app for one proxy endpoint."""

    pid: str = proxy_cfg["id"]
    name: str = proxy_cfg["name"]
    upstream_url: str = proxy_cfg["upstream_url"].rstrip("/")
    upstream_api_key: str = proxy_cfg.get("upstream_api_key", "")
    translate_enabled: bool = proxy_cfg.get("translate_enabled", False)
    translate_to: str = proxy_cfg.get("translate_to", "Russian")
    model_aliases: dict[str, str] = proxy_cfg.get("model_aliases", {})

    app = FastAPI(title=f"Proxy \u2013 {name}", version="1.0.0")

    reverse_aliases: dict[str, list[str]] = {}
    for alias, real in model_aliases.items():
        reverse_aliases.setdefault(real, []).append(alias)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "proxy_id": pid,
            "proxy_name": name,
            "upstream_url": upstream_url,
            "cache": {"enabled": store is not None, "stats": store.stats_dict() if store else {}},
            "translator": {
                "enabled": translate_enabled and translator is not None,
                "target_language": translate_to,
                "phrase_cache_size": len(translator._cache) if translator else 0,
            },
        }

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return {
            "cache": store.stats_dict() if store else {},
            "translator_phrase_cache_size": len(translator._cache) if translator else 0,
        }

    @app.post("/cache/reset")
    async def cache_reset() -> dict[str, str]:
        if store:
            store.reset()
        if translator:
            with translator._lock:
                translator._cache.clear()
        return {"status": "caches_reset"}

    @app.get("/v1/models")
    async def list_models(request: Request) -> JSONResponse:
        headers = _forward_headers(request, upstream_api_key)
        async with httpx.AsyncClient(timeout=_TIMEOUT_NORMAL) as client:
            resp = await client.get(f"{upstream_url}/v1/models", headers=headers)
        body = resp.json()
        if "data" in body and isinstance(body["data"], list):
            existing_ids = {m.get("id") for m in body["data"]}
            for alias, real in model_aliases.items():
                if alias not in existing_ids:
                    body["data"].append({"id": alias, "object": "model", "created": int(time.time()), "owned_by": "proxy-alias", "meta": {"alias_of": real}})
        return JSONResponse(content=body, status_code=resp.status_code)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        body = await _parse_body(request)
        bypass = request.headers.get("x-shadow-bypass", "").lower() in ("1", "true", "yes")
        raw_model = body.get("model", "")
        body["model"] = model_aliases.get(raw_model, raw_model)
        stream = body.get("stream", False)
        session_id = _session_id(body)
        prefix_matched = False
        prompt_en: str | None = None
        if translate_enabled and translator and store and not bypass:
            messages = body.get("messages", [])
            messages, prefix_matched, prompt_en = await build_request(messages, store, translator, session_id)
            body["messages"] = messages
        fwd_headers = _forward_headers(request, upstream_api_key)
        resp_headers: dict[str, str] = {"x-shadow-upstream": upstream_url}
        if prefix_matched: resp_headers["x-shadow-prefix-matched"] = "true"
        timeout = _TIMEOUT_STREAM if stream else _TIMEOUT_NORMAL
        if not stream:
            return await _handle_non_stream(body, fwd_headers, upstream_url, timeout, translate_enabled and not bypass, translator, store, translate_to, session_id, resp_headers)
        else:
            return StreamingResponse(_handle_openai_stream(body, fwd_headers, upstream_url, timeout, translate_enabled and not bypass, translator, store, translate_to, session_id, smooth_drip), media_type="text/event-stream", headers=resp_headers)

    @app.post("/v1/messages")
    async def messages_endpoint(request: Request) -> Any:
        body = await _parse_body(request)
        bypass = request.headers.get("x-shadow-bypass", "").lower() in ("1", "true", "yes")
        raw_model = body.get("model", "")
        body["model"] = model_aliases.get(raw_model, raw_model)
        stream = body.get("stream", False)
        session_id = _session_id(body)
        prefix_matched = False
        prompt_en: str | None = None
        if translate_enabled and translator and store and not bypass:
            messages = body.get("messages", [])
            messages, prefix_matched, prompt_en = await build_request(messages, store, translator, session_id)
            body["messages"] = messages
        fwd_headers = _forward_headers_anthropic(request, upstream_api_key)
        resp_headers: dict[str, str] = {"x-shadow-upstream": upstream_url}
        if prefix_matched: resp_headers["x-shadow-prefix-matched"] = "true"
        timeout = _TIMEOUT_STREAM if stream else _TIMEOUT_NORMAL
        if not stream:
            return await _handle_anthropic_non_stream(body, fwd_headers, upstream_url, timeout, translate_enabled and not bypass, translator, store, translate_to, session_id, resp_headers)
        else:
            return StreamingResponse(_handle_anthropic_stream(body, fwd_headers, upstream_url, timeout, translate_enabled and not bypass, translator, store, translate_to, session_id, smooth_drip), media_type="text/event-stream", headers=resp_headers)

    return app


# =============================================================================
#  Helper functions
# =============================================================================

async def build_request(
    messages: list[dict[str, Any]],
    store: ShadowStore,
    translator: Translator,
    session_id: str,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """
    Prepare request messages for upstream:
    1. Use store.restore_messages() to restore EN from shadow cache.
    2. Translate remaining RU user messages -> EN and commit new turns to session.

    Returns (transformed_messages, prefix_matched, last_prompt_en).
    All translator calls run in a thread pool to avoid blocking the event loop.
    """
    sess = store.get_session(session_id)
    prev_turns_len = len(sess.turns) if sess else 0

    restored, reused = store.restore_messages(session_id, messages)
    result: list[dict[str, Any]] = []
    prefix_matched = reused > 0
    prompt_en: str | None = None

    for idx, msg in enumerate(restored):
        orig_msg = messages[idx] if idx < len(messages) else msg
        orig_content = orig_msg.get("content", "")
        role = msg.get("role", "")
        if role in ROLE_NEVER_TRANSLATE:
            result.append(msg)
            if idx >= prev_turns_len:
                store.record_turn(session_id, role, orig_content, msg.get("content", ""))
            continue
        content = msg.get("content", "")
        if role == "user":
            text = content_to_text(content)
            if text and Translator.has_cyrillic(text):
                translated = await _map_text_parts_async(content, translator.translate_ru_to_en)
                prompt_en = content_to_text(translated)
                new_msg = {**msg, "content": translated}
                result.append(new_msg)
                store.record_token_savings(len(content_to_text(orig_content)), len(prompt_en))
                if idx >= prev_turns_len:
                    store.record_turn(session_id, role, orig_content, translated)
            else:
                prompt_en = text
                result.append(msg)
                if idx >= prev_turns_len:
                    store.record_turn(session_id, role, orig_content, content)
        else:
            result.append(msg)
            if idx >= prev_turns_len:
                store.record_turn(session_id, role, orig_content, content)
    return result, prefix_matched, prompt_en


async def _map_text_parts_async(content: Any, fn: Any) -> Any:
    """Apply *fn* to textual parts of *content* in a thread, skip tool_use/image."""
    if isinstance(content, str):
        return await asyncio.to_thread(fn, content)
    if isinstance(content, list):
        out: list[Any] = []
        for part in content:
            if isinstance(part, dict):
                ptype = part.get("type", "text")
                if ptype == "text":
                    translated = await asyncio.to_thread(fn, part.get("text", ""))
                    out.append({**part, "text": translated})
                else:
                    out.append(part)
            else:
                out.append(part)
        return out
    return content


# -- header forwarding --------------------------------------------------------

_HOP_BY_HOP = frozenset({
    "host", "connection", "keep-alive", "transfer-encoding",
    "te", "trailer", "upgrade", "proxy-authorization",
    "proxy-connection", "content-length",
})


def _forward_headers(request: Request, upstream_api_key: str) -> dict[str, str]:
    """Forward incoming headers, injecting Bearer auth when missing or dropping empty dummy auth."""
    out: dict[str, str] = {}
    has_auth = False
    clean_key = (upstream_api_key or "").strip()

    for key, value in request.headers.items():
        low = key.lower()
        if low in _HOP_BY_HOP: continue
        val = value.strip()
        if low == "authorization":
            parts = val.split(None, 1)
            token = parts[1].strip() if len(parts) > 1 else ""
            if not token:
                if clean_key:
                    out[key] = f"Bearer {clean_key}"
                    has_auth = True
                # Else: drop empty dummy Bearer header so h11 and local LLMs won't reject it
                continue
            has_auth = True
            out[key] = val
        else:
            out[key] = val

    if not has_auth and clean_key:
        out["Authorization"] = f"Bearer {clean_key}"
    return out


def _forward_headers_anthropic(request: Request, upstream_api_key: str) -> dict[str, str]:
    """Forward headers for Anthropic/Messages API, dropping empty dummy auth or injecting key."""
    out: dict[str, str] = {}
    has_auth = False
    clean_key = (upstream_api_key or "").strip()

    for key, value in request.headers.items():
        low = key.lower()
        if low in _HOP_BY_HOP: continue
        val = value.strip()
        if low == "authorization":
            parts = val.split(None, 1)
            token = parts[1].strip() if len(parts) > 1 else ""
            if not token:
                if clean_key:
                    out[key] = f"Bearer {clean_key}"
                    has_auth = True
                continue
            has_auth = True
            out[key] = val
        elif low == "x-api-key":
            if not val:
                if clean_key:
                    out[key] = clean_key
                    has_auth = True
                continue
            has_auth = True
            out[key] = val
        else:
            out[key] = val

    if not has_auth and clean_key:
        out["x-api-key"] = clean_key
    return out


# -- body parsing -------------------------------------------------------------

async def _parse_body(request: Request) -> dict[str, Any]:
    """Parse JSON body with UTF-8, falling back to cp1251."""
    raw = await request.body()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    return json.loads(text)


def _session_id(body: dict[str, Any]) -> str:
    """Derive a stable session key from the request body (system + first user msg)."""
    if "session_id" in body:
        return str(body["session_id"])
    msgs = body.get("messages", [])
    parts: list[str] = []
    for m in msgs:
        r = m.get("role", "")
        t = content_to_text(m.get("content", "")).strip()
        if r == "system" and t:
            parts.append("S:" + t[:200])
        elif r == "user" and t:
            parts.append("U:" + t[:200])
            break
    if parts:
        import hashlib
        h = hashlib.sha256("\n".join(parts).encode("utf-8", "ignore")).hexdigest()[:12]
        return f"sess-{h}"
    return "default"


# =============================================================================
#  OpenAI-format handlers
# =============================================================================

async def _handle_non_stream(
    body: dict[str, Any], headers: dict[str, str], upstream_url: str,
    timeout: float, do_translate: bool, translator: Translator | None,
    store: ShadowStore | None, target_lang: str, session_id: str,
    resp_headers: dict[str, str],
) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{upstream_url}/v1/chat/completions", json=body, headers=headers)
    except httpx.TimeoutException:
        return JSONResponse({"error": {"message": "Proxy: upstream request timed out", "type": "timeout_error"}}, status_code=504)
    except Exception as exc:
        return JSONResponse({"error": {"message": f"Proxy: upstream connection failed: {exc}", "type": "upstream_error"}}, status_code=502)

    try:
        data = resp.json()
    except Exception:
        return JSONResponse({"error": {"message": f"Upstream error {resp.status_code}: {resp.text}", "type": "upstream_error"}}, status_code=resp.status_code)

    translated = False
    for choice in data.get("choices", []):
        msg = choice.get("message", {})
        en_content = msg.get("content", "")
        en_reasoning = msg.get("reasoning_content", "")
        if do_translate and translator and en_content:
            ru_content = await asyncio.to_thread(translator.translate, en_content, target_lang)
            msg["content"] = ru_content
            translated = True
            if store: store.record_turn(session_id, "assistant", ru_content, en_content)
        if do_translate and translator and en_reasoning:
            msg["reasoning_content"] = await asyncio.to_thread(translator.translate, en_reasoning, target_lang)
            translated = True
    if translated: resp_headers["x-shadow-translated"] = "true"
    return JSONResponse(content=data, status_code=resp.status_code, headers=resp_headers)


async def _handle_openai_stream(
    body: dict[str, Any], headers: dict[str, str], upstream_url: str,
    timeout: float, do_translate: bool, translator: Translator | None,
    store: ShadowStore | None, target_lang: str, session_id: str,
    smooth_drip: bool = True,
) -> Any:
    """Async generator yielding SSE lines for OpenAI chat/completions stream."""
    translating = bool(do_translate and translator)
    content_buf = ""
    reasoning_buf = ""
    en_full = ""
    ru_full = ""
    in_code_block = False
    meta: dict[str, Any] = {}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{upstream_url}/v1/chat/completions", json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    err_bytes = await resp.aread()
                    err_msg = err_bytes.decode("utf-8", errors="replace")
                    yield f"data: {json.dumps({'error': {'message': f'Upstream error ({resp.status_code}): {err_msg}'}})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                async for raw_line in resp.aiter_lines():
                    if not raw_line.startswith("data: "):
                        if raw_line.strip(): yield raw_line + "\n\n"
                        continue
                    payload = raw_line[6:].strip()
                    if payload == "[DONE]":
                        if translating:
                            if content_buf:
                                text, ms = await _translate_timed(translator, content_buf, target_lang)
                                content_buf = ""; ru_full += text
                                async for ev in _drip_words(text, ms, _sse_openai_delta, smooth_drip, field="content", meta=meta): yield ev
                            if reasoning_buf:
                                text, ms = await _translate_timed(translator, reasoning_buf, target_lang)
                                reasoning_buf = ""
                                async for ev in _drip_words(text, ms, _sse_openai_delta, smooth_drip, field="reasoning_content", meta=meta): yield ev
                        yield "data: [DONE]\n\n"
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        yield raw_line + "\n\n"
                        continue
                    for k in ("id", "model", "created"):
                        if k in chunk: meta[k] = chunk[k]
                    choices = chunk.get("choices", [])
                    if not choices: yield raw_line + "\n\n"; continue
                    delta = choices[0].get("delta", {})
                    finish = choices[0].get("finish_reason")
                    rc = delta.get("reasoning_content", "")
                    ct = delta.get("content", "")
                    if not translating:
                        yield raw_line + "\n\n"
                        continue
                    if rc:
                        reasoning_buf += rc
                        text, reasoning_buf, ms = await _flush_buffer_async(reasoning_buf, False, translator, target_lang)
                        if text:
                            async for ev in _drip_words(text, ms, _sse_openai_delta, smooth_drip, field="reasoning_content", meta=meta): yield ev
                    if ct:
                        en_full += ct
                        content_buf += ct
                        if _CODE_FENCE in ct: in_code_block = not in_code_block
                        text, content_buf, ms = await _flush_buffer_async(content_buf, in_code_block, translator, target_lang)
                        if text:
                            ru_full += text
                            async for ev in _drip_words(text, ms, _sse_openai_delta, smooth_drip, field="content", meta=meta): yield ev
                    if finish:
                        if content_buf:
                            text, ms = await _translate_timed(translator, content_buf, target_lang)
                            content_buf = ""; ru_full += text
                            async for ev in _drip_words(text, ms, _sse_openai_delta, smooth_drip, field="content", meta=meta): yield ev
                        if reasoning_buf:
                            text, ms = await _translate_timed(translator, reasoning_buf, target_lang)
                            reasoning_buf = ""
                            async for ev in _drip_words(text, ms, _sse_openai_delta, smooth_drip, field="reasoning_content", meta=meta): yield ev
                        yield raw_line + "\n\n"
                    elif not rc and not ct:
                        yield raw_line + "\n\n"
    except httpx.TimeoutException:
        yield f"data: {json.dumps({'error': {'message': 'Proxy error: Upstream timed out.'}})}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'error': {'message': f'Proxy upstream error: {exc}'}})}\n\n"
        yield "data: [DONE]\n\n"

    if store and translating and en_full:
        store.record_turn(session_id, "assistant", ru_full, en_full)


# =============================================================================
#  Anthropic Messages format handlers
# =============================================================================

async def _handle_anthropic_non_stream(
    body: dict[str, Any], headers: dict[str, str], upstream_url: str,
    timeout: float, do_translate: bool, translator: Translator | None,
    store: ShadowStore | None, target_lang: str, session_id: str,
    resp_headers: dict[str, str],
) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{upstream_url}/v1/messages", json=body, headers=headers)
    except httpx.TimeoutException:
        return JSONResponse({"type": "error", "error": {"type": "timeout_error", "message": "Proxy: upstream request timed out"}}, status_code=504)
    except Exception as exc:
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": f"Proxy: upstream connection failed: {exc}"}}, status_code=502)

    try:
        data = resp.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": f"Upstream error {resp.status_code}: {resp.text}"}}, status_code=resp.status_code)

    translated = False
    for block in data.get("content", []):
        btype = block.get("type", "")
        if btype == "text" and do_translate and translator:
            en = block["text"]
            block["text"] = await asyncio.to_thread(translator.translate, en, target_lang)
            translated = True
            if store: store.record_turn(session_id, "assistant", block["text"], en)
        elif btype == "thinking" and do_translate and translator:
            block["thinking"] = await asyncio.to_thread(translator.translate, block.get("thinking", ""), target_lang)
            translated = True
    if translated: resp_headers["x-shadow-translated"] = "true"
    return JSONResponse(content=data, status_code=resp.status_code, headers=resp_headers)


async def _handle_anthropic_stream(
    body: dict[str, Any], headers: dict[str, str], upstream_url: str,
    timeout: float, do_translate: bool, translator: Translator | None,
    store: ShadowStore | None, target_lang: str, session_id: str,
    smooth_drip: bool = True,
) -> Any:
    """Async generator for Anthropic SSE stream (one clean event+data pair per frame)."""
    translating = bool(do_translate and translator)
    text_buf = ""
    thinking_buf = ""
    en_full = ""
    ru_full = ""
    in_code_block = False
    block_index = 0

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{upstream_url}/v1/messages", json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    err_bytes = await resp.aread()
                    err_text = err_bytes.decode("utf-8", errors="replace")
                    try:
                        err_json = json.loads(err_text)
                    except Exception:
                        err_json = {"type": "error", "error": {"type": "api_error", "message": f"Upstream error ({resp.status_code}): {err_text}"}}
                    yield f"event: error\ndata: {json.dumps(err_json, ensure_ascii=False)}\n\n"
                    return
                event_type: str | None = None
                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        # Defer: the header is re-emitted together with its data frame.
                        event_type = line[7:].strip()
                        continue
                    if not line.startswith("data: "):
                        yield line + "\n"
                        continue
                    payload = line[6:]
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        yield _sse_raw(event_type, payload); event_type = None; continue
                    ev_name = event_type or data.get("type")
                    if isinstance(data.get("index"), int):
                        block_index = data["index"]

                    if ev_name == "content_block_delta" and translating:
                        delta = data.get("delta", {})
                        dtype = delta.get("type", "")
                        if dtype == "text_delta":
                            fragment = delta.get("text", "")
                            en_full += fragment
                            text_buf += fragment
                            if _CODE_FENCE in fragment: in_code_block = not in_code_block
                            text, text_buf, ms = await _flush_buffer_async(text_buf, in_code_block, translator, target_lang)
                            if text:
                                ru_full += text
                                async for ev in _drip_words(text, ms, _sse_anthropic_delta, smooth_drip, delta_type="text_delta", index=block_index): yield ev
                        elif dtype == "thinking_delta":
                            thinking_buf += delta.get("thinking", "")
                            text, thinking_buf, ms = await _flush_buffer_async(thinking_buf, False, translator, target_lang)
                            if text:
                                async for ev in _drip_words(text, ms, _sse_anthropic_delta, smooth_drip, delta_type="thinking_delta", field="thinking", index=block_index): yield ev
                        else:
                            yield _sse_raw(ev_name, payload)
                    elif ev_name == "content_block_stop" and translating:
                        if text_buf:
                            text, ms = await _translate_timed(translator, text_buf, target_lang)
                            text_buf = ""; ru_full += text
                            async for ev in _drip_words(text, ms, _sse_anthropic_delta, smooth_drip, delta_type="text_delta", index=block_index): yield ev
                        if thinking_buf:
                            text, ms = await _translate_timed(translator, thinking_buf, target_lang)
                            thinking_buf = ""
                            async for ev in _drip_words(text, ms, _sse_anthropic_delta, smooth_drip, delta_type="thinking_delta", field="thinking", index=block_index): yield ev
                        yield _sse_raw(ev_name, payload)
                    else:
                        yield _sse_raw(ev_name, payload)
                    event_type = None
    except httpx.TimeoutException:
        err_json = {"type": "error", "error": {"type": "timeout_error", "message": "Proxy error: Upstream timed out."}}
        yield f"event: error\ndata: {json.dumps(err_json, ensure_ascii=False)}\n\n"
    except Exception as exc:
        err_json = {"type": "error", "error": {"type": "api_error", "message": f"Proxy upstream error: {exc}"}}
        yield f"event: error\ndata: {json.dumps(err_json, ensure_ascii=False)}\n\n"

    if store and translating and en_full:
        store.record_turn(session_id, "assistant", ru_full, en_full)


# =============================================================================
#  Streaming buffer, Smooth Drip & SSE helpers
# =============================================================================

async def _flush_buffer_async(
    buf: str, in_code_block: bool, translator: Translator, target_lang: str,
) -> tuple[str, str, float]:
    """
    Decide whether to translate-and-flush part of *buf* (non-blocking).

    Returns (text_to_emit, remaining_buffer, translate_ms).

    Strategy:
    - Inside a code fence: flush raw (no translation).
    - Buffer contains newline: translate everything up to last newline.
    - Buffer > 100 chars and ends with sentence punct + space: translate all.
    - Otherwise hold.
    """
    if not buf: return "", "", 0.0
    if in_code_block: return buf, "", 0.0
    nl_idx = buf.rfind("\n")
    if nl_idx != -1:
        to_translate = buf[: nl_idx + 1]
        rest = buf[nl_idx + 1 :]
        t0 = time.monotonic()
        translated = await asyncio.to_thread(translator.translate, to_translate, target_lang)
        elapsed_ms = (time.monotonic() - t0) * 1000
        return translated, rest, elapsed_ms
    if len(buf) > 100 and _FLUSH_SENTENCE_RE.search(buf):
        t0 = time.monotonic()
        translated = await asyncio.to_thread(translator.translate, buf, target_lang)
        elapsed_ms = (time.monotonic() - t0) * 1000
        return translated, "", elapsed_ms
    return "", buf, 0.0


async def _drip_words(
    text: str, translate_ms: float, format_fn: Any, drip_enabled: bool,
    **fmt_kwargs: Any,
) -> Any:
    """
    Yield SSE events word-by-word with adaptive delays (Smooth Drip).

    The delay per word is derived from actual translation time so output
    appears gradually — like model generation — without lagging behind.
    On very short text (<3 words) or drip disabled: emit all at once.
    """
    if not drip_enabled or not text or len(text) < 12:
        yield format_fn(text, **fmt_kwargs)
        return
    # Split preserving spaces: ["Hello ", "world! ", "How", ...]
    parts = re.findall(r'\S+\s*', text)
    if len(parts) <= 2:
        yield format_fn(text, **fmt_kwargs)
        return
    # Adaptive delay: spread over 60% of translation time, clamped [5ms, 40ms]
    total_drip_sec = translate_ms * 0.6 / 1000
    per_word = total_drip_sec / len(parts)
    per_word = max(0.005, min(per_word, 0.04))
    for i, word in enumerate(parts):
        yield format_fn(word, **fmt_kwargs)
        if i < len(parts) - 1:
            await asyncio.sleep(per_word)


async def _translate_timed(translator: Translator, text: str, target_lang: str) -> tuple[str, float]:
    """Translate in a worker thread; return (result, elapsed_ms)."""
    t0 = time.monotonic()
    out = await asyncio.to_thread(translator.translate, text, target_lang)
    return out, (time.monotonic() - t0) * 1000


def _sse_raw(event: str | None, payload: str) -> str:
    """Re-frame an untouched upstream data payload as one clean SSE event."""
    return (f"event: {event}\n" if event else "") + f"data: {payload}\n\n"


def _sse_openai_delta(text: str, field: str = "content", meta: dict[str, Any] | None = None) -> str:
    """Build one SSE data line with an OpenAI-shaped delta."""
    chunk: dict[str, Any] = {
        "id": (meta or {}).get("id", "chatcmpl-proxy"),
        "object": "chat.completion.chunk",
        "created": (meta or {}).get("created", int(time.time())),
        "model": (meta or {}).get("model", ""),
        "choices": [{"index": 0, "delta": {field: text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def _sse_anthropic_delta(text: str, delta_type: str = "text_delta", field: str = "text", index: int = 0) -> str:
    """Build one SSE event+data pair for Anthropic format (index is mandatory)."""
    data = {"type": "content_block_delta", "index": index, "delta": {"type": delta_type, field: text}}
    return f"event: content_block_delta\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
