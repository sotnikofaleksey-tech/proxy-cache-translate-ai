# Proxy Station

**English** · [Русский](#русский)

> A portable Windows gateway that sits between your AI client and any OpenAI/Anthropic-compatible provider. It translates your conversation on the fly and keeps a "shadow cache" of the English side, so you can chat with any model in your own language **without losing quality or prompt-cache hits**.

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-blue) ![python](https://img.shields.io/badge/python-3.11%20(installed%20by%20setup)-green)

## 🤔 Why

Most models reason best in English, and provider-side prompt caching only works when the prompt prefix is *byte-identical* between turns. Naive translation breaks both: re-translating history changes the wording every turn, which destroys cache hits and slowly degrades the dialogue.

Proxy Station fixes this:

- **Shadow Cache** – remembers every turn as a pair *(your language ⇄ English)*. On the next turn the model receives the exact same English prefix as before, so provider prompt caching can hit and the dialogue stays consistent.
- **Two-way translation** – your messages are translated to English before they reach the model; the model's answer (including reasoning / thinking) is translated back into your language.
- **Code & tag shield** – code blocks, inline code, file paths, URLs and system XML blocks are never translated and are preserved byte-for-byte.
- **Smooth Drip** – the translated answer appears word by word, like real model generation, instead of arriving in big jumps that make the chat jitter. The pace adapts to the translation time of the previous block.

## ✨ Features

- Any number of gateways on different ports, each with its own upstream URL, API key, model aliases, target language and on/off translation switch (cache-only mode is supported).
- Target language selectable per gateway from 17 presets (Russian, English, Chinese, Japanese, Korean, German, French, Spanish, Portuguese, Italian, Turkish, Arabic, Hindi, Ukrainian, Polish, Czech, Dutch) or any custom language name.
- Both protocols on every port: **OpenAI** `POST /v1/chat/completions` and **Anthropic** `POST /v1/messages`, streaming and non-streaming, with `reasoning_content` / `thinking` support.
- Modern GUI (dark theme, animated toggles, live token-savings counters, system tray, start/stop/restart per gateway) plus a console menu and a headless mode.
- Translation never blocks the server: heavy work runs in worker threads, so one slow translation can't freeze other requests.

## 📊 Real-world numbers

Measured on a real working session (Claude via an Anthropic-compatible gateway, Russian ⇄ English, one gateway, one long coding conversation):

| Metric | Value |
|---|---|
| Requests handled | **21** |
| Session prefix cache hits | **20 of 21 → 95.2 %** (1 miss = the first request) |
| Conversation turns reused from cache instead of re-translated | **191** (145 new turns recorded) |
| Message fallback cache | 29 entries, **11.6 %** hit rate (20 hits / 172 lookups) – restores English originals after context compaction or a rolled-back turn |
| Characters not re-sent | **17,818** |
| Estimated tokens saved by sending English instead of Russian | **≈ 5,400** |
| Phrase dictionary persisted on disk | **181** translated phrases (reused across restarts) |

> How to read this: the *prefix hit rate* is the gateway's own session cache – it shows how often the model received the identical English prefix as the previous turn (the precondition for provider-side prompt caching). It is **not** your provider's billing cache figure. *Tokens saved* is an estimate (≈1.8 chars/token for Russian vs ≈4.0 for English). This is one session, not a benchmark – your numbers will differ. Live numbers for your own setup are in the GUI dashboard and at `GET /stats`.

## 🚀 Quick start

1. Download or clone this repository.
2. Run **`setup.bat`** once – it downloads a private copy of Python 3.11 (with tkinter) into `python\` and installs the dependencies. Needs internet (~60 MB); no admin rights, nothing is installed system-wide.
3. Run **`proxy.bat`** – the GUI opens (no console window).
4. In **Settings**, point the translator to a fast OpenAI-compatible LLM endpoint and pick the models.
5. Create a gateway (upstream URL, port, language), then point your AI client at `http://127.0.0.1:<port>`.

| File | Purpose |
|---|---|
| `proxy.bat` | Start the GUI (`proxy.bat --cli` for the console menu) |
| `proxy_cli.bat` | Console menu |
| `start_headless.bat` | Run all gateways without a UI |
| `stop_all.bat` | Stop Proxy Station (only processes started from this folder – other programs are never touched) |
| `setup.bat` | Install / repair the bundled Python |

## 🔒 Where your data lives

The program folder contains **no settings, keys or caches**. Everything is stored per user in:

```
%LOCALAPPDATA%\ProxyStation\
├── config\proxies.json     # gateways, translator settings (API keys, plain text!)
├── cache\                  # shadow cache and phrase dictionary (*.jsonl)
└── logs\proxystation.log   # diagnostics (the GUI runs without a console window)
```

Keep that folder private. The repository itself never contains your configuration.

## 🔌 Service endpoints (every gateway)

`GET /health` · `GET /stats` · `POST /cache/reset` · `GET /v1/models` (provider models + your aliases) · `POST /v1/chat/completions` · `POST /v1/messages`

Send header `x-shadow-bypass: true` to skip translation for a single request.

## 🛠️ Troubleshooting

- **Nothing happens / window closes** – open `%LOCALAPPDATA%\ProxyStation\logs\proxystation.log`; if you never ran `setup.bat`, `proxy.bat` runs it automatically.
- **"Proxy Station is already running"** – only one instance (GUI, console or headless) may run at a time. Look for the tray icon, or run `stop_all.bat`.
- **"Port N is occupied by another program"** – Proxy Station never kills foreign programs. Close that program or change the gateway's port. (Leftover Proxy Station processes of this folder are cleaned up automatically.)

## ⚠️ Current limitations

- Detection of *your* messages that need translating to English is currently reliable for **Cyrillic** input. Answers can be translated into any target language; broader source-language detection is planned.
- Translation quality and speed depend on the LLM you configure for it.
- Windows only for now (the GUI and `.bat` launchers).

## 🗂️ Project layout

```
core/
├── gui.py           # GUI application
├── manager.py       # gateway controller + console menu
├── engine.py        # FastAPI engine (OpenAI + Anthropic, streaming, Smooth Drip)
├── shadow_cache.py  # session prefix cache + message fallback cache
├── translator.py    # LLM translation with code/tag protection
└── config.py        # settings and data paths
```

---

## Русский

> Портативный шлюз для Windows между вашим ИИ-клиентом и любым провайдером с OpenAI/Anthropic-совместимым API. Он переводит диалог «на лету» и ведёт «теневой кэш» английской стороны, чтобы вы общались с любой моделью на своём языке **без потери качества и без потери попаданий в prompt-кэш**.

### 🤔 Зачем

Большинство моделей лучше всего рассуждают на английском, а prompt-кэш провайдера работает только если начало запроса **байт-в-байт одинаково** между ходами. Наивный перевод ломает и то и другое: перевод истории каждый раз звучит чуть иначе — кэш не попадает, а диалог постепенно деградирует.

Proxy Station решает это:

- **Теневой кэш** – запоминает каждый ход как пару *(ваш язык ⇄ English)*. На следующем ходу модель получает ровно тот же английский префикс, что и раньше: prompt-кэш провайдера срабатывает, диалог остаётся цельным.
- **Двусторонний перевод** – ваши сообщения переводятся на английский перед отправкой модели, ответ модели (включая reasoning / thinking) переводится обратно.
- **Защита кода и тегов** – блоки кода, inline-код, пути к файлам, URL и системные XML-блоки не переводятся и сохраняются байт-в-байт.
- **Плавная выдача (Smooth Drip)** – переведённый ответ появляется слово за словом, как при настоящей генерации, а не большими скачками, от которых «дёргается» чат. Скорость подстраивается под время перевода предыдущего блока.

### ✨ Возможности

- Любое число шлюзов на разных портах: у каждого свой апстрим, API-ключ, алиасы моделей, язык и переключатель перевода (есть режим «только кэш»).
- Язык выбирается для каждого шлюза: 17 готовых вариантов (русский, английский, китайский, японский, корейский, немецкий, французский, испанский, португальский, итальянский, турецкий, арабский, хинди, украинский, польский, чешский, нидерландский) или любое своё название.
- Оба протокола на каждом порту: **OpenAI** `POST /v1/chat/completions` и **Anthropic** `POST /v1/messages`, со стримингом и без, с поддержкой `reasoning_content` / `thinking`.
- Современный интерфейс (тёмная тема, анимированные переключатели, живые счётчики экономии токенов, системный трей, старт/стоп/рестарт каждого шлюза), а также консольное меню и безынтерфейсный режим.
- Перевод не блокирует сервер: тяжёлая работа идёт в потоках, поэтому один медленный перевод не «замораживает» остальные запросы.

### 📊 Реальные цифры

Измерено на живой рабочей сессии (Claude через Anthropic-совместимый шлюз, русский ⇄ английский, один шлюз, один длинный диалог по коду):

| Метрика | Значение |
|---|---|
| Обработано запросов | **21** |
| Попаданий в сессионный префикс-кэш | **20 из 21 → 95,2 %** (1 промах – самый первый запрос) |
| Ходов диалога взято из кэша вместо повторного перевода | **191** (записано 145 новых) |
| Резервный кэш сообщений | 29 записей, **11,6 %** попаданий (20 из 172 обращений) – восстанавливает английские оригиналы после сжатия контекста или отката хода |
| Символов не отправлено повторно | **17 818** |
| Оценка сэкономленных токенов (английский вместо русского) | **≈ 5 400** |
| Словарь фраз на диске | **181** переведённая фраза (переживает перезапуск) |

> Как читать: *процент попаданий в префикс* – это собственный сессионный кэш шлюза: как часто модель получала тот же английский префикс, что и на прошлом ходу (необходимое условие для prompt-кэша провайдера). Это **не** цифра кэша из биллинга вашего провайдера. *Сэкономленные токены* – оценка (≈1,8 симв./токен для русского против ≈4,0 для английского). Это одна сессия, а не бенчмарк – у вас цифры будут другие. Актуальные цифры вашей установки – на дашборде GUI и в `GET /stats`.

### 🚀 Быстрый старт

1. Скачайте или клонируйте репозиторий.
2. Один раз запустите **`setup.bat`** – он скачает собственную копию Python 3.11 (с tkinter) в папку `python\` и поставит зависимости. Нужен интернет (~60 МБ); права администратора не нужны, в систему ничего не устанавливается.
3. Запустите **`proxy.bat`** – откроется окно программы (без консоли).
4. В **Настройках** укажите быстрый OpenAI-совместимый LLM-эндпоинт для перевода и модели.
5. Создайте шлюз (URL апстрима, порт, язык) и направьте ваш ИИ-клиент на `http://127.0.0.1:<порт>`.

| Файл | Назначение |
|---|---|
| `proxy.bat` | Запуск GUI (`proxy.bat --cli` – консольное меню) |
| `proxy_cli.bat` | Консольное меню |
| `start_headless.bat` | Все шлюзы без интерфейса |
| `stop_all.bat` | Остановить Proxy Station (только процессы из этой папки – чужие программы не трогает) |
| `setup.bat` | Установка / восстановление встроенного Python |

### 🔒 Где хранятся ваши данные

В папке программы **нет настроек, ключей и кэшей**. Всё хранится отдельно для каждого пользователя:

```
%LOCALAPPDATA%\ProxyStation\
├── config\proxies.json     # шлюзы и настройки переводчика (API-ключи, открытым текстом!)
├── cache\                  # теневой кэш и словарь фраз (*.jsonl)
└── logs\proxystation.log   # диагностика (GUI работает без окна консоли)
```

Держите эту папку в секрете. В репозиторий ваша конфигурация не попадает.

### 🔌 Сервисные эндпоинты (каждого шлюза)

`GET /health` · `GET /stats` · `POST /cache/reset` · `GET /v1/models` (модели провайдера + ваши алиасы) · `POST /v1/chat/completions` · `POST /v1/messages`

Заголовок `x-shadow-bypass: true` отключает перевод для одного запроса.

### 🛠️ Если что-то не работает

- **Ничего не происходит / окно закрывается** – откройте `%LOCALAPPDATA%\ProxyStation\logs\proxystation.log`; если `setup.bat` не запускался, `proxy.bat` выполнит его сам.
- **«Proxy Station уже запущен»** – запущен может быть только один экземпляр (GUI, консоль или headless). Найдите значок в трее или запустите `stop_all.bat`.
- **«Порт N занят другой программой»** – Proxy Station никогда не убивает чужие программы. Закройте её или смените порт шлюза. (Остатки самого Proxy Station из этой папки очищаются автоматически.)

### ⚠️ Текущие ограничения

- Автоопределение *ваших* сообщений для перевода на английский сейчас надёжно работает для **кириллицы**. Ответы можно переводить на любой язык; более широкое определение исходного языка запланировано.
- Качество и скорость перевода зависят от LLM, которую вы настроите для него.
- Пока только Windows (GUI и `.bat`-запускатели).
