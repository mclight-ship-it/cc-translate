# CC Translate advanced configuration

[Back to README](../README.md) | [简体中文](ADVANCED_CONFIGURATION.zh.md)

This guide covers manual installation, provider authentication, custom Codex
providers, the translation security boundary, optional dependencies, and the
offline dictionary. Most users only need the
[one-command installer](../README.md#install-in-one-command).

## System and provider requirements

- Windows 10/11
- Git
- Python 3.12+
- Node.js LTS
- At least one working provider:
  - official Codex CLI with ChatGPT sign-in, API-key authentication, or a
    compatible custom provider
  - current Claude Code CLI with a Claude subscription or compatible local proxy

CC Translate has no separate account or API key. Provider availability, plan
limits, model access, and billing remain with OpenAI, Anthropic, or the custom
endpoint you configure.

## Manual installation

```powershell
git clone https://github.com/mclight-ship-it/cc-translate.git
cd cc-translate

winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12

npm install -g @openai/codex@0.146.0
npm install -g @anthropic-ai/claude-code@latest

python -m pip install --upgrade -r requirements.txt
python -c "import cc_update,subprocess; subprocess.Popen([cc_update.ensure_branded_launcher() or cc_update.PYTHONW, cc_update.SCRIPT_PATH], cwd=cc_update.APP_DIR)"
```

The Codex version above is the version currently validated with CC Translate's
streaming path. The installer pins the same version. Claude should be kept at
the latest release because older `claude -p` arguments are incompatible.

The first launch creates a small branded launcher and a Start menu shortcut.
The application still runs from the Git checkout, which also enables safe
self-update.

## Provider authentication

### OpenAI GPT through Codex

For ChatGPT browser authorization:

```powershell
codex login
codex login status
```

CC Translate uses native Codex authentication and configuration from
`CODEX_HOME` (normally `~/.codex`). It does not read, copy, or store Codex
tokens. API-key and compatible custom-provider users can keep their existing
working Codex configuration.

**Smart routing (fast)** is the default. **Auto select (quality)** remains
available in Settings. Actual models depend on the account plan, organization
policy, endpoint, and Codex CLI version.

### Claude Code

```powershell
npm install -g @anthropic-ai/claude-code@latest
claude
```

Complete the browser sign-in, exit the interactive session, and select Claude
in CC Translate Settings. A compatible local Claude proxy can also be used.

If PowerShell reports that scripts are disabled, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Alternatively use `claude.cmd` and `codex.cmd`. CC Translate invokes the `.cmd`
launchers directly.

## Custom Codex providers

Configure a compatible provider in native Codex `config.toml`. CC Translate's
exec, streaming, prewarm, and diagnostics paths use that same effective native
configuration. A separate CC Translate home is not required.

For compatibility testing, you can explicitly isolate CC Translate:

```powershell
setx CC_TRANSLATE_CODEX_HOME "$env:APPDATA\CC Translate\codex-provider"
```

Restart after changing environment variables. The override directory must
contain `config.toml`; invalid configuration fails visibly rather than silently
switching account or home. Remove the variable to return to native `CODEX_HOME`.

For ordinary custom providers, CC Translate can export effective model metadata
from the user's installed Codex into
`%APPDATA%\CC Translate\codex-catalogs`. It does not install a shared model
list, copy credentials, or change provider/model/reasoning selections. Disable
this behavior with `CC_TRANSLATE_CODEX_CATALOG=off` and restart.

Catalog metadata describes capabilities, not account permission or endpoint
availability. Diagnostics labels custom authentication as unverified and does
not run credential helpers or submit a model request merely to test access.

## Translation security boundary

Provider CLIs are used only as translation backends:

- selected text is passed through stdin, not a command-line argument
- Codex uses ephemeral sessions, a dedicated working directory, and a read-only
  sandbox
- personal instructions, skills, memories, plugins, notification commands,
  user hooks, MCP servers, and tools are not available to translation turns
- executable hooks are preflighted before a turn
- unexpected JSONL or tool events fail closed
- global Codex configuration is never rewritten and managed policy is not
  bypassed
- Claude translation calls explicitly disable tools

These restrictions apply to child translation processes; they do not modify the
user's normal interactive Codex or Claude installation.

## Optional capabilities

`requirements.txt` installs the normal app dependencies. Individual optional
enhancements degrade safely when unavailable:

- Pygments: syntax coloring for code blocks
- winsdk: offline Windows OCR for screenshot translation
- comtypes: cross-process selected-text detection

The vision-model screenshot path remains available without local OCR. Core
translation remains available without Pygments or comtypes.

## Offline dictionary data

The dictionary is never downloaded at startup. The explicit Settings action
downloads a pinned release asset, validates its exact size, SHA-256, SQLite
schema, and data version, then installs it atomically under
`%APPDATA%\CC Translate\dictionary\cc_dictionary.sqlite3`.

The artifact uses pinned inputs:

| Source | Version | License |
|---|---|---|
| WikDict eng-zho | 2025.11.21 | CC BY-SA 3.0 |
| CC-CEDICT | immutable 2017-04-28 snapshot | CC BY-SA 3.0 |
| Chinese Open Wordnet / Princeton WordNet | OMW 2.0 alignment | respective WordNet licenses |
| Unicode Unihan | 17.0.0 | Unicode License v3 |

Every entry and sense retains source provenance. The app does not synthesize
missing examples, pronunciation, or parts of speech. Application-code and
dictionary-data licensing remain separate; the data artifact is directly
copyable and is not encrypted or DRM-restricted.

See [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES) and the
[original license texts](../data/dictionary/licenses/). Developers can reproduce
the artifact with:

```powershell
python tools\build_dictionary.py --cache <source-cache> --download
```

The `--download` flag is for the explicit developer build only, never normal
application startup.

## Installer and troubleshooting controls

Optional installer variables:

```powershell
$env:CC_TRANSLATE_DIR = "D:\Apps\cc-translate"
$env:CC_TRANSLATE_DRYRUN = "1"
```

- If a CLI is not found, verify its `.cmd` launcher is in the npm global bin
  directory, commonly `%APPDATA%\npm`.
- If double-tap `Ctrl+C` does nothing, confirm CC Translate is running in the
  tray and translation is not paused.
- Diagnostics in Settings reports provider, streaming, dictionary, and recent
  local performance state without sending a test model request.
- To uninstall, use **Settings → Uninstall CC Translate**. Shared runtimes and
  provider CLIs are intentionally retained.

