# CC Translate

[简体中文](README.md) | [English](README.en.md)

> ⚠️ **Required before use:** CC Translate needs working Claude access — either a signed-in Claude subscription (Pro/Max) or a compatible local proxy endpoint (for example, Agent Maestro). Without either one, the app cannot translate and will not function.

An **LLM-powered** local select-and-translate app focused on **high-quality translation**: **double-tap Ctrl+C** to translate the currently selected text, shown in a popup near the cursor. Built on the Claude Code CLI, it reuses your existing Claude capability and needs no separate API key.

## Screenshots

<p align="center">
  <img src="docs/screenshots/popup-translate.png" alt="Translation popup" width="520"><br>
  <sub><b>Double-tap Ctrl+C</b> — select text and the translation pops up next to your cursor</sub>
</p>

<table>
<tr>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/popup-dict.png" alt="Dictionary mode" width="360"><br>
  <sub><b>Dictionary mode</b>: a single word returns phonetics / part of speech / definitions / examples</sub>
</td>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/popup-code.png" alt="Code-explanation mode" width="360"><br>
  <sub><b>Code-explanation mode</b>: code isn't force-translated — it's explained in plain language</sub>
</td>
</tr>
<tr>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/settings.png" alt="Settings window" width="420"><br>
  <sub><b>Settings</b>: a two-column layout — model / direction / theme / OCR / updates at a glance</sub>
</td>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/history.png" alt="Translation history" width="420"><br>
  <sub><b>History</b>: opened from the tray — list on the left, source &amp; result on the right</sub>
</td>
</tr>
</table>

## Features

- **Double-tap Ctrl+C** to translate the clipboard/selected text, shown in a popup near the mouse
- **Code-explanation mode**: when the selection is code, it explains what the code does (in Chinese) instead of force-translating it; mixed prose + code is translated normally while the code is kept verbatim
- **Dictionary mode**: for a single selected word, returns a bilingual (CN/EN) entry (phonetics, part of speech, definitions, examples)
- **Rich-text rendering**: the result popup supports lightweight Markdown and colorizes code like a code editor; copied text stays plain
- **Multiple target languages**: auto-detect CN↔EN, or fix the target to Chinese/English/Japanese/Korean/French/German/Spanish
- **Re-translate/switch direction in the popup**: a "Re-translate" menu re-translates the selection into another language in one click
- **Streaming for long text**: long text reveals its translation progressively
- **Translation history**: open the history window from the tray
- **Popup layout**: classic (screen-centered) or dynamic (follows the mouse), switchable in settings
- **Themes**: follow system / light / dark
- **System tray**: left-click for settings; right-click for history / check for updates / pause / quit
- **Self-update**: the app itself is a `git clone` deployment, so it can check GitHub and update — via a manual "Check for updates" or a nightly auto-update
- Optional launch on startup

## Requirements

- Windows (uses Windows APIs for DPI awareness, multi-monitor positioning, and reading the theme from the registry)
- Python 3.12+
- Node.js (used to install the Claude Code CLI)
- Working Claude access: either a signed-in Claude subscription (Pro/Max), or a compatible local proxy endpoint (for example, Agent Maestro)
- ⚠️ **Upgrade the Claude Code CLI to the latest version first** — an outdated CLI has incompatible arguments that cause translation errors or garbled output. This is the most common install pitfall, so always update to the latest before installing.

## Quick install (recommended)

Run this one line in **PowerShell**. The script installs git / Python / Node as
needed, clones the repo, installs the Claude CLI and Python dependencies, and
launches the app:

```powershell
irm https://raw.githubusercontent.com/mclight-ship-it/cc-translate/master/install.ps1 | iex
```

It automates **everything except logging in to Claude** — that's a one-time
browser OAuth no script can do for you. When it finishes, run `claude` once to
sign in (uses your existing Claude subscription, no extra charge).

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

# 4. Install Python dependencies
pip install pynput pyperclip pystray Pillow
# Optional: code-block syntax highlighting (auto-degrades to monochrome code style when missing)
pip install Pygments

# 5. First run (make sure the current directory is the project root, cc-translate)
pythonw translator.pyw   # the first run auto-creates a "CC Translate" icon in the Start Menu
```

> ⚠️ **Make sure the Claude Code CLI is up to date**: this tool relies on newer `claude -p`
> command-line arguments, and an old version causes translation errors or garbled output.
> **Even if you already had `claude` installed, run `npm install -g @anthropic-ai/claude-code@latest`
> again before installing this tool**, and confirm with `claude --version`.

> Note: `translator.pyw` auto-detects the location of the `claude` CLI (checks PATH first, then the npm global directory).
> If it can't be found, make sure `claude` is on PATH, or that the npm global bin directory has been added to PATH.

## Launching

After the first run it auto-creates a **CC Translate** icon in the Start Menu; afterwards you can launch it straight from the Start Menu (no command line needed).

## Launch on startup (optional)

Check "Launch on startup" in the app's **Settings** (this creates a shortcut in the Startup folder).
Or manually place a shortcut to `run.vbs` in the Startup folder. `run.vbs` relies on `pythonw.exe` being on PATH.

## Files

| File | Purpose |
|---|---|
| `translator.pyw` | Main program |
| `install.ps1` | One-line installer (`irm ... \| iex`) |
| `run.vbs` | Silent launcher (portable, locates `translator.pyw` in the same directory) |
| `cc-dark.ico` / `cc-light.ico` | Adaptive tray icons (dark/light taskbar); Start Menu / shortcut uses `cc-dark.ico` |
| `cc.ico` | Legacy icon (fallback when the themed icons are missing) |
| `config.json` | User config (stored under `%APPDATA%\CC Translate\`, generated locally, not committed) |
| `history.json` | Translation history (stored under `%APPDATA%\CC Translate\`, generated locally, not committed) |

## One-shot install instructions for AI assistants

See [INSTALL_FOR_LLM.md](INSTALL_FOR_LLM.md): hand that file's contents to a Claude/AI assistant on a new machine and it will install the dependencies, sign in, install the libraries, and launch the app step by step.

## Development / testing

Change workflow and conventions are in [AGENTS.md](AGENTS.md). Key points:

- Run the tests: `python -m unittest discover -s tests` (standard library, no extra dependencies).
- The repo ships a pre-push hook that runs the tests before pushing and blocks the push on failure.
- **Enable it once after a fresh clone**: `git config core.hooksPath .githooks`.
