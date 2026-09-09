# CC Translate

[English](README.md) | [简体中文](README.zh.md)

**Double-tap Ctrl+C for high-quality selected-text translation on Windows.**
Results appear beside the cursor, powered by OpenAI GPT or Claude, with an
optional instant offline dictionary for short English and Chinese terms.

> CC Translate does not require its own API key. It reuses the authentication
> of your local Codex or Claude CLI. You still need one working provider through
> a subscription/sign-in, API key, or compatible custom endpoint.

## Install in one command

Run in **PowerShell**:

```powershell
irm https://raw.githubusercontent.com/mclight-ship-it/cc-translate/master/install.ps1 | iex
```

The installer prepares Git, Python 3.12, Node.js, both model CLIs, Python
dependencies, and the Start menu launcher. Account authorization remains with
the provider. OpenAI GPT is the default; sign in with:

```powershell
codex login
codex login status
```

Already using an API key or compatible custom Codex provider? Keep that working
native Codex configuration instead. To use Claude, run `claude`, complete its
browser sign-in, then switch provider in Settings.

**Requirements:** Windows 10/11 and at least one working provider: the official
Codex CLI with ChatGPT sign-in, API-key authentication, or a compatible custom
provider; or an up-to-date Claude Code CLI with a Claude subscription or
compatible local proxy. The installer handles Git, Python 3.12, and Node.js when
they are missing.

## See it in action

<p align="center">
  <img src="docs/screenshots/popup-translate.png" alt="Current CC Translate result popup" width="620"><br>
  <sub>Select text, double-tap <b>Ctrl+C</b>, and get a readable result beside the cursor.</sub>
</p>

## Why CC Translate

- **One gesture, minimal interruption.** Translate selected text with
  **Ctrl+C twice**. With nothing selected, the same gesture opens Quick Input;
  `Win+Shift+C` starts screenshot translation.
- **Smart before the model call.** A local classifier distinguishes normal
  text, code, and short dictionary queries without another model request. Code
  is explained instead of awkwardly translated, while mixed text keeps its code
  intact.
- **Instant dictionary, AI when useful.** The optional offline dictionary shows
  high-confidence exact results first, with a lightning badge and source
  attribution. Misses fall back to AI, and an AI supplement can add context
  without delaying the local result.
- **Choose the provider that fits.** Use OpenAI GPT through the official Codex
  CLI or Claude Code. Long text streams progressively and can lead with a short
  key-point summary.
- **Built for repeated use.** Searchable local history, screenshot translation
  with vision or offline OCR, quick rewrite/distill actions, multiple target
  languages, light/dark themes, tray controls, and safe self-update.

<p align="center">
  <img src="docs/screenshots/popup-dict.png" alt="Current offline dictionary result with instant badge, sources, and AI supplement" width="720"><br>
  <sub>An exact local result appears first; sources and AI assistance remain clearly separated.</sub>
</p>

<p align="center">
  <img src="docs/screenshots/settings.png" alt="Current CC Translate settings" width="760"><br>
  <sub><b>Settings</b>: provider, translation, dictionary, screenshot, history, and update controls.</sub>
</p>

<p align="center">
  <img src="docs/screenshots/history.png" alt="Current searchable local translation history" width="760"><br>
  <sub><b>History</b>: local, searchable, and filterable by result type.</sub>
</p>

## Everyday use

1. Launch **CC Translate** from the Start menu; it stays in the system tray.
2. Select text anywhere and double-tap `Ctrl+C`.
3. Use the result actions to copy, re-translate, rewrite, or distill.
4. Press `Win+Shift+C` to translate a screen region with a vision model or
   optional offline OCR.
5. Open Settings from the tray to change provider, target language, theme,
   history, dictionary, or startup behavior.

The result window always offers a manual correction path when automatic
classification is not what you intended: translate detected code as text, or
explain ordinary text as code.

## Providers, privacy, and advanced setup

CC Translate sends selected content only to the provider you configure. Codex
requests use ephemeral sessions, stdin, a read-only sandbox, disabled MCP/tool
access, hook preflight, and fail-closed handling of unexpected tool events.
Claude tools are disabled for translation calls. CC Translate does not copy
provider credentials or rewrite global provider configuration.

Custom Codex providers, isolated `CODEX_HOME` compatibility, model-catalog
behavior, manual installation, optional OCR dependencies, and troubleshooting
are documented in [Advanced configuration](docs/ADVANCED_CONFIGURATION.md).
For installation by a coding assistant, use
[INSTALL_FOR_LLM.md](docs/INSTALL_FOR_LLM.md).

## Offline dictionary and licenses

The optional ~65 MB dictionary is downloaded only after
**Settings → Offline dictionary acceleration → Download and enable**. It is
verified before atomic installation and never fetched during app startup.
Disabling or deleting it leaves AI dictionary mode available.

The artifact contains pinned data from:

- WikDict eng-zho 2025.11.21 — CC BY-SA 3.0
- CC-CEDICT 2017-04-28 immutable snapshot — CC BY-SA 3.0
- Chinese Open Wordnet / Princeton WordNet via OMW 2.0 — their respective
  WordNet licenses
- Unihan 17.0.0 — Unicode License v3

Application code and dictionary data have separate licensing terms. Full
attribution, upstream URLs, hashes, indexing changes, and original license texts
are in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES),
[`data/dictionary/licenses/`](data/dictionary/licenses/), and
**About → Data licenses**. Each local result also exposes its own sources.

The installed-path benchmark for **local lookup and formatting only** measured
12,000 queries at P95 0.616 ms; this is not end-to-end popup latency.

## More

- [Advanced configuration and manual install](docs/ADVANCED_CONFIGURATION.md)
- [Roadmap](docs/ROADMAP.md)
- [Development conventions](AGENTS.md)

To uninstall, open **Settings**, scroll to the bottom, and choose
**Uninstall CC Translate**. Python, Node.js, and model CLIs are left installed
because other applications may use them.
