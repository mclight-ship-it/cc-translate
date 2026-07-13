# CC Translate

[简体中文](README.md) | [English](README.en.md)

A local select-and-translate tool: **double-tap Ctrl+C** to translate the currently selected text, shown in a popup near the cursor. Built on the Claude Code CLI — it reuses your existing Claude subscription, needs no separate API key, and runs entirely on your machine.

## Features

- **Double-tap Ctrl+C** to translate the clipboard/selected text, shown in a popup near the mouse
- **Code-explanation mode**: when the selection is code, it explains what the code does (in Chinese) instead of force-translating it; for mixed prose + code it translates normally while keeping the code verbatim, and the popup offers an "Explain code" button to explain the code on demand
- **Dictionary mode**: for a single selected word, returns a bilingual (CN/EN) entry (phonetics, part of speech, definitions, examples)
- **Rich-text rendering**: the result popup supports lightweight Markdown (inline code, code blocks, bold, italic, headings, lists, links) and colorizes code like a code editor; code blocks can optionally use Pygments for syntax highlighting (auto-degrading to monochrome when not installed); copied text stays plain
- **Multiple target languages**: auto-detect CN↔EN, or fix the target to Chinese/English/Japanese/Korean/French/German/Spanish
- **Re-translate/switch direction in the popup**: the normal translation popup has a "Re-translate" menu to force the current selection into Chinese/English/Japanese/Korean/French/German/Spanish
- **Streaming for long text**: long text reveals its translation progressively
- **Translation history**: open the history window from the tray; toggleable, with a configurable entry cap
- **Popup layout**: classic (screen-centered, fixed size) or dynamic (follows the mouse, auto-sizing), switchable in settings
- **Themes**: follow system / light / dark
- **System tray**: left-click for settings; right-click for history / check for updates / pause / quit (right-click "Check for updates" opens Settings and triggers the check there, converging both entry points on one experience)
- **Self-update**: the app itself is a `git clone` deployment, so it can check GitHub and `git pull` to update, then restart. "Check for updates" in Settings only checks and changes nothing; when a newer version is found it reveals an "Update & restart" button, leaving the decision to you. The "Nightly auto-update" toggle (on by default) updates silently in the background. Before updating it compiles + runs the tests and auto-rolls-back on failure, so it will never update into a non-starting state
- Optional launch on startup

## Requirements

- Windows (uses Windows APIs for DPI awareness, multi-monitor positioning, and reading the theme from the registry)
- Python 3.12+
- Node.js (used to install the Claude Code CLI)
- A signed-in Claude subscription (Pro/Max)
- Upgrade the Claude Code CLI to the latest version first (to avoid argument incompatibilities)

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
npm install -g @anthropic-ai/claude-code@latest
claude --version
claude   # on first run, follow the prompt to sign in via browser, then Ctrl+C to exit interactive mode

# 4. Install Python dependencies
pip install pynput pyperclip pystray Pillow
# Optional: code-block syntax highlighting (auto-degrades to monochrome code style when missing)
pip install Pygments

# 5. First run (make sure the current directory is the project root, cc-translate)
pythonw translator.pyw   # the first run auto-creates a "CC Translate" icon in the Start Menu
```

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
| `run.vbs` | Silent launcher (portable, locates `translator.pyw` in the same directory) |
| `cc.ico` | Tray / shortcut icon |
| `config.json` | User config (stored under `%APPDATA%\CC Translate\`, generated locally, not committed) |
| `history.json` | Translation history (stored under `%APPDATA%\CC Translate\`, generated locally, not committed) |

## One-shot install instructions for AI assistants

See [INSTALL_FOR_LLM.md](INSTALL_FOR_LLM.md): hand that file's contents to a Claude/AI assistant on a new machine and it will install the dependencies, sign in, install the libraries, and launch the app step by step.

## Development / testing

Change workflow and conventions are in [AGENTS.md](AGENTS.md). Key points:

- Run the tests: `python -m unittest discover -s tests` (standard library, no extra dependencies).
- The repo ships a pre-push hook that runs the tests before pushing and blocks the push on failure.
- **Enable it once after a fresh clone**: `git config core.hooksPath .githooks`.
