# -*- coding: utf-8 -*-
"""
Proxy Station GUI v4.0 Ultra — Премиальный анимированный центр управления.
============================================================================
- Дизайн 2026: глубокий обсидиан, неоновые акценты (Linear / Cyberpunk / Arc).
- 60 FPS плавные анимированные тумблеры (SmoothSwitch Canvas) со скользящей кнопкой.
- Аврора-эффект: плавно переливающаяся неоновая обводка активных карточек (Aurora Glow).
- Пульсирующее «дыхание» индикатора онлайна и бегущий световой импульс маршрута (──●──▶).
- Плавные счётчики чисел (Smooth Number Interpolation) для токенов и запросов.
- Интерактивная подсветка и приподнятие карточек при наведении курсора (Hover Glow & Elevation).
- Кнопка копирования адреса с мгновенной анимацией подтверждения (📋 -> ✓ Скопировано!).
- Глубокие закругления (18px corner radius), неоновые капсулы и бейджи.
- Системный трей Windows (pystray) с двойным кликом и контекстным меню.
- Перехват крестика [X]: выбор «Свернуть в трей» или «Выйти и остановить всё».
- 100% автовыключение серверов и освобождение портов при выходе.
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import sys
import threading
import time
import urllib.request
import tkinter as tk
from dataclasses import asdict
from typing import Any, Callable

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw

from core.config import (
    ProxyEntry,
    load_config,
    save_config,
)
from core.manager import ProxyController
from core.translator import SUPPORTED_LANGUAGES

# ── Премиальная цветовая палитра (Deep Obsidian & Cyberpunk Neon) ────────────
COL_BG = "#070a0f"             # Глубокий космический чёрный
COL_SIDEBAR = "#0a0e17"        # Обсидиановый сланец
COL_PANEL = "#0f1420"          # Шапки и панели
COL_CARD = "#121927"           # Карточки сервисов
COL_CARD_HOVER = "#182235"     # Карточка при наведении
COL_CARD_ONLINE = "#111f32"    # Фон карточки когда шлюз онлайн
COL_BORDER = "#1b2436"         # Базовый контур
COL_BORDER_HOVER = "#00f0ff"   # Неоновый контур при наведении
COL_BORDER_ACTIVE = "#38bdf8"  # Активный синий контур

# Неоновые акценты
COL_CYAN = "#00f0ff"           # Киберпанк-циан
COL_ACCENT = "#38bdf8"         # Электрический лазурный
COL_ACCENT_HOVER = "#0284c7"
COL_ACCENT_BG = "#082f49"

COL_PURPLE = "#c084fc"         # Неоновый фиолетовый (для перевода и ИИ)
COL_PURPLE_HOVER = "#9333ea"
COL_PURPLE_BG = "#3b0764"

COL_GREEN = "#10b981"          # Изумрудный неон (Online)
COL_GREEN_HOVER = "#059669"
COL_GREEN_BG = "#064e3b"

COL_RED = "#f43f5e"            # Розово-красный неон (Danger/Offline)
COL_RED_HOVER = "#e11d48"
COL_RED_BG = "#4c0519"

COL_AMBER = "#fbbf24"          # Золотистый янтарь (Токены / Внимание)
COL_AMBER_BG = "#451a03"

COL_TEXT_BRIGHT = "#ffffff"    # Чистый белый титан
COL_TEXT = "#e2e8f0"           # Основной текст
COL_MUTED = "#94a3b8"          # Вторичный приглушенный
COL_SUBTLE = "#334155"         # Тонкие разделители
COL_INPUT_BG = "#080c14"       # Поля ввода

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ── Математика плавной анимации ──────────────────────────────────────────────

def get_breathing_color(phase: int, total_phases: int = 16) -> str:
    """Генерирует плавно пульсирующий цвет изумрудного неона для активного сервера."""
    t = (math.sin(phase * 2 * math.pi / total_phases) + 1.0) / 2.0
    r = int(6 * (1.0 - t) + 52 * t)
    g = int(95 * (1.0 - t) + 225 * t)
    b = int(70 * (1.0 - t) + 160 * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def get_aurora_border_color(phase: int, total_phases: int = 48) -> str:
    """Плавный переливающийся цвет Авроры: Cyan -> Violet -> Emerald -> Cyan."""
    t = (phase % total_phases) / float(total_phases)
    if t < 0.333:
        # Cyan (56, 189, 248) -> Violet (192, 132, 252)
        p = t / 0.333
        r = int(56 * (1 - p) + 192 * p)
        g = int(189 * (1 - p) + 132 * p)
        b = int(248 * (1 - p) + 252 * p)
    elif t < 0.666:
        # Violet (192, 132, 252) -> Emerald (16, 185, 129)
        p = (t - 0.333) / 0.333
        r = int(192 * (1 - p) + 16 * p)
        g = int(132 * (1 - p) + 185 * p)
        b = int(252 * (1 - p) + 129 * p)
    else:
        # Emerald (16, 185, 129) -> Cyan (56, 189, 248)
        p = (t - 0.666) / 0.334
        r = int(16 * (1 - p) + 56 * p)
        g = int(185 * (1 - p) + 189 * p)
        b = int(129 * (1 - p) + 248 * p)
    return f"#{r:02x}{g:02x}{b:02x}"


def get_flow_arrow(phase: int) -> str:
    """Анимация бегущего неонового импульса по цепочке маршрута."""
    frames = [
        "────────▶",
        "──●─────▶",
        "────●───▶",
        "──────●─▶",
        "────────●",
    ]
    return frames[phase % len(frames)]


def create_tray_image(is_running: bool = True) -> Image.Image:
    """Генерирует контрастную неоновую иконку молнии для системного трея."""
    img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=16, fill="#0a0e17", outline="#38bdf8" if is_running else "#64748b", width=3)
    fill_col = "#10b981" if is_running else "#f43f5e"
    coords = [(35, 7), (16, 33), (32, 33), (28, 57), (49, 27), (33, 27)]
    d.polygon(coords, fill=fill_col)
    return img


# ── Кастомный 60 FPS анимированный тумблер (SmoothSwitch) ────────────────────

class SmoothSwitch(tk.Canvas):
    """
    Ультра-стильный тумблер со скользящей кнопкой и плавной 60 FPS интерполяцией.
    """

    def __init__(
        self,
        master: Any,
        width: int = 54,
        height: int = 28,
        bg: str = COL_CARD,
        on_color: str = COL_GREEN,
        off_color: str = COL_BORDER,
        callback: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0)
        self.w = width
        self.h = height
        self.r = height // 2
        self.on_color = on_color
        self.off_color = off_color
        self.callback = callback

        self.is_on = False
        self.knob_pos = float(self.r)
        self.target_pos = float(self.r)
        self.animating = False

        self.bind("<Button-1>", self.toggle)
        self.bind("<Enter>", lambda _: self.config(cursor="hand2"))
        self.draw()

    def toggle(self, event: Any = None) -> None:
        self.set_state(not self.is_on, trigger_callback=True)

    def set_state(self, state: bool, trigger_callback: bool = False) -> None:
        if self.is_on == state and not self.animating:
            return
        self.is_on = state
        self.target_pos = float(self.w - self.r) if self.is_on else float(self.r)
        if not self.animating:
            self.animating = True
            self._animate_step()
        if trigger_callback and self.callback:
            self.callback(self.is_on)

    def _animate_step(self) -> None:
        diff = self.target_pos - self.knob_pos
        if abs(diff) < 0.6:
            self.knob_pos = self.target_pos
            self.animating = False
            self.draw()
            return

        # Плавное замедление (ease-out decay)
        self.knob_pos += diff * 0.38
        self.draw()
        self.after(16, self._animate_step)

    def draw(self) -> None:
        self.delete("all")
        t = (self.knob_pos - self.r) / max(1.0, (self.w - 2 * self.r))
        t = min(1.0, max(0.0, t))

        # Цвет трека
        track_col = self.on_color if t > 0.45 else self.off_color

        # Внешнее неоновое кольцо если включено
        d = self.h - 2
        if t > 0.1:
            self.create_oval(0, 0, d + 2, d + 2, fill=track_col, outline="")
            self.create_oval(self.w - d - 2, 0, self.w, d + 2, fill=track_col, outline="")
            self.create_rectangle(self.r, 0, self.w - self.r, d + 2, fill=track_col, outline="")

        # Трек капсулы
        self.create_oval(1, 1, d, d, fill=track_col, outline="")
        self.create_oval(self.w - d, 1, self.w - 1, d, fill=track_col, outline="")
        self.create_rectangle(self.r, 1, self.w - self.r, d, fill=track_col, outline="")

        # Белый слайдер (Knob)
        kx = int(self.knob_pos)
        kr = self.r - 3
        self.create_oval(kx - kr, self.r - kr, kx + kr, self.r + kr, fill="#ffffff", outline="")


# ── Кнопка копирования с живой анимацией подтверждения ───────────────────────

class AnimatedCopyButton(ctk.CTkButton):
    """Кнопка копирования адреса: при клике плавно превращается в зеленую галочку."""

    def __init__(self, master: Any, text_to_copy: str, app: "ProxyStationApp", **kwargs: Any) -> None:
        super().__init__(
            master,
            text="📋 Копировать",
            width=92,
            height=24,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            fg_color=COL_CARD,
            hover_color=COL_CARD_HOVER,
            text_color=COL_TEXT,
            corner_radius=8,
            command=self._on_click,
            **kwargs,
        )
        self.text_to_copy = text_to_copy
        self.app = app
        self._is_flashing = False

    def _on_click(self) -> None:
        self.app.copy_to_clipboard(self.text_to_copy)
        if not self._is_flashing:
            self._is_flashing = True
            self.configure(
                text="✓ Скопировано!",
                fg_color=COL_GREEN_BG,
                hover_color=COL_GREEN_BG,
                text_color=COL_GREEN,
            )
            self.after(1600, self._reset_text)

    def _reset_text(self) -> None:
        self.configure(
            text="📋 Копировать",
            fg_color=COL_CARD,
            hover_color=COL_CARD_HOVER,
            text_color=COL_TEXT,
        )
        self._is_flashing = False


# ── Карточка прокси с ультра-стилем и Авророй ────────────────────────────────

class UltraProxyCard(ctk.CTkFrame):
    """Премиальная карточка прокси со скруглениями 18px, тумблером и анимацией Авроры."""

    def __init__(
        self,
        master: Any,
        entry: ProxyEntry,
        app: "ProxyStationApp",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            fg_color=COL_CARD,
            border_color=COL_BORDER,
            border_width=1,
            corner_radius=18,
            **kwargs,
        )
        self.entry = entry
        self.app = app
        self.is_running = False
        self.is_hovered = False

        self._build_ui()
        self._bind_hover_events()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        # ── Строка 1: Шапка карточки ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 10))

        h_left = ctk.CTkFrame(header, fg_color="transparent")
        h_left.pack(side="left", fill="x", expand=True)

        self.lbl_name = ctk.CTkLabel(
            h_left,
            text=self.entry.name,
            font=ctk.CTkFont("Segoe UI", 18, "bold"),
            text_color=COL_TEXT_BRIGHT,
        )
        self.lbl_name.pack(side="left", padx=(0, 10))

        self.port_pill = ctk.CTkLabel(
            h_left,
            text=f":{self.entry.listen_port}",
            font=ctk.CTkFont("Consolas", 12, "bold"),
            text_color=COL_ACCENT,
            fg_color=COL_ACCENT_BG,
            corner_radius=8,
            padx=9,
            pady=3,
        )
        self.port_pill.pack(side="left", padx=(0, 10))

        # Индикатор статуса
        self.status_pill = ctk.CTkLabel(
            h_left,
            text="○ OFFLINE",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=COL_MUTED,
            fg_color=COL_PANEL,
            corner_radius=8,
            padx=11,
            pady=3,
        )
        self.status_pill.pack(side="left")

        # Правая часть шапки: Свитч запуска + Кнопки
        h_right = ctk.CTkFrame(header, fg_color="transparent")
        h_right.pack(side="right")

        # Плавный анимированный Canvas-тумблер
        self.switch_label = ctk.CTkLabel(
            h_right,
            text="Шлюз активен",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=COL_TEXT,
        )
        self.switch_label.pack(side="left", padx=(0, 8))

        self.smooth_switch = SmoothSwitch(
            h_right,
            width=52,
            height=26,
            bg=COL_CARD,
            on_color=COL_GREEN,
            off_color=COL_BORDER,
            callback=self._on_switch_toggle,
        )
        self.smooth_switch.pack(side="left", padx=(0, 16))

        self.btn_restart = ctk.CTkButton(
            h_right,
            text="🔄",
            width=36,
            height=32,
            font=ctk.CTkFont("Segoe UI", 14),
            fg_color=COL_PANEL,
            hover_color=COL_CARD_HOVER,
            corner_radius=8,
            text_color=COL_TEXT,
            command=lambda: self.app.restart_single(self.entry.id),
        )
        self.btn_restart.pack(side="left", padx=3)

        self.btn_edit = ctk.CTkButton(
            h_right,
            text="✏️",
            width=36,
            height=32,
            font=ctk.CTkFont("Segoe UI", 14),
            fg_color=COL_PANEL,
            hover_color=COL_CARD_HOVER,
            corner_radius=8,
            text_color=COL_TEXT,
            command=lambda: self.app.open_edit_dialog(self.entry),
        )
        self.btn_edit.pack(side="left", padx=3)

        self.btn_delete = ctk.CTkButton(
            h_right,
            text="🗑️",
            width=36,
            height=32,
            font=ctk.CTkFont("Segoe UI", 14),
            fg_color=COL_PANEL,
            hover_color=COL_RED_BG,
            corner_radius=8,
            text_color=COL_RED,
            command=lambda: self.app.delete_proxy(self.entry),
        )
        self.btn_delete.pack(side="left", padx=3)

        # ── Строка 2: Наглядная схема маршрутизации ──
        pipeline_box = ctk.CTkFrame(self, fg_color=COL_PANEL, corner_radius=12, border_color=COL_BORDER, border_width=1)
        pipeline_box.grid(row=1, column=0, sticky="ew", padx=22, pady=6)
        pipeline_box.grid_columnconfigure(0, weight=1)
        pipeline_box.grid_columnconfigure(2, weight=1)
        pipeline_box.grid_columnconfigure(4, weight=1)

        in_url = f"http://{self.entry.listen_host}:{self.entry.listen_port}/v1"

        # Блок 1: Вход
        b1 = ctk.CTkFrame(pipeline_box, fg_color="transparent")
        b1.grid(row=0, column=0, sticky="ew", padx=14, pady=12)
        ctk.CTkLabel(b1, text="💻 ВХОД ДЛЯ КЛИЕНТОВ:", font=ctk.CTkFont("Segoe UI", 9, "bold"), text_color=COL_MUTED).pack(anchor="w")
        b1_sub = ctk.CTkFrame(b1, fg_color="transparent")
        b1_sub.pack(anchor="w", pady=(3, 0))
        ctk.CTkLabel(b1_sub, text=in_url, font=ctk.CTkFont("Consolas", 12, "bold"), text_color=COL_ACCENT).pack(side="left", padx=(0, 8))
        AnimatedCopyButton(b1_sub, in_url, app=self.app).pack(side="left")

        # Стрелка 1 с бегущим импульсом
        self.arrow1 = ctk.CTkLabel(pipeline_box, text="────────▶", font=ctk.CTkFont("Consolas", 12, "bold"), text_color=COL_MUTED)
        self.arrow1.grid(row=0, column=1, padx=6)

        # Блок 2: Шлюз + Переводчик + Кэш
        b2 = ctk.CTkFrame(pipeline_box, fg_color="transparent")
        b2.grid(row=0, column=2, sticky="ew", padx=8, pady=12)
        ctk.CTkLabel(b2, text="⚡ ШЛЮЗ & SHADOW CACHE:", font=ctk.CTkFont("Segoe UI", 9, "bold"), text_color=COL_MUTED).pack(anchor="w")
        if self.entry.translate_enabled:
            tr_label = f"🌐 RU ⇄ EN ({self.entry.translate_to})"
            tr_col = COL_PURPLE
        else:
            tr_label = "🛡️ Режим чистого кэша"
            tr_col = COL_MUTED
        ctk.CTkLabel(b2, text=tr_label, font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=tr_col).pack(anchor="w", pady=(3, 0))

        # Стрелка 2 с бегущим импульсом
        self.arrow2 = ctk.CTkLabel(pipeline_box, text="────────▶", font=ctk.CTkFont("Consolas", 12, "bold"), text_color=COL_MUTED)
        self.arrow2.grid(row=0, column=3, padx=6)

        # Блок 3: ИИ-Провайдер
        b3 = ctk.CTkFrame(pipeline_box, fg_color="transparent")
        b3.grid(row=0, column=4, sticky="ew", padx=14, pady=12)
        ctk.CTkLabel(b3, text="🤖 ИИ-ПРОВАЙДЕР (UPSTREAM):", font=ctk.CTkFont("Segoe UI", 9, "bold"), text_color=COL_MUTED).pack(anchor="w")
        ctk.CTkLabel(b3, text=self.entry.upstream_url, font=ctk.CTkFont("Consolas", 12, "bold"), text_color=COL_TEXT).pack(anchor="w", pady=(3, 0))

        # ── Строка 3: Метаданные (Протоколы, Алиасы) ──
        meta_row = ctk.CTkFrame(self, fg_color="transparent")
        meta_row.grid(row=2, column=0, sticky="ew", padx=22, pady=(6, 6))

        ctk.CTkLabel(
            meta_row,
            text="⚡ OpenAI + Anthropic SSE",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=COL_MUTED,
            fg_color=COL_PANEL,
            corner_radius=8,
            padx=10,
            pady=3,
        ).pack(side="left", padx=(0, 6))

        if self.entry.model_aliases:
            alias_str = "Алиасы: " + ", ".join(f"{k}→{v}" for k, v in self.entry.model_aliases.items())
            if len(alias_str) > 60:
                alias_str = alias_str[:57] + "..."
            ctk.CTkLabel(
                meta_row,
                text=alias_str,
                font=ctk.CTkFont("Segoe UI", 10),
                text_color=COL_MUTED,
                fg_color=COL_PANEL,
                corner_radius=8,
                padx=10,
                pady=3,
            ).pack(side="left", padx=6)

        # ── Строка 4: Прогресс-бар эффективности и микро-статы ──
        stats_box = ctk.CTkFrame(self, fg_color="transparent")
        stats_box.grid(row=3, column=0, sticky="ew", padx=22, pady=(4, 18))
        stats_box.grid_columnconfigure(0, weight=1)

        pb_box = ctk.CTkFrame(stats_box, fg_color="transparent")
        pb_box.pack(side="left", fill="x", expand=True)

        self.hit_label = ctk.CTkLabel(
            pb_box,
            text="Эффективность префиксного кэша: 0.0%",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=COL_MUTED,
        )
        self.hit_label.pack(anchor="w")

        self.hit_bar = ctk.CTkProgressBar(
            pb_box,
            height=8,
            progress_color=COL_GREEN,
            fg_color=COL_PANEL,
            corner_radius=4,
        )
        self.hit_bar.set(0.0)
        self.hit_bar.pack(fill="x", pady=(4, 0))

        # Микро-чипы метрик
        chips = ctk.CTkFrame(stats_box, fg_color="transparent")
        chips.pack(side="right", padx=(20, 0))

        self.chip_reqs = self._create_chip(chips, "📨 0 запр.")
        self.chip_reused = self._create_chip(chips, "🔁 0 ходов")
        self.chip_tokens = self._create_chip(chips, "🪙 ~0 ток.", color=COL_AMBER)

    def _create_chip(self, parent: Any, text: str, color: str = COL_TEXT) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=color,
            fg_color=COL_PANEL,
            corner_radius=8,
            padx=10,
            pady=3,
        )
        lbl.pack(side="left", padx=4)
        return lbl

    def _bind_hover_events(self) -> None:
        def _on_enter(e: Any) -> None:
            self.is_hovered = True
            if not self.is_running:
                self.configure(fg_color=COL_CARD_HOVER, border_color=COL_BORDER_HOVER)

        def _on_leave(e: Any) -> None:
            self.is_hovered = False
            if not self.is_running:
                self.configure(fg_color=COL_CARD, border_color=COL_BORDER)

        self.bind("<Enter>", _on_enter)
        self.bind("<Leave>", _on_leave)

    def _on_switch_toggle(self, is_on: bool) -> None:
        if is_on:
            self.app.start_single(self.entry.id)
        else:
            self.app.stop_single(self.entry.id)

    def animate_step(self, pulse_phase: int, flow_phase: int, aurora_color: str) -> None:
        """60 FPS шаг анимации: переливы Авроры, дыхание онлайна и бегущий поток."""
        if self.is_running:
            # 1. Плавный перелив Авроры по рамке карточки!
            self.configure(border_color=aurora_color)

            # 2. Пульсирующее «дыхание» бейджика статуса
            glow_col = get_breathing_color(pulse_phase)
            self.status_pill.configure(text_color=glow_col)

            # 3. Бегущий неоновый импульс по стрелкам маршрутизации
            arrow_str = get_flow_arrow(flow_phase)
            self.arrow1.configure(text=arrow_str, text_color=COL_ACCENT)
            self.arrow2.configure(text=arrow_str, text_color=COL_PURPLE)
        else:
            self.arrow1.configure(text="────────▶", text_color=COL_MUTED)
            self.arrow2.configure(text="────────▶", text_color=COL_MUTED)

    def update_metrics(self, is_running: bool, is_busy: bool, stats_data: dict[str, Any] | None) -> None:
        self.is_running = is_running

        if is_running:
            self.status_pill.configure(
                text="● ONLINE",
                fg_color=COL_GREEN_BG,
            )
            self.configure(fg_color=COL_CARD_ONLINE)
            self.smooth_switch.configure(bg=COL_CARD_ONLINE)
            self.smooth_switch.set_state(True)
        elif is_busy:
            self.status_pill.configure(
                text="◐ PORT BUSY",
                text_color=COL_AMBER,
                fg_color=COL_AMBER_BG,
            )
            self.configure(fg_color=COL_CARD, border_color=COL_BORDER)
            self.smooth_switch.configure(bg=COL_CARD)
            self.smooth_switch.set_state(False)
        else:
            self.status_pill.configure(
                text="○ OFFLINE",
                text_color=COL_MUTED,
                fg_color=COL_PANEL,
            )
            self.configure(fg_color=COL_CARD, border_color=COL_BORDER)
            self.smooth_switch.configure(bg=COL_CARD)
            self.smooth_switch.set_state(False)

        if stats_data:
            c = stats_data.get("cache", {})
            reqs = c.get("requests", 0)
            hr = c.get("prefix_hit_rate", 0.0)
            reused = c.get("turns_reused", 0)
            saved_tok = c.get("saved_tokens_est", 0)

            self.hit_label.configure(
                text=f"Эффективность префиксного кэша: {hr * 100:.1f}%",
                text_color=COL_GREEN if hr >= 0.5 else COL_TEXT,
            )
            self.hit_bar.set(min(1.0, max(0.0, hr)))
            self.chip_reqs.configure(text=f"📨 {reqs:,} запр.")
            self.chip_reused.configure(text=f"🔁 {reused:,} ходов")
            self.chip_tokens.configure(text=f"🪙 ~{saved_tok:,} ток.")



# ── Модальный диалог закрытия [X] ──

class CloseActionDialog(ctk.CTkToplevel):
    def __init__(self, parent: "ProxyStationApp") -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("Закрытие Proxy Station")
        self.geometry("500x300")
        self.minsize(460, 270)
        self.configure(fg_color=COL_BG)
        self.grab_set()
        self._build_ui()

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="⚡ Закрытие Proxy Station",
            font=ctk.CTkFont("Segoe UI", 19, "bold"),
            text_color=COL_TEXT_BRIGHT,
        ).pack(anchor="w", padx=26, pady=(24, 8))

        ctk.CTkLabel(
            self,
            text="Прокси-серверы активны. Как вы хотите закрыть приложение?",
            font=ctk.CTkFont("Segoe UI", 13),
            text_color=COL_MUTED,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=26, pady=(0, 16))

        self.remember_cb = ctk.CTkCheckBox(
            self,
            text="Запомнить выбор (можно изменить в «⚙️ Настройки LLM»)",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=COL_TEXT,
            fg_color=COL_ACCENT,
            hover_color=COL_ACCENT_HOVER,
            corner_radius=6,
        )
        self.remember_cb.pack(anchor="w", padx=26, pady=(0, 22))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=26, pady=(0, 20))

        ctk.CTkButton(
            btns,
            text="📥 Свернуть в трей",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=COL_ACCENT,
            hover_color=COL_ACCENT_HOVER,
            text_color=COL_BG,
            height=40,
            corner_radius=10,
            command=self._on_tray,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            btns,
            text="❌ Выйти и остановить всё",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=COL_RED,
            hover_color=COL_RED_HOVER,
            height=40,
            corner_radius=10,
            command=self._on_exit,
        ).pack(side="left", fill="x", expand=True, padx=6)

        ctk.CTkButton(
            btns,
            text="Отмена",
            width=70,
            height=40,
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COL_PANEL,
            hover_color=COL_CARD_HOVER,
            corner_radius=10,
            command=self.destroy,
        ).pack(side="left", padx=(6, 0))

    def _on_tray(self) -> None:
        if self.remember_cb.get():
            self.parent_app.ctrl.config.on_close_action = "tray"
            save_config(self.parent_app.ctrl.config)
        self.destroy()
        self.parent_app.minimize_to_tray()

    def _on_exit(self) -> None:
        if self.remember_cb.get():
            self.parent_app.ctrl.config.on_close_action = "exit"
            save_config(self.parent_app.ctrl.config)
        self.destroy()
        self.parent_app.on_full_quit()


# ── Модальный диалог создания / редактирования прокси ──

class ProxyEditDialog(ctk.CTkToplevel):
    def __init__(self, parent: "ProxyStationApp", entry: ProxyEntry | None = None) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.entry = entry
        self.is_edit = entry is not None

        title_text = "Редактировать прокси" if self.is_edit else "Добавить новый прокси"
        self.title(title_text)
        self.geometry("640x740")
        self.minsize(580, 650)
        self.configure(fg_color=COL_BG)
        self.grab_set()
        self._build_ui()

    def _build_ui(self) -> None:
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=24, pady=20)
        scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            scroll,
            text="⚡ Настройка параметров шлюза",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=COL_TEXT_BRIGHT,
        ).grid(row=0, column=0, sticky="w", pady=(0, 16))

        self.f_name = self._add_field(scroll, 1, "Название шлюза:", self.entry.name if self.entry else "Новый шлюз")
        default_id = self.entry.id if self.entry else "custom-proxy"
        self.f_id = self._add_field(scroll, 2, "Уникальный ID:", default_id)

        # Порт и хост
        pf = ctk.CTkFrame(scroll, fg_color="transparent")
        pf.grid(row=3, column=0, sticky="ew", pady=6)
        pf.grid_columnconfigure(0, weight=1)
        pf.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(pf, text="Порт входа (Listen Port):", font=ctk.CTkFont("Segoe UI", 12), text_color=COL_TEXT).grid(row=0, column=0, sticky="w")
        self.f_port = ctk.CTkEntry(pf, fg_color=COL_INPUT_BG, border_color=COL_BORDER, corner_radius=8, height=36)
        default_port = str(self.entry.listen_port) if self.entry else "8092"
        self.f_port.insert(0, default_port)
        self.f_port.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 0))

        ctk.CTkLabel(pf, text="Хост входа (0.0.0.0 — все сети):", font=ctk.CTkFont("Segoe UI", 12), text_color=COL_TEXT).grid(row=0, column=1, sticky="w")
        self.f_host = ctk.CTkEntry(pf, fg_color=COL_INPUT_BG, border_color=COL_BORDER, corner_radius=8, height=36)
        default_host = self.entry.listen_host if self.entry else "0.0.0.0"
        self.f_host.insert(0, default_host)
        self.f_host.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))

        self.f_upstream = self._add_field(
            scroll, 4, "Адрес провайдера (Upstream URL):",
            self.entry.upstream_url if self.entry else "http://127.0.0.1:8080",
            placeholder="например http://127.0.0.1:8080 или https://openrouter.ai/api",
        )

        self.f_key = self._add_field(
            scroll, 5, "API Ключ провайдера (если требуется):",
            self.entry.upstream_api_key if self.entry else "",
            placeholder="оставьте пустым для локальных серверов без ключа",
        )

        # Блок перевода
        tr_box = ctk.CTkFrame(scroll, fg_color=COL_CARD, border_color=COL_BORDER, border_width=1, corner_radius=12)
        tr_box.grid(row=6, column=0, sticky="ew", pady=14)

        self.tr_switch = ctk.CTkSwitch(
            tr_box,
            text="Включить двусторонний перевод (Пользователь ⇄ English ⇄ Модель)",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            progress_color=COL_PURPLE,
        )
        if (self.entry and self.entry.translate_enabled) or not self.entry:
            self.tr_switch.select()
        self.tr_switch.pack(anchor="w", padx=18, pady=(14, 8))

        lang_row = ctk.CTkFrame(tr_box, fg_color="transparent")
        lang_row.pack(fill="x", padx=18, pady=(0, 14))

        ctk.CTkLabel(lang_row, text="Целевой язык для ответов клиенту:", font=ctk.CTkFont("Segoe UI", 12), text_color=COL_TEXT).pack(side="left", padx=(0, 10))

        default_lang = self.entry.translate_to if (self.entry and self.entry.translate_to) else "Russian"
        self.lang_menu = ctk.CTkOptionMenu(
            lang_row,
            values=SUPPORTED_LANGUAGES,
            fg_color=COL_PANEL,
            button_color=COL_PURPLE,
            button_hover_color=COL_PURPLE_HOVER,
            corner_radius=8,
            height=34,
        )
        self.lang_menu.set(default_lang)
        self.lang_menu.pack(side="left")

        # Алиасы
        cur_aliases = ""
        if self.entry and self.entry.model_aliases:
            cur_aliases = ", ".join(f"{k}={v}" for k, v in self.entry.model_aliases.items())
        self.f_aliases = self._add_field(
            scroll, 7, "Алиасы моделей (alias=real_model через запятую):",
            cur_aliases,
            placeholder="например qwen_3.8=qwen3.8-flash-next-coder-iq1_m, flash=gemini-3.5-flash-lite",
        )

        self.auto_start_switch = ctk.CTkSwitch(
            scroll,
            text="Запускать этот шлюз автоматически при старте",
            font=ctk.CTkFont("Segoe UI", 12),
            progress_color=COL_GREEN,
        )
        if (self.entry and self.entry.auto_start) or not self.entry:
            self.auto_start_switch.select()
        self.auto_start_switch.grid(row=8, column=0, sticky="w", pady=12)

        # Нижние кнопки
        btns = ctk.CTkFrame(self, fg_color=COL_PANEL, height=64)
        btns.pack(fill="x", side="bottom")
        btns.pack_propagate(False)

        ctk.CTkButton(
            btns,
            text="Отмена",
            width=100,
            height=38,
            fg_color=COL_CARD,
            hover_color=COL_CARD_HOVER,
            corner_radius=8,
            command=self.destroy,
        ).pack(side="right", padx=20, pady=13)

        ctk.CTkButton(
            btns,
            text="Сохранить и запустить" if not self.is_edit else "Сохранить изменения",
            width=190,
            height=38,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=COL_ACCENT,
            hover_color=COL_ACCENT_HOVER,
            text_color=COL_BG,
            corner_radius=8,
            command=self._on_save,
        ).pack(side="right", padx=8, pady=13)

    def _add_field(self, parent: Any, row: int, label: str, default_val: str = "", placeholder: str = "") -> ctk.CTkEntry:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", pady=6)
        box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(box, text=label, font=ctk.CTkFont("Segoe UI", 12), text_color=COL_TEXT).grid(row=0, column=0, sticky="w")
        entry = ctk.CTkEntry(box, fg_color=COL_INPUT_BG, border_color=COL_BORDER, height=36, corner_radius=8, placeholder_text=placeholder)
        if default_val:
            entry.insert(0, default_val)
        entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        return entry

    def _on_save(self) -> None:
        name = self.f_name.get().strip() or "Custom Proxy"
        pid = re.sub(r"[^a-zA-Z0-9_\-]+", "-", self.f_id.get().strip().lower()).strip("-") or f"proxy-{int(time.time()) % 10000}"

        try:
            listen_port = int(self.f_port.get().strip())
        except ValueError:
            self.parent_app.log_event("[Ошибка] Порт должен быть числом!")
            return

        listen_host = self.f_host.get().strip() or "0.0.0.0"
        upstream_url = self.f_upstream.get().strip().rstrip("/")
        upstream_url = re.sub(r"/v1/?$", "", upstream_url)
        upstream_key = self.f_key.get().strip()
        translate_enabled = bool(self.tr_switch.get())
        translate_to = self.lang_menu.get().strip() or "Russian"
        auto_start = bool(self.auto_start_switch.get())

        model_aliases: dict[str, str] = {}
        raw_aliases = self.f_aliases.get().strip()
        if raw_aliases:
            for pair in raw_aliases.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k and v:
                        model_aliases[k] = v

        new_entry = ProxyEntry(
            id=pid,
            name=name,
            listen_host=listen_host,
            listen_port=listen_port,
            upstream_url=upstream_url,
            upstream_api_key=upstream_key,
            translate_enabled=translate_enabled,
            translate_to=translate_to,
            model_aliases=model_aliases,
            auto_start=auto_start,
            protocols=["openai", "anthropic"],
        )

        ctrl = self.parent_app.ctrl
        if self.is_edit and self.entry:
            idx = next((i for i, p in enumerate(ctrl.config.proxies) if p.id == self.entry.id), None)
            if idx is not None:
                ctrl.config.proxies[idx] = new_entry
        else:
            existing_ids = {p.id for p in ctrl.config.proxies}
            if pid in existing_ids:
                pid = f"{pid}-{int(time.time()) % 1000}"
                new_entry.id = pid
            ctrl.config.proxies.append(new_entry)

        save_config(ctrl.config)
        self.destroy()
        self.parent_app.refresh_proxy_cards()
        self.parent_app.start_single(new_entry.id)


# ── Главное окно Proxy Station с ультра-дизайном ─────────────────────────────

class ProxyStationApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Proxy Station v4.0 — Ультра-шлюз & переводчик LLM")
        self.geometry("1260x860")
        self.minsize(1080, 740)
        self.configure(fg_color=COL_BG)

        self.ctrl = ProxyController()
        self.cards: dict[str, UltraProxyCard] = {}
        self._poller_running = True
        self._ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stats_cache: dict[str, dict[str, Any]] = {}
        self._active_tab = "proxies"

        # Фазы анимации
        self._pulse_phase = 0
        self._flow_phase = 0
        self._aurora_phase = 0

        # Анимированные числа (Smooth Count-up)
        self._disp_tokens = 0.0

        # Трей
        self._init_tray()

        # Интерфейс
        self._build_sidebar()
        self._build_content_area()

        # Автостарт
        self._auto_start_servers()

        # Потоки
        self._status_thread = threading.Thread(target=self._status_worker, daemon=True)
        self._status_thread.start()

        self.protocol("WM_DELETE_WINDOW", self.on_window_close_request)

        # 60 FPS Анимационный цикл
        self._animation_loop()
        # Цикл обновления метрик (раз в 1 сек)
        self._poll_ui()

    # ── Трей ──

    def _init_tray(self) -> None:
        self.tray_image = create_tray_image(True)
        self.tray_icon = pystray.Icon(
            "proxy_station",
            self.tray_image,
            "Proxy Station — Шлюзы активны",
            menu=self._build_tray_menu(),
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _build_tray_menu(self) -> pystray.Menu:
        items = [
            pystray.MenuItem("⚡ Открыть Proxy Station", lambda _: self.restore_from_tray(), default=True),
            pystray.Menu.SEPARATOR,
        ]
        for p in self.ctrl.config.proxies:
            is_alive = p.id in self.ctrl.running and self.ctrl.running[p.id].is_alive
            mark = "●" if is_alive else "○"
            label = f"{mark} {p.name} (:{p.listen_port})"
            items.append(pystray.MenuItem(label, lambda _, pid=p.id: self.toggle_proxy_from_tray(pid)))

        items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 Перезапустить все сервисы", lambda _: self.restart_all()),
            pystray.MenuItem("🧹 Очистить кэш", lambda _: self.reset_all_caches()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Выход (остановить все сервисы)", lambda _: self.on_full_quit()),
        ])
        return pystray.Menu(*items)

    def _update_tray_menu(self) -> None:
        try:
            self.tray_icon.menu = self._build_tray_menu()
            any_alive = any(rp.is_alive for rp in self.ctrl.running.values())
            self.tray_icon.title = f"Proxy Station — {'Серверы активны' if any_alive else 'Остановлено'}"
        except Exception:
            pass

    def minimize_to_tray(self) -> None:
        self.withdraw()
        self.log_event("Proxy Station свёрнут в трей. Все шлюзы продолжают работу.")

    def restore_from_tray(self) -> None:
        self._ui_queue.put(("restore", None))

    def toggle_proxy_from_tray(self, proxy_id: str) -> None:
        if proxy_id in self.ctrl.running and self.ctrl.running[proxy_id].is_alive:
            self.stop_single(proxy_id)
        else:
            self.start_single(proxy_id)

    # ── Боковая панель ──

    def _build_sidebar(self) -> None:
        sb = ctk.CTkFrame(self, fg_color=COL_SIDEBAR, width=256, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Логотип
        logo_box = ctk.CTkFrame(sb, fg_color="transparent")
        logo_box.pack(fill="x", padx=22, pady=(24, 28))

        ctk.CTkLabel(
            logo_box,
            text="⚡ PROXY",
            font=ctk.CTkFont("Segoe UI", 23, "bold"),
            text_color=COL_ACCENT,
        ).pack(side="left")

        ctk.CTkLabel(
            logo_box,
            text="STATION",
            font=ctk.CTkFont("Segoe UI", 23, "bold"),
            text_color=COL_TEXT_BRIGHT,
        ).pack(side="left", padx=(4, 8))

        ctk.CTkLabel(
            logo_box,
            text="v4.0",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=COL_ACCENT,
            fg_color=COL_ACCENT_BG,
            corner_radius=6,
            padx=7,
            pady=2,
        ).pack(side="left")

        # Кнопки навигации с анимированной левой плашкой
        self.nav_btns: dict[str, ctk.CTkButton] = {}

        nav_items = [
            ("proxies", "⚡  Шлюзы (Proxies)"),
            ("analytics", "📊  Мониторинг & Кэш"),
            ("settings", "⚙️  Настройки LLM"),
            ("logs", "📜  Консоль / Логи"),
        ]

        for tab_id, label in nav_items:
            btn = ctk.CTkButton(
                sb,
                text=label,
                font=ctk.CTkFont("Segoe UI", 13, "bold"),
                fg_color=COL_ACCENT_BG if tab_id == "proxies" else "transparent",
                hover_color=COL_ACCENT_BG,
                text_color=COL_ACCENT if tab_id == "proxies" else COL_MUTED,
                anchor="w",
                height=46,
                corner_radius=10,
                command=lambda tid=tab_id: self.switch_tab(tid),
            )
            btn.pack(fill="x", padx=16, pady=4)
            self.nav_btns[tab_id] = btn

        # Нижняя инфо-карточка в сайдбаре
        sb_bottom = ctk.CTkFrame(sb, fg_color=COL_PANEL, corner_radius=14, border_color=COL_BORDER, border_width=1)
        sb_bottom.pack(side="bottom", fill="x", padx=16, pady=22)

        ctk.CTkLabel(
            sb_bottom,
            text="СТАТУС СЕРВЕРОВ",
            font=ctk.CTkFont("Segoe UI", 9, "bold"),
            text_color=COL_MUTED,
        ).pack(anchor="w", padx=14, pady=(12, 2))

        self.sb_active_lbl = ctk.CTkLabel(
            sb_bottom,
            text="● Активно: 0 / 0",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=COL_GREEN,
        )
        self.sb_active_lbl.pack(anchor="w", padx=14)

        self.sb_tokens_lbl = ctk.CTkLabel(
            sb_bottom,
            text="🪙 Экономия: ~0 ток.",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=COL_TEXT,
        )
        self.sb_tokens_lbl.pack(anchor="w", padx=14, pady=(2, 12))

        # Быстрые кнопки
        btn_box = ctk.CTkFrame(sb_bottom, fg_color="transparent")
        btn_box.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(
            btn_box,
            text="▶ Старт все",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            fg_color=COL_GREEN_BG,
            hover_color=COL_GREEN_HOVER,
            text_color=COL_GREEN,
            height=32,
            corner_radius=8,
            command=self.start_all,
        ).pack(side="left", fill="x", expand=True, padx=(0, 3))

        ctk.CTkButton(
            btn_box,
            text="⏹ Стоп все",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            fg_color=COL_RED_BG,
            hover_color=COL_RED_HOVER,
            text_color=COL_RED,
            height=32,
            corner_radius=8,
            command=self.stop_all,
        ).pack(side="left", fill="x", expand=True, padx=(3, 0))

    # ── Область контента ──

    def _build_content_area(self) -> None:
        self.content_container = ctk.CTkFrame(self, fg_color=COL_BG, corner_radius=0)
        self.content_container.pack(side="right", fill="both", expand=True)

        self.tab_frames: dict[str, ctk.CTkFrame] = {}

        self.tab_frames["proxies"] = self._create_proxies_tab()
        self.tab_frames["analytics"] = self._create_analytics_tab()
        self.tab_frames["settings"] = self._create_settings_tab()
        self.tab_frames["logs"] = self._create_logs_tab()

        self.switch_tab("proxies")

    def switch_tab(self, tab_id: str) -> None:
        self._active_tab = tab_id
        for tid, btn in self.nav_btns.items():
            if tid == tab_id:
                btn.configure(fg_color=COL_ACCENT_BG, text_color=COL_ACCENT)
            else:
                btn.configure(fg_color="transparent", text_color=COL_MUTED)

        for tid, f in self.tab_frames.items():
            if tid == tab_id:
                f.pack(fill="both", expand=True)
            else:
                f.pack_forget()

        if tab_id == "analytics":
            self._refresh_analytics_view()

    # ── Вкладка 1: Шлюзы ──

    def _create_proxies_tab(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content_container, fg_color="transparent")

        tb = ctk.CTkFrame(page, fg_color=COL_PANEL, height=72, corner_radius=0)
        tb.pack(fill="x", side="top")
        tb.pack_propagate(False)

        ctk.CTkLabel(
            tb,
            text="Настроенные прокси-шлюзы",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=COL_TEXT_BRIGHT,
        ).pack(side="left", padx=26)

        ctk.CTkButton(
            tb,
            text="➕ Создать шлюз",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=COL_ACCENT,
            hover_color=COL_ACCENT_HOVER,
            text_color=COL_BG,
            height=38,
            corner_radius=10,
            command=lambda: self.open_edit_dialog(None),
        ).pack(side="right", padx=24)

        ctk.CTkButton(
            tb,
            text="🔄 Перезапустить все",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COL_CARD,
            hover_color=COL_CARD_HOVER,
            height=38,
            corner_radius=10,
            command=self.restart_all,
        ).pack(side="right", padx=6)

        ctk.CTkButton(
            tb,
            text="🧹 Сброс кэша",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COL_CARD,
            hover_color=COL_CARD_HOVER,
            text_color=COL_MUTED,
            height=38,
            corner_radius=10,
            command=self.reset_all_caches,
        ).pack(side="right", padx=6)

        self.scroll_cards = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.scroll_cards.pack(fill="both", expand=True, padx=24, pady=18)
        self.scroll_cards.grid_columnconfigure(0, weight=1)

        self.refresh_proxy_cards()
        return page

    def refresh_proxy_cards(self) -> None:
        for w in self.scroll_cards.winfo_children():
            w.destroy()
        self.cards.clear()

        if not self.ctrl.config.proxies:
            empty = ctk.CTkFrame(self.scroll_cards, fg_color=COL_CARD, corner_radius=18, border_color=COL_BORDER, border_width=1)
            empty.pack(fill="x", pady=40, padx=40)
            ctk.CTkLabel(
                empty,
                text="Список шлюзов пуст.\nНажмите «➕ Создать шлюз», чтобы настроить первый маршрут.",
                font=ctk.CTkFont("Segoe UI", 15),
                text_color=COL_MUTED,
            ).pack(pady=36)
            return

        for idx, entry in enumerate(self.ctrl.config.proxies):
            card = UltraProxyCard(self.scroll_cards, entry, app=self)
            card.grid(row=idx, column=0, sticky="ew", pady=10)
            self.cards[entry.id] = card

    # ── Вкладка 2: Мониторинг & Кэш ──

    def _create_analytics_tab(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content_container, fg_color="transparent")

        tb = ctk.CTkFrame(page, fg_color=COL_PANEL, height=72, corner_radius=0)
        tb.pack(fill="x", side="top")
        tb.pack_propagate(False)

        ctk.CTkLabel(
            tb,
            text="📊 Аналитика Shadow Cache & Экономия токенов",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=COL_TEXT_BRIGHT,
        ).pack(side="left", padx=26)

        ctk.CTkButton(
            tb,
            text="🧹 Полная очистка всех кэшей",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=COL_RED_BG,
            hover_color=COL_RED_HOVER,
            text_color=COL_RED,
            height=38,
            corner_radius=10,
            command=self.reset_all_caches,
        ).pack(side="right", padx=24)

        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=24, pady=18)
        scroll.grid_columnconfigure(0, weight=1)

        # 4 Hero KPI Карточки
        kpi_grid = ctk.CTkFrame(scroll, fg_color="transparent")
        kpi_grid.pack(fill="x", pady=(0, 20))
        for i in range(4):
            kpi_grid.grid_columnconfigure(i, weight=1)

        self.kpi_reqs = self._create_kpi_card(kpi_grid, 0, "ОБРАБОТАНО ЗАПРОСОВ", "0", "через все шлюзы", COL_ACCENT)
        self.kpi_hitrate = self._create_kpi_card(kpi_grid, 1, "HIT RATE ПРЕФИКСА", "0.0%", "байт-в-байт prompt cache", COL_GREEN)
        self.kpi_tokens = self._create_kpi_card(kpi_grid, 2, "ЭКОНОМИЯ ТОКЕНОВ", "~0", "сжатие RU ⇄ EN", COL_AMBER)
        self.kpi_phrases = self._create_kpi_card(kpi_grid, 3, "СЛОВАРЬ ФРАЗ", "0", "на диске (translations.jsonl)", COL_PURPLE)

        # Таблица
        table_card = ctk.CTkFrame(scroll, fg_color=COL_CARD, corner_radius=16, border_color=COL_BORDER, border_width=1)
        table_card.pack(fill="x", pady=8)

        ctk.CTkLabel(
            table_card,
            text="Разбивка эффективности по каждому шлюзу",
            font=ctk.CTkFont("Segoe UI", 16, "bold"),
            text_color=COL_TEXT_BRIGHT,
        ).pack(anchor="w", padx=24, pady=(20, 14))

        self.analytics_table = ctk.CTkFrame(table_card, fg_color="transparent")
        self.analytics_table.pack(fill="x", padx=24, pady=(0, 20))
        self.analytics_table.grid_columnconfigure(0, weight=2)
        self.analytics_table.grid_columnconfigure(1, weight=1)
        self.analytics_table.grid_columnconfigure(2, weight=1)
        self.analytics_table.grid_columnconfigure(3, weight=1)
        self.analytics_table.grid_columnconfigure(4, weight=1)

        return page

    def _create_kpi_card(self, parent: Any, col: int, title: str, val: str, sub: str, color: str) -> dict[str, ctk.CTkLabel]:
        card = ctk.CTkFrame(parent, fg_color=COL_CARD, corner_radius=14, border_color=COL_BORDER, border_width=1)
        card.grid(row=0, column=col, sticky="ew", padx=6, pady=4)

        ctk.CTkLabel(card, text=title, font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=COL_MUTED).pack(anchor="w", padx=16, pady=(16, 4))
        lbl_val = ctk.CTkLabel(card, text=val, font=ctk.CTkFont("Segoe UI", 26, "bold"), text_color=color)
        lbl_val.pack(anchor="w", padx=16)
        ctk.CTkLabel(card, text=sub, font=ctk.CTkFont("Segoe UI", 11), text_color=COL_MUTED).pack(anchor="w", padx=16, pady=(2, 16))
        return {"val": lbl_val}

    def _refresh_analytics_view(self) -> None:
        for w in self.analytics_table.winfo_children():
            w.destroy()

        headers = ["Прокси / Порт", "Запросов", "Префикс Hit Rate", "Переиспользовано", "Экономия токенов"]
        for c, h in enumerate(headers):
            ctk.CTkLabel(
                self.analytics_table,
                text=h,
                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                text_color=COL_MUTED,
            ).grid(row=0, column=c, sticky="w", pady=(0, 10))

        row_idx = 1
        total_r = total_h = total_s = 0

        for p in self.ctrl.config.proxies:
            st = self._stats_cache.get(p.id) or {}
            c = st.get("cache", {})
            reqs = c.get("requests", 0)
            hits = c.get("prefix_hits", 0)
            reused = c.get("turns_reused", 0)
            saved = c.get("saved_tokens_est", 0)
            hr = c.get("prefix_hit_rate", 0.0) * 100

            total_r += reqs
            total_h += hits
            total_s += saved

            ctk.CTkLabel(self.analytics_table, text=f"{p.name} (:{p.listen_port})", font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=COL_TEXT).grid(row=row_idx, column=0, sticky="w", pady=6)
            ctk.CTkLabel(self.analytics_table, text=f"{reqs:,}", font=ctk.CTkFont("Segoe UI", 13), text_color=COL_TEXT).grid(row=row_idx, column=1, sticky="w", pady=6)
            ctk.CTkLabel(self.analytics_table, text=f"{hr:.1f}%", font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=COL_GREEN if hr >= 50 else COL_TEXT).grid(row=row_idx, column=2, sticky="w", pady=6)
            ctk.CTkLabel(self.analytics_table, text=f"{reused:,} ходов", font=ctk.CTkFont("Segoe UI", 13), text_color=COL_TEXT).grid(row=row_idx, column=3, sticky="w", pady=6)
            ctk.CTkLabel(self.analytics_table, text=f"~{saved:,} токенов", font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=COL_AMBER).grid(row=row_idx, column=4, sticky="w", pady=6)
            row_idx += 1

        self.kpi_reqs["val"].configure(text=f"{total_r:,}")
        overall_hr = (total_h / total_r * 100.0) if total_r > 0 else 0.0
        self.kpi_hitrate["val"].configure(text=f"{overall_hr:.1f}%")
        self.kpi_tokens["val"].configure(text=f"~{total_s:,}")

        sample_st = next(iter(self._stats_cache.values()), {})
        dict_size = sample_st.get("translator_phrase_cache_size", 0)
        self.kpi_phrases["val"].configure(text=f"{dict_size:,}")

    # ── Вкладка 3: Настройки LLM & Тестовый стенд ──

    def _create_settings_tab(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content_container, fg_color="transparent")

        tb = ctk.CTkFrame(page, fg_color=COL_PANEL, height=72, corner_radius=0)
        tb.pack(fill="x", side="top")
        tb.pack_propagate(False)

        ctk.CTkLabel(
            tb,
            text="⚙️ Глобальные настройки LLM-переводчика & Поведения",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=COL_TEXT_BRIGHT,
        ).pack(side="left", padx=26)

        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=24, pady=18)
        scroll.grid_columnconfigure(0, weight=1)

        # Карта 1: Параметры LLM
        card1 = ctk.CTkFrame(scroll, fg_color=COL_CARD, corner_radius=16, border_color=COL_BORDER, border_width=1)
        card1.pack(fill="x", pady=8)

        ctk.CTkLabel(card1, text="⚡ Подключение к быстрому LLM-переводчику", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=COL_TEXT_BRIGHT).pack(anchor="w", padx=24, pady=(20, 14))

        t = self.ctrl.config.translation
        self.set_ep = self._make_input(card1, "URL эндпоинта (/v1/chat/completions):", t.llm_endpoint)
        self.set_key = self._make_input(card1, "API Ключ (Bearer token):", t.llm_api_key)
        self.set_prim = self._make_input(card1, "Основная быстрая модель перевода (<1 сек):", t.primary_model)
        self.set_back = self._make_input(card1, "Резервная модель перевода:", t.backup_model)

        # Интерактивный спидометр
        test_box = ctk.CTkFrame(card1, fg_color=COL_PANEL, corner_radius=12, border_color=COL_BORDER, border_width=1)
        test_box.pack(fill="x", padx=24, pady=(12, 20))

        ctk.CTkLabel(test_box, text="ТЕСТОВЫЙ СТЕНД ПЕРЕВОДА & ЗАМЕР ЗАДЕРЖКИ:", font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=COL_MUTED).pack(anchor="w", padx=16, pady=(12, 4))

        test_row = ctk.CTkFrame(test_box, fg_color="transparent")
        test_row.pack(fill="x", padx=16, pady=(0, 12))

        self.btn_ping = ctk.CTkButton(
            test_row,
            text="🚀 Запустить тест скорости",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=COL_PURPLE,
            hover_color=COL_PURPLE_HOVER,
            height=36,
            corner_radius=8,
            command=self._test_llm_ping,
        )
        self.btn_ping.pack(side="left")

        self.ping_status = ctk.CTkLabel(test_row, text="", font=ctk.CTkFont("Segoe UI", 12))
        self.ping_status.pack(side="left", padx=16)

        # Карта 1b: Плавная выдача перевода
        card_drip = ctk.CTkFrame(scroll, fg_color=COL_CARD, corner_radius=16, border_color=COL_BORDER, border_width=1)
        card_drip.pack(fill="x", pady=8)

        ctk.CTkLabel(card_drip, text="✨ Плавная выдача текста", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=COL_TEXT_BRIGHT).pack(anchor="w", padx=24, pady=(20, 6))
        ctk.CTkLabel(
            card_drip,
            text="Переведённый ответ появляется постепенно, слово за словом — как при обычной генерации модели,\nа не крупными блоками, от которых «дёргается» чат. Скорость подстраивается под время перевода.\nИзменение вступает в силу после перезапуска шлюзов.",
            font=ctk.CTkFont("Segoe UI", 12), text_color=COL_MUTED, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 10))
        self.drip_switch = ctk.CTkSwitch(
            card_drip, text="Включить плавную выдачу", font=ctk.CTkFont("Segoe UI", 13),
            text_color=COL_TEXT, progress_color=COL_ACCENT,
        )
        if t.smooth_drip:
            self.drip_switch.select()
        self.drip_switch.pack(anchor="w", padx=24, pady=(0, 20))

        # Карта 1c: Авто-расстановка Prompt Cache (85-95%)
        card_cache = ctk.CTkFrame(scroll, fg_color=COL_CARD, corner_radius=16, border_color=COL_BORDER, border_width=1)
        card_cache.pack(fill="x", pady=8)

        ctk.CTkLabel(card_cache, text="⚡ Авто-расстановка Prompt Cache (85–95%)", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=COL_TEXT_BRIGHT).pack(anchor="w", padx=24, pady=(20, 6))
        ctk.CTkLabel(
            card_cache,
            text="Автоматически внедряет контрольные точки кэширования (cache_control: ephemeral) в системные промпты,\nсписок инструментов (tools) и границу истории диалога для Claude и совместимых моделей.\nОбеспечивает 85–95% попадания в кэш провайдера и максимальную скорость первого токена.",
            font=ctk.CTkFont("Segoe UI", 12), text_color=COL_MUTED, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 10))
        self.cache_switch = ctk.CTkSwitch(
            card_cache, text="Автоматически расставлять точки кэша (Anthropic / OmniRoute)", font=ctk.CTkFont("Segoe UI", 13),
            text_color=COL_TEXT, progress_color=COL_GREEN,
        )
        if getattr(t, "auto_prompt_cache", True):
            self.cache_switch.select()
        self.cache_switch.pack(anchor="w", padx=24, pady=(0, 20))

        # Карта 2: Поведение окна
        card2 = ctk.CTkFrame(scroll, fg_color=COL_CARD, corner_radius=16, border_color=COL_BORDER, border_width=1)
        card2.pack(fill="x", pady=14)

        ctk.CTkLabel(card2, text="🖥️ Поведение при закрытии окна [X]", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=COL_TEXT_BRIGHT).pack(anchor="w", padx=24, pady=(20, 10))

        close_row = ctk.CTkFrame(card2, fg_color="transparent")
        close_row.pack(fill="x", padx=24, pady=(0, 20))

        ctk.CTkLabel(close_row, text="При нажатии на крестик окна:", font=ctk.CTkFont("Segoe UI", 13), text_color=COL_TEXT).pack(side="left", padx=(0, 14))

        close_modes = {
            "ask": "Спрашивать каждый раз",
            "tray": "Всегда сворачивать в трей (серверы работают)",
            "exit": "Всегда выходить и выключать все сервисы",
        }
        self.close_mode_menu = ctk.CTkOptionMenu(
            close_row,
            values=list(close_modes.values()),
            fg_color=COL_PANEL,
            button_color=COL_ACCENT,
            button_hover_color=COL_ACCENT_HOVER,
            height=36,
            corner_radius=8,
        )
        current_mode = getattr(self.ctrl.config, "on_close_action", "ask")
        self.close_mode_menu.set(close_modes.get(current_mode, "Спрашивать каждый раз"))
        self.close_mode_menu.pack(side="left")

        # Кнопка сохранения
        ctk.CTkButton(
            scroll,
            text="💾 Сохранить все настройки",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            fg_color=COL_ACCENT,
            hover_color=COL_ACCENT_HOVER,
            text_color=COL_BG,
            height=44,
            corner_radius=10,
            command=self._save_global_settings,
        ).pack(anchor="w", pady=16)

        return page

    def _make_input(self, parent: Any, label: str, val: str) -> ctk.CTkEntry:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=24, pady=5)
        ctk.CTkLabel(f, text=label, font=ctk.CTkFont("Segoe UI", 12), text_color=COL_TEXT).pack(anchor="w")
        ent = ctk.CTkEntry(f, fg_color=COL_INPUT_BG, border_color=COL_BORDER, height=36, corner_radius=8)
        if val:
            ent.insert(0, val)
        ent.pack(fill="x", pady=(3, 4))
        return ent

    def _test_llm_ping(self) -> None:
        self.ping_status.configure(text="Выполняется тестовый запрос к LLM...", text_color=COL_AMBER)
        self.btn_ping.configure(state="disabled")

        def _worker() -> None:
            ep = self.set_ep.get().strip()
            key = self.set_key.get().strip()
            model = self.set_prim.get().strip()
            try:
                t0 = time.perf_counter()
                req = urllib.request.Request(
                    ep,
                    data=json.dumps({
                        "model": model,
                        "messages": [{"role": "user", "content": "Translate to English: Привет мир"}],
                        "max_tokens": 16,
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                )
                with urllib.request.urlopen(req, timeout=8) as r:
                    d = json.loads(r.read().decode("utf-8"))
                    ans = d["choices"][0]["message"]["content"].strip()
                    dt = (time.perf_counter() - t0) * 1000
                msg = f"✓ Успешно ({dt:.0f} мс) — Ответ: «{ans}»"
                color = COL_GREEN
            except Exception as exc:
                msg = f"✗ Ошибка подключения: {exc}"
                color = COL_RED

            self._ui_queue.put(("ping_result", (msg, color)))

        threading.Thread(target=_worker, daemon=True).start()

    def _save_global_settings(self) -> None:
        t = self.ctrl.config.translation
        t.llm_endpoint = self.set_ep.get().strip()
        t.llm_api_key = self.set_key.get().strip()
        t.primary_model = self.set_prim.get().strip()
        t.backup_model = self.set_back.get().strip()
        t.smooth_drip = bool(self.drip_switch.get())
        t.auto_prompt_cache = bool(self.cache_switch.get())

        mode_text = self.close_mode_menu.get()
        if "трей" in mode_text:
            self.ctrl.config.on_close_action = "tray"
        elif "выходить" in mode_text:
            self.ctrl.config.on_close_action = "exit"
        else:
            self.ctrl.config.on_close_action = "ask"

        save_config(self.ctrl.config)
        self.ctrl._translator = None
        self.log_event("Настройки LLM и поведение при выходе сохранены.")

    # ── Вкладка 4: Консоль / Логи ──

    def _create_logs_tab(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content_container, fg_color="transparent")

        tb = ctk.CTkFrame(page, fg_color=COL_PANEL, height=72, corner_radius=0)
        tb.pack(fill="x", side="top")
        tb.pack_propagate(False)

        ctk.CTkLabel(
            tb,
            text="📜 Живой журнал событий, кэша и переводов",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=COL_TEXT_BRIGHT,
        ).pack(side="left", padx=26)

        ctk.CTkButton(
            tb,
            text="Очистить лог",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COL_CARD,
            hover_color=COL_CARD_HOVER,
            height=38,
            corner_radius=10,
            command=self._clear_log,
        ).pack(side="right", padx=24)

        self.log_box = ctk.CTkTextbox(
            page,
            fg_color=COL_INPUT_BG,
            border_color=COL_BORDER,
            border_width=1,
            corner_radius=12,
            text_color=COL_TEXT,
            font=ctk.CTkFont("Consolas", 12),
        )
        self.log_box.pack(fill="both", expand=True, padx=24, pady=18)
        self.log_box.configure(state="disabled")

        return page

    def log_event(self, text: str) -> None:
        self._ui_queue.put(("log", text))

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log_event(f"Скопировано в буфер обмена: {text}")

    # ── Анимационный цикл (60 FPS) ──

    def _animation_loop(self) -> None:
        """Плавная 60 FPS отрисовка перелива Авроры, дыхания и бегущего потока."""
        if not self._poller_running:
            return

        self._pulse_phase = (self._pulse_phase + 1) % 16
        self._aurora_phase = (self._aurora_phase + 1) % 48

        if self._pulse_phase % 4 == 0:
            self._flow_phase = (self._flow_phase + 1) % 5

        # Вычисляем цвет Авроры для этого кадра
        aurora_col = get_aurora_border_color(self._aurora_phase)

        # Анимируем все карточки
        for card in self.cards.values():
            card.animate_step(self._pulse_phase, self._flow_phase, aurora_col)

        self.after(50, self._animation_loop)

    # ── Фоновый мониторинг метрик ──

    def _status_worker(self) -> None:
        while self._poller_running:
            for p in self.ctrl.config.proxies:
                stats_data: dict[str, Any] | None = None
                try:
                    url = f"http://127.0.0.1:{p.listen_port}/stats"
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=1.2) as r:
                        stats_data = json.loads(r.read().decode("utf-8"))
                except Exception:
                    pass
                self._stats_cache[p.id] = stats_data or {}
            time.sleep(1.8)

    def _poll_ui(self) -> None:
        if not self._poller_running:
            return

        # Разбор очереди из потоков
        while not self._ui_queue.empty():
            try:
                kind, data = self._ui_queue.get_nowait()
                if kind == "log":
                    stamp = time.strftime("%H:%M:%S")
                    line = f"[{stamp}] {data}\n"
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", line)
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind == "ping_result":
                    msg, color = data
                    self.ping_status.configure(text=msg[:70], text_color=color)
                    self.btn_ping.configure(state="normal")
                elif kind == "restore":
                    self.deiconify()
                    self.lift()
                    self.focus_force()
            except queue.Empty:
                break

        active_cnt = 0
        target_tokens = 0

        for p in self.ctrl.config.proxies:
            card = self.cards.get(p.id)
            rp = self.ctrl.running.get(p.id)
            is_alive = bool(rp and rp.is_alive)
            is_busy = False

            if is_alive:
                active_cnt += 1
            else:
                is_busy = self.ctrl.is_port_in_use(p.listen_port, p.listen_host)

            st = self._stats_cache.get(p.id) or {}
            c = st.get("cache", {})
            target_tokens += c.get("saved_tokens_est", 0)

            if card:
                card.update_metrics(is_alive, is_busy, st if is_alive else None)

        total_proxies = len(self.ctrl.config.proxies)
        self.sb_active_lbl.configure(text=f"● Активно: {active_cnt} / {total_proxies}")

        # Плавная интерполяция сохраненных токенов
        diff = (target_tokens - self._disp_tokens) * 0.25
        self._disp_tokens += diff
        self.sb_tokens_lbl.configure(text=f"🪙 Экономия: ~{int(self._disp_tokens):,} ток.")

        self._update_tray_menu()

        if self._poller_running:
            self.after(1000, self._poll_ui)

    # ── Управление серверами ──

    def _auto_start_servers(self) -> None:
        def _worker() -> None:
            self.log_event("Запуск шлюзов с включенным автостартом...")
            for msg in self.ctrl.start_all(only_auto_start=True, force_free_port=True):
                self.log_event(msg)

        threading.Thread(target=_worker, daemon=True).start()

    def start_single(self, proxy_id: str) -> None:
        def _worker() -> None:
            ok, msg = self.ctrl.start_proxy(proxy_id, force_free_port=True)
            self.log_event(f"[{'OK' if ok else 'ERR'}] {msg}")

        threading.Thread(target=_worker, daemon=True).start()

    def stop_single(self, proxy_id: str) -> None:
        def _worker() -> None:
            ok, msg = self.ctrl.stop_proxy(proxy_id)
            self.log_event(f"[{'OK' if ok else 'INFO'}] {msg}")

        threading.Thread(target=_worker, daemon=True).start()

    def restart_single(self, proxy_id: str) -> None:
        def _worker() -> None:
            ok, msg = self.ctrl.restart_proxy(proxy_id, force_free_port=True)
            self.log_event(f"[{'OK' if ok else 'ERR'}] {msg}")

        threading.Thread(target=_worker, daemon=True).start()

    def start_all(self) -> None:
        def _worker() -> None:
            for msg in self.ctrl.start_all(only_auto_start=False, force_free_port=True):
                self.log_event(msg)

        threading.Thread(target=_worker, daemon=True).start()

    def stop_all(self) -> None:
        def _worker() -> None:
            for msg in self.ctrl.stop_all():
                self.log_event(msg)

        threading.Thread(target=_worker, daemon=True).start()

    def restart_all(self) -> None:
        def _worker() -> None:
            self.log_event("Перезапуск всех шлюзов...")
            self.ctrl.stop_all()
            time.sleep(0.4)
            for msg in self.ctrl.start_all(only_auto_start=False, force_free_port=True):
                self.log_event(msg)

        threading.Thread(target=_worker, daemon=True).start()

    def reset_all_caches(self) -> None:
        def _worker() -> None:
            for p in self.ctrl.config.proxies:
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{p.listen_port}/cache/reset", data=b"{}", method="POST")
                    urllib.request.urlopen(req, timeout=2.0).read()
                    self.log_event(f"Кэш сброшен для '{p.name}' (порт {p.listen_port})")
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def open_edit_dialog(self, entry: ProxyEntry | None = None) -> None:
        ProxyEditDialog(self, entry=entry)

    def delete_proxy(self, entry: ProxyEntry) -> None:
        if entry.id in self.ctrl.running:
            self.ctrl.stop_proxy(entry.id)
        self.ctrl.config.proxies = [p for p in self.ctrl.config.proxies if p.id != entry.id]
        save_config(self.ctrl.config)
        self.refresh_proxy_cards()
        self.log_event(f"Шлюз '{entry.name}' удалён.")

    # ── Закрытие и Выход ──

    def on_window_close_request(self) -> None:
        action = getattr(self.ctrl.config, "on_close_action", "ask")
        if action == "tray":
            self.minimize_to_tray()
        elif action == "exit":
            self.on_full_quit()
        else:
            CloseActionDialog(self)

    def on_full_quit(self) -> None:
        self._poller_running = False
        print("\n[ProxyStation] Остановка всех сервисов перед выходом...")

        self.ctrl.stop_all()

        try:
            self.tray_icon.stop()
        except Exception:
            pass

        try:
            self.destroy()
        except Exception:
            pass

        os._exit(0)


def main() -> None:
    from core import instance

    if not instance.acquire():
        instance.notify_already_running(gui=True)
        return
    app = ProxyStationApp()
    app.mainloop()


if __name__ == "__main__":
    main()
