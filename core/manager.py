# -*- coding: utf-8 -*-
"""
Interactive CLI Manager & Server Controller for the Portable Proxy.

Allows managing multiple proxy endpoints (adding, editing, deleting,
starting, stopping, restarting, viewing live cache stats, and configuring
global LLM translation settings & target languages) from a single console.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import asdict
from typing import Any

import uvicorn

from core.config import (
    CACHE_DIR,
    AppConfig,
    ProxyEntry,
    TranslationSettings,
    load_config,
    save_config,
)
from core.engine import create_proxy_app
from core.shadow_cache import ShadowStore, create_store
from core.translator import SUPPORTED_LANGUAGES, Translator, TranslatorConfig


class RunningProxy:
    """Tracks a live uvicorn Server running in its own background thread."""

    def __init__(
        self,
        entry: ProxyEntry,
        server: uvicorn.Server,
        thread: threading.Thread,
        store: ShadowStore,
        translator: Translator,
    ) -> None:
        self.entry = entry
        self.server = server
        self.thread = thread
        self.store = store
        self.translator = translator
        self.started_at = time.time()

    @property
    def is_alive(self) -> bool:
        return self.thread.is_alive() and not self.server.should_exit


class ProxyController:
    """Manages the lifecycle of all configured proxy servers."""

    def __init__(self) -> None:
        self.config: AppConfig = load_config()
        self.running: dict[str, RunningProxy] = {}
        self._translator: Translator | None = None

    def reload_config(self) -> None:
        self.config = load_config()
        self._translator = None

    def get_translator(self) -> Translator:
        if self._translator is None:
            t_cfg = self.config.translation
            self._translator = Translator(
                TranslatorConfig(
                    llm_endpoint=t_cfg.llm_endpoint,
                    llm_api_key=t_cfg.llm_api_key,
                    primary_model=t_cfg.primary_model,
                    backup_model=t_cfg.backup_model,
                    enabled=True,
                ),
                cache_dir=CACHE_DIR,
            )
        return self._translator

    @staticmethod
    def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
        check_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex((check_host, port)) == 0

    @staticmethod
    def free_port(port: int) -> None:
        """Kill any external process occupying `port` on Windows."""
        my_pid = str(os.getpid())
        try:
            out = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore")
            pids = set()
            for line in out.splitlines():
                if f":{port} " in line or f":{port}\t" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        if pid.isdigit() and pid != "0" and pid != my_pid:
                            pids.add(pid)
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
        except Exception:
            pass

    def start_proxy(self, proxy_id: str, force_free_port: bool = False) -> tuple[bool, str]:
        if proxy_id in self.running and self.running[proxy_id].is_alive:
            return False, f"Proxy '{proxy_id}' is already running on port {self.running[proxy_id].entry.listen_port}."

        entry = next((p for p in self.config.proxies if p.id == proxy_id), None)
        if entry is None:
            return False, f"Proxy '{proxy_id}' not found."

        if self.is_port_in_use(entry.listen_port, entry.listen_host):
            if force_free_port:
                self.free_port(entry.listen_port)
                time.sleep(0.5)
            else:
                return (
                    False,
                    f"Port {entry.listen_port} is already in use by another process (use Restart/Force or free the port).",
                )

        store = create_store(entry.id)
        translator = self.get_translator()
        app = create_proxy_app(
            asdict(entry), translator=translator, store=store,
            smooth_drip=self.config.translation.smooth_drip,
        )

        uv_cfg = uvicorn.Config(
            app,
            host=entry.listen_host,
            port=entry.listen_port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(uv_cfg)

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(server.serve())
            except Exception as exc:
                print(f"\n[ERROR] Proxy '{entry.id}' crashed: {exc}")
            finally:
                loop.close()

        thread = threading.Thread(target=_run, name=f"proxy-{entry.id}", daemon=True)
        thread.start()

        # Wait briefly for port to bind
        for _ in range(25):
            if self.is_port_in_use(entry.listen_port, entry.listen_host):
                break
            if not thread.is_alive():
                return False, f"Proxy '{entry.id}' failed to bind to {entry.listen_host}:{entry.listen_port}."
            time.sleep(0.1)

        self.running[proxy_id] = RunningProxy(
            entry=entry,
            server=server,
            thread=thread,
            store=store,
            translator=translator,
        )
        return True, f"Started '{entry.name}' on http://{entry.listen_host}:{entry.listen_port}/v1 -> {entry.upstream_url}"

    def stop_proxy(self, proxy_id: str) -> tuple[bool, str]:
        rp = self.running.get(proxy_id)
        if rp is None or not rp.is_alive:
            self.running.pop(proxy_id, None)
            return False, f"Proxy '{proxy_id}' is not running."

        rp.server.should_exit = True
        rp.thread.join(timeout=4.0)
        self.running.pop(proxy_id, None)
        return True, f"Stopped '{rp.entry.name}' (port {rp.entry.listen_port})."

    def restart_proxy(self, proxy_id: str, force_free_port: bool = True) -> tuple[bool, str]:
        if proxy_id in self.running:
            self.stop_proxy(proxy_id)
            time.sleep(0.3)
        return self.start_proxy(proxy_id, force_free_port=force_free_port)

    def start_all(self, only_auto_start: bool = False, force_free_port: bool = False) -> list[str]:
        msgs: list[str] = []
        for p in self.config.proxies:
            if only_auto_start and not p.auto_start:
                continue
            ok, msg = self.start_proxy(p.id, force_free_port=force_free_port)
            msgs.append(f"[{'OK' if ok else 'SKIP'}] {msg}")
        return msgs

    def stop_all(self) -> list[str]:
        msgs: list[str] = []
        for pid in list(self.running.keys()):
            ok, msg = self.stop_proxy(pid)
            msgs.append(f"[{'OK' if ok else 'INFO'}] {msg}")
        return msgs


# --------------------------------------------------------------------------- #
# Interactive CLI Helpers
# --------------------------------------------------------------------------- #

def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default != "" else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val if val else default


def _ask_bool(prompt: str, default: bool = True) -> bool:
    d_str = "Y/n" if default else "y/N"
    try:
        val = input(f"{prompt} [{d_str}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not val:
        return default
    return val in ("y", "yes", "1", "true", "да", "д")


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\-]+", "-", name.strip().lower()).strip("-")
    return s or f"proxy-{int(time.time()) % 10000}"


def _pick_language(current: str = "Russian") -> str:
    print("\n  Available target languages:")
    for i, lang in enumerate(SUPPORTED_LANGUAGES, 1):
        marker = " *" if lang.lower() == current.lower() else ""
        print(f"    [{i:2d}] {lang}{marker}")
    print("    [ 0] Custom language name")
    choice = _ask("  Select language number or type name", current)
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(SUPPORTED_LANGUAGES):
            return SUPPORTED_LANGUAGES[idx - 1]
        if idx == 0:
            return _ask("  Enter custom language name (e.g. Vietnamese)", current)
    return choice


def _parse_aliases(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not raw.strip():
        return out
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                out[k] = v
    return out


def _format_aliases(aliases: dict[str, str]) -> str:
    if not aliases:
        return "-"
    return ", ".join(f"{k}={v}" for k, v in aliases.items())


def _print_proxies_table(ctrl: ProxyController) -> None:
    print("\n" + "═" * 102)
    print(
        f" {'#':<3} {'ID':<16} {'Name':<26} {'Listen (Entry)':<19} "
        f"{'Upstream (Provider)':<23} {'Translation':<12} {'Status'}"
    )
    print("─" * 102)
    if not ctrl.config.proxies:
        print("  (No proxies configured yet. Choose [2] Add new proxy.)")
    for idx, p in enumerate(ctrl.config.proxies, 1):
        rp = ctrl.running.get(p.id)
        if rp and rp.is_alive:
            status = "● RUNNING"
        elif ctrl.is_port_in_use(p.listen_port, p.listen_host):
            status = "◐ PORT BUSY (external)"
        else:
            status = "○ STOPPED"
        tr_str = f"ON -> {p.translate_to}" if p.translate_enabled else "OFF (Cache only)"
        entry_addr = f"{p.listen_host}:{p.listen_port}"
        print(
            f" [{idx}] {p.id:<16} {p.name[:25]:<26} {entry_addr:<19} "
            f"{p.upstream_url[:22]:<23} {tr_str:<12} {status}"
        )
        if p.model_aliases:
            print(f"      └─ Model aliases: {_format_aliases(p.model_aliases)}")
    print("═" * 102)


def _select_proxy(ctrl: ProxyController, action_label: str) -> ProxyEntry | None:
    if not ctrl.config.proxies:
        print("  No proxies configured.")
        return None
    _print_proxies_table(ctrl)
    raw = _ask(f"Select proxy # or ID to {action_label} (Enter to cancel)", "")
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(ctrl.config.proxies):
            return ctrl.config.proxies[idx - 1]
    for p in ctrl.config.proxies:
        if p.id.lower() == raw.lower():
            return p
    print(f"  [!] Proxy '{raw}' not found.")
    return None


# --------------------------------------------------------------------------- #
# Menu Actions
# --------------------------------------------------------------------------- #

def action_add_proxy(ctrl: ProxyController) -> None:
    print("\n─── Add New Proxy Endpoint ───")
    name = _ask("1. Display Name", "My Custom Proxy")
    default_id = _slugify(name)
    existing_ids = {p.id for p in ctrl.config.proxies}
    while default_id in existing_ids:
        default_id += "-2"
    pid = _slugify(_ask("2. Unique Proxy ID", default_id))
    if pid in existing_ids:
        print(f"  [!] Proxy with ID '{pid}' already exists.")
        return

    listen_host = _ask("3. User Entry Listen Host (0.0.0.0 for LAN+Local, 127.0.0.1 for Local only)", "0.0.0.0")
    used_ports = {p.listen_port for p in ctrl.config.proxies}
    default_port = 8092
    while default_port in used_ports:
        default_port += 1
    port_str = _ask("4. User Entry Listen Port", str(default_port))
    try:
        listen_port = int(port_str)
    except ValueError:
        print("  [!] Invalid port number.")
        return

    upstream_url = _ask("5. Provider Upstream URL (e.g. http://127.0.0.1:8080 or https://openrouter.ai/api)", "http://127.0.0.1:8080")
    upstream_url = re.sub(r"/v1/?$", "", upstream_url.strip().rstrip("/"))
    upstream_api_key = _ask("6. Provider API Key (leave empty if none or forwarded from client)", "")

    translate_enabled = _ask_bool("7. Enable bidirectional translation (User -> EN -> Model -> Target Lang)?", True)
    translate_to = "Russian"
    if translate_enabled:
        translate_to = _pick_language("Russian")

    aliases_raw = _ask(
        "8. Model aliases (optional, comma-separated alias=target_model, e.g. fast=gemini-3.5-flash-lite)",
        "",
    )
    model_aliases = _parse_aliases(aliases_raw)
    auto_start = _ask_bool("9. Auto-start this proxy on launch?", True)

    entry = ProxyEntry(
        id=pid,
        name=name,
        listen_host=listen_host,
        listen_port=listen_port,
        upstream_url=upstream_url,
        upstream_api_key=upstream_api_key,
        translate_enabled=translate_enabled,
        translate_to=translate_to,
        model_aliases=model_aliases,
        auto_start=auto_start,
        protocols=["openai", "anthropic"],
    )
    ctrl.config.proxies.append(entry)
    save_config(ctrl.config)
    print(f"\n  [OK] Proxy '{entry.name}' ({entry.id}) saved to config/proxies.json!")

    if _ask_bool("  Start this proxy right now?", True):
        ok, msg = ctrl.start_proxy(entry.id, force_free_port=True)
        print(f"  [{'OK' if ok else 'ERR'}] {msg}")


def action_edit_proxy(ctrl: ProxyController) -> None:
    entry = _select_proxy(ctrl, "edit")
    if entry is None:
        return

    print(f"\n─── Editing Proxy '{entry.name}' ({entry.id}) ───")
    print("  (Press Enter to keep current value)")
    entry.name = _ask("1. Display Name", entry.name)
    entry.listen_host = _ask("2. Listen Host", entry.listen_host)
    port_str = _ask("3. Listen Port", str(entry.listen_port))
    if port_str.isdigit():
        entry.listen_port = int(port_str)

    up_url = _ask("4. Provider Upstream URL", entry.upstream_url)
    entry.upstream_url = re.sub(r"/v1/?$", "", up_url.strip().rstrip("/"))
    entry.upstream_api_key = _ask("5. Provider API Key", entry.upstream_api_key)
    entry.translate_enabled = _ask_bool("6. Enable translation?", entry.translate_enabled)
    if entry.translate_enabled:
        if _ask_bool(f"7. Change target language (current: {entry.translate_to})?", False):
            entry.translate_to = _pick_language(entry.translate_to)

    cur_aliases = ", ".join(f"{k}={v}" for k, v in entry.model_aliases.items())
    new_aliases = _ask("8. Model aliases (alias=model, comma-separated, '-' to clear)", cur_aliases)
    if new_aliases.strip() == "-":
        entry.model_aliases = {}
    else:
        entry.model_aliases = _parse_aliases(new_aliases)

    entry.auto_start = _ask_bool("9. Auto-start on launch?", entry.auto_start)

    save_config(ctrl.config)
    print(f"\n  [OK] Changes saved for '{entry.name}'.")

    if entry.id in ctrl.running and ctrl.running[entry.id].is_alive:
        if _ask_bool("  Proxy is currently running. Restart it now to apply changes?", True):
            ok, msg = ctrl.restart_proxy(entry.id, force_free_port=True)
            print(f"  [{'OK' if ok else 'ERR'}] {msg}")


def action_delete_proxy(ctrl: ProxyController) -> None:
    entry = _select_proxy(ctrl, "delete")
    if entry is None:
        return
    if not _ask_bool(f"Are you sure you want to delete '{entry.name}' ({entry.id})?", False):
        return
    if entry.id in ctrl.running:
        ctrl.stop_proxy(entry.id)
    ctrl.config.proxies = [p for p in ctrl.config.proxies if p.id != entry.id]
    save_config(ctrl.config)
    print(f"  [OK] Deleted proxy '{entry.id}'.")


def action_view_stats(ctrl: ProxyController) -> None:
    print("\n─── Live Proxy & Shadow Cache Statistics ───")
    any_shown = False
    for p in ctrl.config.proxies:
        url = f"http://127.0.0.1:{p.listen_port}/stats"
        try:
            with urllib.request.urlopen(url, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            any_shown = True
            c = data.get("cache", {})
            print(f"\n  ● {p.name} ({p.id}) — http://127.0.0.1:{p.listen_port}/v1 -> {p.upstream_url}")
            print(f"    Translation          : {'ON -> ' + p.translate_to if p.translate_enabled else 'OFF'}")
            print(f"    Requests             : {c.get('requests', 0)}")
            print(f"    Prefix Hit Rate      : {c.get('prefix_hit_rate', 0.0) * 100:.1f}% (hits={c.get('prefix_hits', 0)}, misses={c.get('prefix_misses', 0)})")
            print(f"    Turns Reused (exact) : {c.get('turns_reused', 0)}  |  Turns Recorded: {c.get('turns_recorded', 0)}")
            print(f"    Active Sessions      : {c.get('sessions', 0)}  |  Msg Fallback Entries: {c.get('msg_cache_entries', 0)}")
            print(f"    Est. Tokens Saved    : ~{c.get('saved_tokens_est', 0)} tokens ({c.get('saved_chars', 0)} chars)")
            print(f"    Phrase Dict Entries  : {data.get('translator_phrase_cache_size', 0)}")
        except Exception:
            print(f"\n  ○ {p.name} ({p.id}) — port {p.listen_port} is not responding (stopped).")

    if any_shown and _ask_bool("\n  Reset/clear caches on all running proxies?", False):
        for p in ctrl.config.proxies:
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{p.listen_port}/cache/reset", data=b"{}", method="POST")
                urllib.request.urlopen(req, timeout=2.5).read()
                print(f"    [OK] Cache reset on {p.id} (port {p.listen_port})")
            except Exception:
                pass


def action_translation_settings(ctrl: ProxyController) -> None:
    t = ctrl.config.translation
    print("\n─── Global LLM Translator Settings ───")
    print(f"  Current Endpoint      : {t.llm_endpoint}")
    print(f"  Current API Key       : {t.llm_api_key[:10] + '...' if len(t.llm_api_key) > 10 else (t.llm_api_key or '(none)')}")
    print(f"  Primary Fast Model    : {t.primary_model}")
    print(f"  Backup Fallback Model : {t.backup_model}")
    print()

    if not _ask_bool("Edit translation settings?", True):
        return

    t.llm_endpoint = _ask("1. Translation LLM Endpoint (/v1/chat/completions)", t.llm_endpoint)
    t.llm_api_key = _ask("2. Translation LLM API Key", t.llm_api_key)
    t.primary_model = _ask("3. Primary Translation Model", t.primary_model)
    t.backup_model = _ask("4. Backup Translation Model", t.backup_model)

    save_config(ctrl.config)
    ctrl._translator = None
    print("  [OK] Translation settings saved! Restart running proxies to apply.")


def interactive_menu(ctrl: ProxyController, auto_start_on_boot: bool = True) -> None:
    if auto_start_on_boot:
        print("\n[Boot] Auto-starting configured proxies...")
        for msg in ctrl.start_all(only_auto_start=True, force_free_port=True):
            print(" ", msg)

    while True:
        print(
            """
╔══════════════════════════════════════════════════════════════════╗
║               PORTABLE PROXY MANAGER & TRANSLATOR                ║
╠══════════════════════════════════════════════════════════════════╣
║  [1] List all proxies & status                                   ║
║  [2] Add new proxy (entry port, provider URL, models, language)  ║
║  [3] Edit existing proxy                                         ║
║  [4] Delete proxy                                                ║
║  [5] Start ALL proxies                                           ║
║  [6] Stop ALL proxies                                            ║
║  [7] Restart ALL proxies (force-free ports)                      ║
║  [8] Start / Stop / Restart a single proxy                       ║
║  [9] View live Shadow Cache & token stats (+ reset cache)        ║
║ [10] Global Translation LLM settings (endpoint, models, key)     ║
║  [0] Exit (stops running proxies in this console)                ║
╚══════════════════════════════════════════════════════════════════╝"""
        )
        choice = _ask("Select option", "1")

        if choice == "1":
            _print_proxies_table(ctrl)
        elif choice == "2":
            action_add_proxy(ctrl)
        elif choice == "3":
            action_edit_proxy(ctrl)
        elif choice == "4":
            action_delete_proxy(ctrl)
        elif choice == "5":
            for m in ctrl.start_all(only_auto_start=False, force_free_port=True):
                print(" ", m)
        elif choice == "6":
            for m in ctrl.stop_all():
                print(" ", m)
        elif choice == "7":
            ctrl.stop_all()
            time.sleep(0.4)
            for m in ctrl.start_all(only_auto_start=False, force_free_port=True):
                print(" ", m)
        elif choice == "8":
            entry = _select_proxy(ctrl, "start/stop/restart")
            if entry:
                act = _ask("Action: [1] Start  [2] Stop  [3] Restart", "3")
                if act == "1":
                    ok, msg = ctrl.start_proxy(entry.id, force_free_port=True)
                elif act == "2":
                    ok, msg = ctrl.stop_proxy(entry.id)
                else:
                    ok, msg = ctrl.restart_proxy(entry.id, force_free_port=True)
                print(f"  [{'OK' if ok else 'INFO'}] {msg}")
        elif choice == "9":
            action_view_stats(ctrl)
        elif choice == "10":
            action_translation_settings(ctrl)
        elif choice in ("0", "q", "exit", "quit"):
            print("\nStopping all proxies managed by this session...")
            ctrl.stop_all()
            print("Goodbye!")
            break
        else:
            print("  [!] Unknown option.")


def run_headless(ctrl: ProxyController) -> None:
    """Headless mode (`--headless` or `serve`): starts all auto_start proxies and waits."""
    print("══════════════════════════════════════════════════════════════════")
    print("  PORTABLE PROXY — HEADLESS SERVER MODE")
    print("══════════════════════════════════════════════════════════════════")
    for msg in ctrl.start_all(only_auto_start=True, force_free_port=True):
        print(" ", msg)
    _print_proxies_table(ctrl)
    print("Press Ctrl+C to stop all proxies.\n")
    try:
        while True:
            time.sleep(2.0)
    except KeyboardInterrupt:
        print("\nShutting down...")
        ctrl.stop_all()


def main() -> None:
    ctrl = ProxyController()
    args = [a.lower() for a in sys.argv[1:]]
    if "--headless" in args or "serve" in args:
        run_headless(ctrl)
    elif "--status" in args or "status" in args:
        _print_proxies_table(ctrl)
        action_view_stats(ctrl)
    elif "--no-autostart" in args:
        interactive_menu(ctrl, auto_start_on_boot=False)
    else:
        interactive_menu(ctrl, auto_start_on_boot=True)


if __name__ == "__main__":
    main()
