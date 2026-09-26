# CC Translate for Mac

[English](MACOS.md) | [简体中文](MACOS.zh.md)

## Requirements

- macOS 14 or later, on an Apple Silicon Mac (M1 or later).
- A working Codex CLI or Claude Code installation for model translation.
  The app uses your existing CLI account. With subscription sign-in, no separate
  paid API key is required.
- No system Python installation is needed; the app includes its own runtime.
- The optional local dictionary can be downloaded and used without a model account.

## Install

1. Open [Mac releases](https://github.com/mclight-ship-it/cc-translate/releases?q=macos-v&expanded=true).
2. Download the Apple Silicon application ZIP under **Assets**, not GitHub's
   automatically generated **Source code** archive.
3. Quit any older CC Translate copy. Unzip the download and move the app to
   **Applications**, replacing the older copy if present. Keep only one installed
   copy so shortcuts and permissions refer to the intended app.
4. Open CC Translate from Applications.

The release includes a checksum file for verifying the downloaded archive.
The app package does not include the maintainer's work logs, account credentials,
translation history or local configuration.

## First launch and permissions

**This release is not Apple Developer ID signed or notarized.** Its Sparkle
update signature authenticates update archives; it is not an Apple notarization
or a guarantee that macOS will retain permission approvals.

If macOS blocks the app, check that the download came from this repository's
release page. Use **System Settings > Privacy & Security > Open Anyway** if
macOS offers it for that app. Do not disable Gatekeeper globally or run a
blanket quarantine-removal command. If macOS reports malware or a damaged app,
stop rather than bypassing the warning, and report the exact message.

Open **Settings > Shortcuts** to enable double-tap Command+C. Grant Accessibility
and Input Monitoring only when using the shortcut. Screenshot translation
requires Screen Recording permission. If approval does not take effect, quit
and reopen the app. Typing or pasting into the main translation window does not
require granting all shortcut permissions.

## Configure model translation

Install and sign in to a CLI using its official instructions:

- [Codex CLI](https://developers.openai.com/codex/cli)
- [Claude Code](https://code.claude.com/docs/en/setup)

In **Settings > Translation**, choose the service. If the app cannot locate its
executable, use the installation-location control to select the actual
executable. The service name being selected does not by itself confirm that
its CLI is installed or that its account is ready.

The app does not install a model account, copy credentials, or change your
account's billing route. Existing API-key/custom-provider CLI configurations
can incur their own charges; subscription-only users should use the CLI's
subscription sign-in.

## Everyday use

- Select text in another app and press **Command+C twice** to translate it.
- When a selection cannot be read, use the quick-input window to type or paste
  text. Some PDF viewers and protected fields do not expose readable selections.
- In the main window, enter text and choose **Translate**, or press
  **Command+Return**.
- Long-text summaries appear before the full translation when enabled. Reading
  position and selected text are preserved as the remaining output arrives.
- Use screenshot translation for images or inaccessible text. Text recognition
  runs locally. Image mode sends the selected image to the configured model
  service; it is not an offline alternative.
- Download the optional local dictionary from Settings. Its data and source
  licenses remain separate from model-generated results.
- History and settings are stored locally. Updating the app is not an uninstall.

## Updates

Choose **Check for Updates** in the app. The Mac updater uses HTTPS and verifies
the archive's Ed25519 signature before extraction and installation. Accept the
offered update to replace the installed app and restart it; repeated manual ZIP
downloads should not be necessary once a release-channel build is installed.
Update checks and installation are user-initiated; the app does not silently
install updates.

Older development builds without a configured release feed need **one manual
installation of the first release-channel build**. An updater cannot add a feed
to an already-installed build that has no feed.

Keep the app in the same Applications location. Settings, history and downloaded
dictionary data should remain in place across updates. However, because these
builds have no stable Developer ID signing identity, **macOS can require
Accessibility, Input Monitoring or Screen Recording permission again after an
update**. Signed update archives do not remove that OS limitation.

If an update fails verification or cannot install, keep the current app and use
the release page for recovery. Do not disable signature verification. Include
the installed version, macOS version and the visible error when reporting a
problem, but do not post account files or private source text.

## Licenses

The application-code license is not yet selected. No permissive application
license is implied. Bundled Python, Sparkle and other third-party components
retain their own notices inside the app. Optional dictionary data is separately
licensed; see [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES).
