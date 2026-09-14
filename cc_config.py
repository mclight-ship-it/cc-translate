"""Shared configuration values and migration rules; no paths, I/O or lifecycle ownership."""


class CFG:
    """String constants for every key in the user config dict.
    Use these instead of bare string literals to catch typos at lint time."""
    MODEL = "model"
    MODEL_PROVIDER = "model_provider"
    CLAUDE_MODEL = "claude_model"
    CODEX_MODEL = "codex_model"
    CODEX_STREAMING_EXPERIMENTAL = "codex_streaming_experimental"
    DOUBLE_PRESS_WINDOW = "double_press_window"
    FONT_SIZE = "font_size"
    DIRECTION = "direction"
    MAX_CHARS = "max_chars"
    THEME = "theme"
    POPUP_LAYOUT = "popup_layout"
    HISTORY_ENABLED = "history_enabled"
    HISTORY_LIMIT = "history_limit"
    AUTO_UPDATE_ENABLED = "auto_update_enabled"
    AUTO_UPDATE_HOUR = "auto_update_hour"
    OCR_ENGINE = "ocr_engine"
    OCR_HOTKEY_ENABLED = "ocr_hotkey_enabled"
    LANGUAGE = "language"
    CLIPBOARD_PROTECTION_ENABLED = "clipboard_protection_enabled"
    PLAIN_TEXT_PASTE_ENABLED = "plain_text_paste_enabled"
    AUTOSTART_INITIALIZED = "autostart_initialized"
    SUMMARY_ENABLED = "summary_enabled"
    LOCAL_DICTIONARY_ENABLED = "local_dictionary_enabled"
    # One-time marker for promoting the initial Labs features to on-by-default
    # without overriding a later explicit opt-out.
    LABS_DEFAULTS_MIGRATED = "labs_defaults_migrated"
    TRAY_CLICK_ACTION = "tray_click_action"
    # V2 is the production UI. Keep the saved flag and environment override so
    # support/dev builds can still force the legacy UI when diagnosing a
    # regression.
    UI_V2 = "ui_v2"
    # One-time marker for configs that saved the old dark-launch default. Before
    # this marker existed, Settings persisted ``ui_v2: false`` even though no
    # user-facing opt-out existed; migrate that generated value once so existing
    # users receive v2 too. A later explicit false is preserved.
    UI_V2_DEFAULT_MIGRATED = "ui_v2_default_migrated"


DEFAULT_CONFIG = {
    CFG.MODEL: "haiku",
    CFG.MODEL_PROVIDER: "codex_cli",
    CFG.CLAUDE_MODEL: "haiku",
    CFG.CODEX_MODEL: "auto-fast",
    CFG.CODEX_STREAMING_EXPERIMENTAL: True,
    CFG.DOUBLE_PRESS_WINDOW: 0.5,
    CFG.FONT_SIZE: 12,
    CFG.DIRECTION: "auto",
    CFG.MAX_CHARS: 5000,
    CFG.THEME: "system",
    CFG.POPUP_LAYOUT: "dynamic",
    CFG.HISTORY_ENABLED: True,
    CFG.HISTORY_LIMIT: 100,
    CFG.AUTO_UPDATE_ENABLED: True,
    CFG.AUTO_UPDATE_HOUR: 3,
    CFG.OCR_ENGINE: "claude",
    CFG.OCR_HOTKEY_ENABLED: True,
    CFG.CLIPBOARD_PROTECTION_ENABLED: True,
    CFG.PLAIN_TEXT_PASTE_ENABLED: False,
    CFG.AUTOSTART_INITIALIZED: False,
    CFG.SUMMARY_ENABLED: True,
    CFG.LOCAL_DICTIONARY_ENABLED: False,
    CFG.LABS_DEFAULTS_MIGRATED: True,
    CFG.TRAY_CLICK_ACTION: "settings",
    CFG.UI_V2: True,
    CFG.UI_V2_DEFAULT_MIGRATED: True,
}


def coerce_config(config, *, strict=False):
    """Apply the shared field conversions; explicit strict callers reject failed conversions."""
    for key, default in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = default
            continue
        value = config[key]
        try:
            if isinstance(default, bool):
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    config[key] = bool(value)
                elif isinstance(value, str):
                    config[key] = value.strip().lower() in ("1", "true", "yes", "on")
                elif strict:
                    raise TypeError("config_boolean_value_required: " + key)
                else:
                    config[key] = default
            elif isinstance(default, int):
                config[key] = int(value)
            elif isinstance(default, float):
                config[key] = float(value)
            elif isinstance(default, str):
                config[key] = value if isinstance(value, str) else str(value)
        except (TypeError, ValueError):
            if strict:
                raise
            config[key] = default


class Config(dict):
    """Typed, self-validating view over the user config.

    Subclasses ``dict`` so every existing access pattern keeps working
    unchanged — ``cfg[key]``, ``cfg.get(key)``, ``cfg[key] = v`` and
    ``json.dump(cfg, ...)`` all behave exactly as before. On top of that it:

      * merges ``DEFAULT_CONFIG`` so every known key is always present, and
      * coerces each known key to the type of its default (a config file that
        somehow holds a wrong-typed value can't crash the UI downstream), and
      * exposes typed read-only properties for the hot keys so new code can
        say ``cfg.model`` instead of ``cfg.get(CFG.MODEL, ...)`` with a
        literal fallback repeated at every call site.

    Unknown keys are preserved untouched for forward-compatibility."""

    def __init__(self, data=None):
        raw = dict(data or {})
        super().__init__(DEFAULT_CONFIG)
        if data:
            self.update(data)
        if CFG.UI_V2_DEFAULT_MIGRATED not in raw:
            # Settings used to serialize the internal dark-launch default
            # (ui_v2=false) into ordinary user configs even though users had no
            # UI control for it. Move every pre-release config to the production
            # v2 default once; the marker lets a subsequent explicit false keep
            # selecting legacy.
            self[CFG.UI_V2] = True
            self[CFG.UI_V2_DEFAULT_MIGRATED] = True
        if CFG.LABS_DEFAULTS_MIGRATED not in raw:
            # Earlier releases serialized both Labs features as false by
            # default. Promote existing configs once, then preserve any later
            # explicit opt-out.
            self[CFG.SUMMARY_ENABLED] = True
            self[CFG.CLIPBOARD_PROTECTION_ENABLED] = True
            self[CFG.LABS_DEFAULTS_MIGRATED] = True
        if CFG.MODEL_PROVIDER not in raw:
            # Configs from before provider selection existed contain only the
            # legacy Claude "model" key. Preserve that explicit old choice;
            # genuinely new/partial configs use the current GPT default.
            self[CFG.MODEL_PROVIDER] = (
                "claude_cli" if CFG.MODEL in raw
                else DEFAULT_CONFIG[CFG.MODEL_PROVIDER])
        if CFG.CLAUDE_MODEL not in raw:
            self[CFG.CLAUDE_MODEL] = raw.get(
                CFG.MODEL, DEFAULT_CONFIG[CFG.CLAUDE_MODEL])
        if CFG.CODEX_MODEL not in raw:
            self[CFG.CODEX_MODEL] = DEFAULT_CONFIG[CFG.CODEX_MODEL]
        elif self[CFG.CODEX_MODEL] == "gpt-5.4-mini":
            # The former standalone mini option is now an internal branch of
            # smart routing, so migrate saved selections to the complete mode.
            self[CFG.CODEX_MODEL] = "auto-fast"
        # Keep the old key synchronized for one downgrade-compatible release.
        self[CFG.MODEL] = self[CFG.CLAUDE_MODEL]
        self._coerce()

    def _coerce(self):
        """Force every known key to the type of its default; on mismatch that
        can't be coerced, fall back to the default rather than keep a value
        that would break a downstream widget."""
        coerce_config(self)

    # ---- Typed accessors (optional convenience; the dict API still works) ----
    @property
    def model(self):
        return self.get(CFG.MODEL, DEFAULT_CONFIG[CFG.MODEL])

    @property
    def model_provider(self):
        return self.get(CFG.MODEL_PROVIDER, DEFAULT_CONFIG[CFG.MODEL_PROVIDER])

    @property
    def claude_model(self):
        return self.get(CFG.CLAUDE_MODEL, DEFAULT_CONFIG[CFG.CLAUDE_MODEL])

    @property
    def codex_model(self):
        return self.get(CFG.CODEX_MODEL, DEFAULT_CONFIG[CFG.CODEX_MODEL])

    @property
    def direction(self):
        return self.get(CFG.DIRECTION, DEFAULT_CONFIG[CFG.DIRECTION])

    @property
    def theme(self):
        return self.get(CFG.THEME, DEFAULT_CONFIG[CFG.THEME])

    @property
    def font_size(self):
        return self.get(CFG.FONT_SIZE, DEFAULT_CONFIG[CFG.FONT_SIZE])

    @property
    def max_chars(self):
        return self.get(CFG.MAX_CHARS, DEFAULT_CONFIG[CFG.MAX_CHARS])

    @property
    def double_press_window(self):
        return self.get(CFG.DOUBLE_PRESS_WINDOW,
                        DEFAULT_CONFIG[CFG.DOUBLE_PRESS_WINDOW])

    @property
    def popup_layout(self):
        return self.get(CFG.POPUP_LAYOUT, DEFAULT_CONFIG[CFG.POPUP_LAYOUT])

    @property
    def language(self):
        return self.get(CFG.LANGUAGE)

    @property
    def history_enabled(self):
        return self.get(CFG.HISTORY_ENABLED, DEFAULT_CONFIG[CFG.HISTORY_ENABLED])

    @property
    def history_limit(self):
        return self.get(CFG.HISTORY_LIMIT, DEFAULT_CONFIG[CFG.HISTORY_LIMIT])

    @property
    def ocr_engine(self):
        return self.get(CFG.OCR_ENGINE, DEFAULT_CONFIG[CFG.OCR_ENGINE])

    @property
    def summary_enabled(self):
        return self.get(CFG.SUMMARY_ENABLED, DEFAULT_CONFIG[CFG.SUMMARY_ENABLED])


def plan_config_migration(raw, cfg):
    """Return (changed, raw payload), preserving the explicit cfg's streaming upgrade."""
    migrated = dict(raw)
    config_changed = False
    if CFG.UI_V2_DEFAULT_MIGRATED not in raw:
        migrated[CFG.UI_V2] = cfg[CFG.UI_V2]
        migrated[CFG.UI_V2_DEFAULT_MIGRATED] = cfg[
            CFG.UI_V2_DEFAULT_MIGRATED]
        config_changed = True
    if CFG.LABS_DEFAULTS_MIGRATED not in raw:
        migrated[CFG.SUMMARY_ENABLED] = cfg[CFG.SUMMARY_ENABLED]
        migrated[CFG.CLIPBOARD_PROTECTION_ENABLED] = cfg[
            CFG.CLIPBOARD_PROTECTION_ENABLED]
        migrated[CFG.LABS_DEFAULTS_MIGRATED] = cfg[
            CFG.LABS_DEFAULTS_MIGRATED]
        config_changed = True
    if not cfg[CFG.CODEX_STREAMING_EXPERIMENTAL]:
        cfg[CFG.CODEX_STREAMING_EXPERIMENTAL] = True
        migrated[CFG.CODEX_STREAMING_EXPERIMENTAL] = True
        config_changed = True
    return config_changed, migrated
