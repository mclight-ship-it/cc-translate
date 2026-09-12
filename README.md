# CC Translate

[English](README.md) | [简体中文](README.zh.md)

> ⚠️ **Required before use:** CC Translate needs at least one working model CLI: the official Codex CLI (ChatGPT sign-in, API key, or compatible custom provider), or Claude Code (subscription or compatible local proxy). OpenAI GPT smart routing is the default.

A select-and-translate app focused on **high-quality translation**: **double-tap Ctrl+C** to translate the currently selected text, shown in a popup near the cursor. It combines an offline local dictionary with parallel Claude Code and OpenAI GPT (through the official Codex CLI) providers. ChatGPT or Claude subscription sign-in needs no separate API key; API-key and compatible custom-provider configurations are also supported.

## Screenshots

<p align="center">
  <img src="docs/screenshots/popup-translate.png" alt="Selected text with CC Translate popup" width="760"><br>
  <sub><b>Select text, then double-tap Ctrl+C</b> — the translation appears next to the selection</sub>
</p>

<table>
<tr>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/popup-code.png" alt="Code-explanation mode" width="360"><br>
  <sub><b>Code-explanation mode</b>: code isn't force-translated — it's explained in plain language</sub>
</td>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/popup-dict.png" alt="Instant local dictionary result with AI supplement" width="420"><br>
  <sub><b>Dictionary mode</b>: source-grounded local results appear instantly, followed by an optional AI supplement</sub>
</td>
</tr>
<tr>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/popup-summary.png" alt="Long-text summary" width="420"><br>
  <sub><b>Long-text summary (Beta)</b>: long text leads with a key-point summary, then the full translation</sub>
</td>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/quick-input.png" alt="Quick input translation" width="420"><br>
  <sub><b>Quick input translation</b>: with nothing selected, double-tap Ctrl+C to type text in an input box</sub>
</td>
</tr>
<tr>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/screenshot-ocr.png" alt="Screenshot translation region select" width="420"><br>
  <sub><b>Screenshot translation</b>: press <code>Win+Shift+C</code> to drag-select any screen region and translate the text in it (vision model or offline local OCR)</sub>
</td>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/history.png" alt="Translation history" width="420"><br>
  <sub><b>History</b>: opened from the tray — list on the left, source &amp; result on the right</sub>
</td>
</tr>
</table>

## Features

- **Double-tap Ctrl+C** to translate the clipboard/selected text, shown in a popup near the mouse
- **Claude / OpenAI GPT switching**: choose a model service in Settings. Claude keeps its existing warm pool and streaming path; GPT follows your local Codex CLI configuration and authentication.
- **Screenshot translation**: press `Win+Shift+C` to drag-select any screen region and translate the text in it; choose between the vision model or an offline local OCR engine
- **Quick input translation**: with nothing selected, double-tap Ctrl+C to open an input box and type the text you want translated
- **Code-explanation mode**: local classification distinguishes code, mixed content, and prose before any model call—covering Python, JSON, YAML, and common config structures without an extra AI request. Code is explained instead of force-translated; mixed prose + code is translated while code stays verbatim, and ordinary forms, dates, paths, or sentences containing `foo()` remain text.
- **Locally accelerated dictionary mode**: optional one-click download in Settings. Once installed and enabled, short English/Chinese terms first query the per-user read-only SQLite database; high-confidence exact, source-provided or reviewed build-time inflection, simplified/traditional, and Unihan character matches appear immediately with a compact lightning badge beside the headword, while disabled/missing/weak/broken-database cases seamlessly use the existing AI dictionary path. No risky runtime stemming is used. Numbered source Pinyin is rendered with standard tone marks, polyphonic Chinese senses are grouped by pronunciation, clearly specialized/verbose senses are placed later, and long entries initially show five senses with an in-place **Show more** control. After an instant local result, an AI supplement adds only missing information in the background without delaying the first paint; cached supplements appear directly, pending state stays subtle, and failures quietly retain the complete local result. Supplements use a separate bounded local cache and never create visible history entries. **Query again with AI** still performs the existing full replacement query. Every local result exposes its source and license details. Local fields and attribution remain source-grounded—no examples, pronunciation, or parts of speech are invented.
- **Long-text summary (Beta)**: on by default; longer natural-language text leads with a short summary before the full translation, and can be turned off in Labs
- **Paste as plain text (Beta)**: optionally reserve `Ctrl+Shift+K` to remove clipboard formatting and paste the text immediately; image- and file-only clipboards are left untouched
- **Rich-text rendering**: the result popup supports lightweight Markdown and colorizes code like a code editor; copied text stays plain
- **Multiple target languages**: auto-detect CN↔EN, or fix the target to Chinese/English/Japanese/Korean/French/German/Spanish
- **Re-translate/switch direction in the popup**: a "Re-translate" menu re-translates the selection into another language in one click
- **Rewrite & distill**: from the popup, rewrite the translation in a casual / formal / professional tone, or distill it to key points
- **Long-text streaming**: Claude progressively reveals long translations. Codex app-server streaming is always on; it preserves source lists and emits summary points as Markdown bullets, preflights executable hooks before starting a model turn, and safely falls back to stable `codex exec` before output when necessary.
- **Provider-aware diagnostics**: the Diagnostics window shows Codex version/sign-in, streaming compatibility and trigger rules, the latest request route, and a seven-day app-run summary with outcomes, models, routes, and P50/P95. Its advisory rollout gate tracks the 7-day / 200-request target and streamed-first-text versus stable-long-text P95 without changing saved settings.
- **Smart selection detection**: automatically detects whether text is actually selected, so it won't mistranslate the whole field when nothing is selected in an input box (including cross-process apps like VS Code)
- **Translation history**: open the history window from the tray — searchable and filterable by type
- **Popup layout**: classic (screen-centered) or dynamic (follows the mouse), switchable in settings
- **Themes**: follow system / light / dark
- **System tray**: left-click runs a configurable action (default settings; also history / screenshot / quick translate); right-click for quick translate / screenshot translate / history / check for updates / pause / quit
- **Self-update**: the app itself is a `git clone` deployment, so it can check GitHub and update — via a manual "Check for updates" or a nightly auto-update
- Optional launch on startup

## Offline dictionary data and licenses

The app never downloads dictionary data at startup. Use **Settings → Offline
dictionary acceleration (recommended) → Download and enable** to explicitly
download the pinned ~65 MB release asset. It is verified by exact size, SHA-256,
SQLite schema, and data version before an atomic install under
`%APPDATA%\CC Translate\dictionary\cc_dictionary.sqlite3`. Settings can disable
the fast path without deleting the data, or **Delete local data** after
confirmation. Either action leaves AI dictionary mode available.
Diagnostics also reports session-only aggregate hit rate, P50/P95 lookup latency,
and AI fallback reasons; query text is never included in these metrics.

The optional artifact is built from pinned inputs: WikDict eng-zho 2025.11.21
(CC BY-SA 3.0), the immutable 2017-04-28 CC-CEDICT snapshot whose own header
specifies CC BY-SA 3.0, Chinese Open Wordnet / Princeton WordNet via OMW 2.0
(their respective WordNet licenses), and Unihan 17.0.0 (Unicode License v3).
Application-code and dictionary-data licenses are separate. Full attribution,
source URLs, SHA-256 hashes, modifications, artifact hash, and original license
texts are available from **About → Data licenses**, each local result's themed
**Sources & licenses** card, [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES), and
[`data/dictionary/licenses/`](data/dictionary/licenses/).

Developers can reproduce the database with the standard-library-only builder:

```powershell
python tools\build_dictionary.py --cache <source-cache> --download
```

The explicit `--download` flag is builder-only; normal app lookup is always
offline after the user's explicit one-time download. Every input hash is verified
and every entry/sense retains its own source ID and provenance. The reproducible
release-staging copy is `data/dictionary/cc_dictionary.sqlite3`; publish it under
the immutable release tag and asset name declared in
`cc_dictionary_artifact.py` before shipping application code that references it.

## Requirements

- Windows (uses Windows APIs for DPI awareness, multi-monitor positioning, and reading the theme from the registry)
- Python 3.12+
- Node.js (used to install the Claude Code and Codex CLIs)
- At least one provider:
  - Claude Code: a signed-in Claude subscription (Pro/Max), or a compatible local proxy endpoint (for example, Agent Maestro)
  - OpenAI GPT: the official Codex CLI with ChatGPT sign-in, API-key auth, or a working compatible custom provider
- ⚠️ **Upgrade the Claude Code CLI to the latest version first** — an outdated CLI has incompatible arguments that cause translation errors or garbled output. This is the most common install pitfall, so always update to the latest before installing.

## Quick install (recommended)

Run this one line in **PowerShell**. The script installs git / Python / Node as
needed, clones the repo, installs the Claude CLI, the compatible Codex CLI, and
Python dependencies, then launches the app:

```powershell
irm https://raw.githubusercontent.com/mclight-ship-it/cc-translate/master/install.ps1 | iex
```

It automates installation, **not account authorization**. OpenAI GPT is the
default. For ChatGPT subscription access, sign in to the official Codex CLI with
the browser authorization flow below. Existing API-key or custom-provider users
can keep their working Codex configuration instead:

```powershell
codex login
codex login status
```

**Native Codex configuration:** exec, streaming, prewarm and diagnostics all use
the same native Codex config/auth home (`CODEX_HOME`, or `~/.codex` by default).
Configure ChatGPT sign-in, API-key auth, or a compatible custom provider in Codex;
CC Translate does not implement authentication or copy credentials. Claude remains
available as an alternate provider in **Settings**.

The optional compatibility override below selects a separate Codex home for
CC Translate only. It is **not required** for custom providers:

```powershell
setx CC_TRANSLATE_CODEX_HOME "$env:APPDATA\CC Translate\codex-provider"
```

Restart after changing environment or routing settings. An explicit override must
contain `config.toml`; invalid configuration fails visibly instead of silently
switching homes/accounts. Removing this override returns to native `CODEX_HOME`.

Translation-only restrictions apply **only to child processes**: no personal
instructions, skill prompts, memory, notification commands, plugins or user hooks.
Codex's native config reader discovers merged MCP entries, each of which is
explicitly disabled (an empty MCP table alone does not clear them). Ephemeral
sessions, read-only sandboxing, hook preflight and fail-closed tool-event checks
remain in place; global Codex files are never rewritten. Required managed policy
is not bypassed. Diagnostics name the selected backend; custom auth is shown as
**unverified**, not as cached ChatGPT login or proof of endpoint/model access.
Diagnostics never execute a custom credential helper or submit a model request.

**Managed local model catalog:** For an ordinary custom-provider configuration
(including global project entries containing only `trust_level`),
CC Translate exports the effective model metadata from each user's installed
Codex into `%APPDATA%\CC Translate\codex-catalogs`. No shared model list,
credentials, provider switch, model switch, or reasoning changes are installed.
This avoids repeated incompatible `/models` discovery on custom endpoints.
The catalog describes capabilities, not account permissions or model availability.

Snapshots are checked before starting an `exec` or streaming process, including
prewarm. They are keyed by CLI executable, Codex home, configuration and native
model cache; snapshots older than 24 hours are refreshed at the next process
start, not during an active translation. Codex validates each new snapshot and
revalidates it on first use in a new app instance. Writes are atomic. Missing,
corrupt or expired snapshots are rebuilt; export/validation failure leaves off
the managed override and records a metadata-only `codex_catalog` warning in
`%APPDATA%\CC Translate\error.log`. No submitted translation is resent.
First-time generation can add startup time; prewarm normally does this work.

Currently validated with Codex **0.146.0**. Unvalidated CLI versions retain
native discovery rather than using an old snapshot. Official OpenAI configs,
layered provider/model/catalog settings and user-specified `model_catalog_json` retain
their native behavior; CC Translate does not repair or override user-owned
catalogs. A requested model missing from the exported catalog also retains native
discovery, never silently substitutes another model. Set the user environment
variable `CC_TRANSLATE_CODEX_CATALOG=off` and restart to disable management.
Model retirement or missing account permissions can still fail independently
of the catalog.

For GPT, **Smart routing (fast)** is the default and streams text incrementally.
**Auto select (quality)** remains available when translation quality matters
more than latency. Model availability depends on your ChatGPT plan,
organization policy, and Codex CLI version.

> Optional environment variables (set before running): `$env:CC_TRANSLATE_DIR`
> to choose the install location (default `%USERPROFILE%\cc-translate`);
> `$env:CC_TRANSLATE_DRYRUN="1"` to do a dry run that only prints each step and
> changes nothing.

> If running `claude` manually fails with **"running scripts is disabled on this
> system"**, PowerShell's default `Restricted` execution policy is blocking npm's
> `.ps1` shims. The installer automatically raises the current-user policy to
> `RemoteSigned` to fix this; if you still hit it, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (answer Y), or log in with
> `claude.cmd` instead. This does not affect the app's translation (it calls
> `claude.cmd` via subprocess, unaffected by the policy), but it blocks the manual
> login — and without logging in, translation can't work.

Prefer to install step by step? See [Install (manual steps)](#install-manual-steps) below.

## Install (manual steps)

```bash
# 1. Get the project code
git clone https://github.com/mclight-ship-it/cc-translate.git
cd cc-translate

# 2. Install Node.js and Python (skip if already installed)
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12

# 3. Install/upgrade the Claude Code CLI and sign in
#    (browser OAuth, uses your subscription, no extra charge)
#    ⚠️ Even if you installed it before, run this to upgrade to the latest —
#       an outdated version causes translation failures or garbled output
npm install -g @anthropic-ai/claude-code@latest
claude --version   # confirm it's the latest; if clearly old, re-run the line above to force an update
claude   # on first run, follow the prompt to sign in via browser, then Ctrl+C to exit interactive mode

# Optional: install the GPT provider and sign in with ChatGPT
npm install -g @openai/codex@0.146.0
codex login
codex login status

# 4. Install Python dependencies
pip install pynput pyperclip pystray Pillow
# Optional enhancements (each feature auto-degrades/turns off if missing; core translation is unaffected):
pip install Pygments   # code-block syntax highlighting (falls back to monochrome code style when missing)
pip install winsdk     # offline local OCR engine (the vision model still works without it)
pip install comtypes   # smart selection detection, avoids mistranslating a whole input box when nothing is selected (incl. cross-process apps like VS Code)
# Or install everything at once (equivalent to all packages above): pip install -r requirements.txt

# 5. First run (make sure the current directory is the project root, cc-translate)
python -c "import cc_update,subprocess; subprocess.Popen([cc_update.ensure_branded_launcher() or cc_update.PYTHONW, cc_update.SCRIPT_PATH], cwd=cc_update.APP_DIR)"
```

> ⚠️ **Make sure the Claude Code CLI is up to date**: this tool relies on newer `claude -p`
> command-line arguments, and an old version causes translation errors or garbled output.
> **Even if you already had `claude` installed, run `npm install -g @anthropic-ai/claude-code@latest`
> again before installing this tool**, and confirm with `claude --version`.

> Note: `translator.pyw` auto-detects both CLIs, including their npm global
> installation directories. If one cannot be found, confirm its `.cmd` launcher
> is on PATH. The app invokes Codex with an ephemeral, read-only working
> directory and fails closed if Codex reports a tool event.

## Launching

The first run creates a small local branded launcher plus a **CC Translate**
icon in the Start Menu. The app still runs directly from this source checkout,
but Windows Task Manager shows **CC Translate** instead of the generic
**Python** process name. Afterwards, launch it straight from the Start Menu.

## Launch on startup (optional)

Check "Launch on startup" in the app's **Settings** (this creates a shortcut in the Startup folder).

## One-shot install instructions for AI assistants

See [INSTALL_FOR_LLM.md](docs/INSTALL_FOR_LLM.md): hand that file's contents to a Claude/AI assistant on a new machine and it will install the dependencies, sign in, install the libraries, and launch the app step by step.

## Development / testing

- Run the tests: `python -m unittest discover -s tests` (standard library, no extra dependencies).
- The repo ships a pre-push hook that checks newly added content for local user paths,
  credentials, and sensitive local-data files, then runs change-scoped tests. Any failure
  blocks the push.
- **Enable it once after a fresh clone**: `git config core.hooksPath .githooks`.
