"""
CC Translate — i18n (internationalization) module.
Centralized translation management for Chinese (zh_CN) and English (en_US).
"""

import os
import locale as _locale
from typing import Optional

# Current language: will be initialized at startup
_current_language: str = "en_US"

TRANSLATIONS = {
    "zh_CN": {
        # Tray menu
        "tray.history": "历史记录",
        "tray.screenshot": "截图翻译",
        "tray.pause": "暂停翻译",
        "tray.resume": "继续翻译",
        "tray.settings": "设置",
        "tray.diagnostics": "诊断",
        "tray.check_update": "检查更新",
        "tray.exit": "退出",

        # Result popup
        "result.title": "译文",
        "result.copy": "复制",
        "result.copied": "已复制",
        "result.copy_failed": "复制失败",
        "result.actions": "操作 ▾",
        "result.retranslate": "重新翻译",
        "result.copy_bilingual": "复制双语",
        "result.rewrite_casual": "改写为口语",
        "result.rewrite_formal": "改写为正式",
        "result.rewrite_professional": "改写为专业术语",
        "result.explain": "解释代码",
        "result.explaining": "解释中…",
        "result.processing": "处理中…",
        "result.explained": "已解释",

        # Error messages
        "error.screenshot_failed": "截图失败，请重试。",
        "error.no_text_detected": "未识别到文字。",
        "error.translation_failed": "翻译失败",
        "error.title": "翻译失败",

        # Settings window
        "settings.title": "设置",
        "settings.model": "模型",
        "settings.custom_model": "自定义模型",
        "settings.work_dir": "工作目录",
        "settings.theme": "主题",
        "settings.layout": "布局",
        "settings.ocr_engine": "OCR 引擎",
        "settings.language": "语言",
        "settings.auto_start": "开机自动启动",
        "settings.update_and_restart": "更新并重启",
        "settings.check_update": "检查更新",
        "settings.translate_shortcut": "翻译 (双击 Ctrl+C)",
        "settings.screenshot_shortcut": "截图翻译 (Win+Shift+C)",
        
        # Settings field labels (section titles and field names)
        "settings.label.translate_section": "翻译 (双击 Ctrl+C)",
        "settings.label.translate_model": "翻译模型",
        "settings.label.translate_direction": "翻译方向",
        "settings.label.appearance_section": "外观",
        "settings.label.theme_field": "主题",
        "settings.label.popup_layout": "弹窗位置",
        "settings.label.font_size": "字体大小",
        "settings.label.language_field": "语言",
        "settings.label.screenshot_section": "截图翻译 (Win+Shift+C)",
        "settings.label.ocr_engine": "识别引擎",
        "settings.label.ocr_hotkey": "启用截图翻译热键",
        "settings.label.behavior_section": "行为",
        "settings.label.double_press_window": "双击间隔 (秒)",
        "settings.label.max_chars": "最大字符数",
        "settings.label.history_limit": "历史保留条数",
        "settings.label.history_enabled": "记录历史",
        "settings.label.open_history": "打开历史",
        "settings.label.auto_start_boot": "开机自动启动",
        "settings.label.update_section": "更新",
        "settings.label.current_version": "当前版本",
        "settings.label.auto_update": "夜间自动更新",
        "settings.label.check_update_action": "检查更新",
        "settings.label.saved_notice": "已保存 ✓（主题下次弹窗生效）",
        "settings.label.language_changed": "语言已更改，正在重启…",
        "settings.label.save_failed": "保存失败",
        "settings.label.close": "关闭",

        # Diagnostics window
        "diagnostics.title": "诊断",
        "diagnostics.copy": "复制诊断",
        "diagnostics.copied": "已复制",
        "diagnostics.overview": "【概览】",
        "diagnostics.version": "版本",
        "diagnostics.git_deployed": "Git 部署",
        "diagnostics.backend": "当前后端",
        "diagnostics.cli_version": "Claude CLI",
        "diagnostics.login_status": "登录状态",
        "diagnostics.powershell_policy": "PowerShell 执行策略",
        "diagnostics.custom_model": "自定义模型",
        "diagnostics.endpoint_connectivity": "端点连通性",
        "diagnostics.last_result": "最近一次结果",
        "diagnostics.refreshing": "刷新中…",
        "diagnostics.redetect": "重新检测",
        "diagnostics.yes": "是",
        "diagnostics.no": "否",
        "diagnostics.none": "暂无",
        "diagnostics.available": "可连接",
        "diagnostics.unreachable": "无法连接",
        "diagnostics.login_complete": "订阅登录已完成",
        "diagnostics.unrestricted": "Unrestricted",

        # History window
        "history.title": "历史记录",
        "history.search_placeholder": "搜索",
        "history.filter_all": "全部",
        "history.filter_text": "文本",
        "history.filter_code": "代码",
        "history.filter_ocr": "截图",
        "history.copy": "复制",
        "history.rerun": "重新翻译",
        "history.delete": "删除",

        # Status/UI elements
        "ui.loading": "处理中…",
        "ui.close": "✕",
        "ui.ok": "确定",
        "ui.cancel": "取消",
        "ui.save": "保存",
        "ui.search": "搜索",

        # Model routing note (in diagnostics)
        "model.routing_app_model": "设置中的模型",
        "model.routing_normal": "正常情况下会使用",
        "model.routing_proxy": "代理声明模型",
        "model.routing_note": "模型路由",

        # Update messages
        "update.available": "有新版本可用",
        "update.no_update": "已是最新版本",
        "update.checking": "检查中…",
        "update.updating": "更新中…",

        # Misc
        "misc.custom_endpoint": "自定义兼容端点",
        "misc.powered_by_maestro": "Powered by Agent Maestro",
    },

    "en_US": {
        # Tray menu
        "tray.history": "History",
        "tray.screenshot": "Screenshot",
        "tray.pause": "Pause",
        "tray.resume": "Resume",
        "tray.settings": "Settings",
        "tray.diagnostics": "Diagnostics",
        "tray.check_update": "Check Update",
        "tray.exit": "Exit",

        # Result popup
        "result.title": "Translation",
        "result.copy": "Copy",
        "result.copied": "Copied",
        "result.copy_failed": "Copy Failed",
        "result.actions": "More ▾",
        "result.retranslate": "Retranslate",
        "result.copy_bilingual": "Bilingual",
        "result.rewrite_casual": "Casual",
        "result.rewrite_formal": "Formal",
        "result.rewrite_professional": "Professional",
        "result.explain": "Explain",
        "result.explaining": "Explaining…",
        "result.processing": "Processing…",
        "result.explained": "Explained",

        # Error messages
        "error.screenshot_failed": "Screenshot failed",
        "error.no_text_detected": "No text",
        "error.translation_failed": "Failed",
        "error.title": "Error",

        # Settings window
        "settings.title": "Settings",
        "settings.model": "Model",
        "settings.custom_model": "Custom",
        "settings.work_dir": "Directory",
        "settings.theme": "Theme",
        "settings.layout": "Layout",
        "settings.ocr_engine": "OCR",
        "settings.language": "Language",
        "settings.auto_start": "Auto-start",
        "settings.update_and_restart": "Update",
        "settings.check_update": "Check",
        "settings.translate_shortcut": "Translate",
        "settings.screenshot_shortcut": "Screenshot",
        
        # Settings field labels (section titles and field names)
        "settings.label.translate_section": "Translate (Double Ctrl+C)",
        "settings.label.translate_model": "Model",
        "settings.label.translate_direction": "Direction",
        "settings.label.appearance_section": "Appearance",
        "settings.label.theme_field": "Theme",
        "settings.label.popup_layout": "Position",
        "settings.label.font_size": "Font",
        "settings.label.language_field": "Language",
        "settings.label.screenshot_section": "Screenshot (Win+Shift+C)",
        "settings.label.ocr_engine": "Engine",
        "settings.label.ocr_hotkey": "Hotkey",
        "settings.label.behavior_section": "Behavior",
        "settings.label.double_press_window": "Double-click (s)",
        "settings.label.max_chars": "Max Chars",
        "settings.label.history_limit": "History Limit",
        "settings.label.history_enabled": "History",
        "settings.label.open_history": "Open",
        "settings.label.auto_start_boot": "Auto-start",
        "settings.label.update_section": "Update",
        "settings.label.current_version": "Version",
        "settings.label.auto_update": "Auto Update",
        "settings.label.check_update_action": "Check",
        "settings.label.saved_notice": "Saved ✓",
        "settings.label.language_changed": "Language changed, restarting…",
        "settings.label.save_failed": "Save failed",
        "settings.label.close": "Close",

        # Diagnostics window
        "diagnostics.title": "Diagnostics",
        "diagnostics.copy": "Copy",
        "diagnostics.copied": "Copied",
        "diagnostics.overview": "【Overview】",
        "diagnostics.version": "Version",
        "diagnostics.git_deployed": "Git",
        "diagnostics.backend": "Backend",
        "diagnostics.cli_version": "CLI",
        "diagnostics.login_status": "Login",
        "diagnostics.powershell_policy": "Policy",
        "diagnostics.custom_model": "Custom",
        "diagnostics.endpoint_connectivity": "Endpoint",
        "diagnostics.last_result": "Last",
        "diagnostics.refreshing": "Refreshing…",
        "diagnostics.redetect": "Redetect",
        "diagnostics.yes": "Yes",
        "diagnostics.no": "No",
        "diagnostics.none": "None",
        "diagnostics.available": "OK",
        "diagnostics.unreachable": "Unreachable",
        "diagnostics.login_complete": "Complete",
        "diagnostics.unrestricted": "Unrestricted",

        # History window
        "history.title": "History",
        "history.search_placeholder": "Search",
        "history.filter_all": "All",
        "history.filter_text": "Text",
        "history.filter_code": "Code",
        "history.filter_ocr": "Screenshot",
        "history.copy": "Copy",
        "history.rerun": "Retranslate",
        "history.delete": "Delete",

        # Status/UI elements
        "ui.loading": "Processing…",
        "ui.close": "✕",
        "ui.ok": "OK",
        "ui.cancel": "Cancel",
        "ui.save": "Save",
        "ui.search": "Search",

        # Model routing note (in diagnostics)
        "model.routing_app_model": "App Model",
        "model.routing_normal": "Used normally",
        "model.routing_proxy": "Proxy",
        "model.routing_note": "Routing",

        # Update messages
        "update.available": "Update available",
        "update.no_update": "Up to date",
        "update.checking": "Checking…",
        "update.updating": "Updating…",

        # Misc
        "misc.custom_endpoint": "Endpoint",
        "misc.powered_by_maestro": "Powered by Maestro",
    }
}


def set_language(lang: str) -> None:
    """Set the current language (zh_CN or en_US)."""
    global _current_language
    if lang in TRANSLATIONS:
        _current_language = lang


def get_language() -> str:
    """Get the current language code."""
    return _current_language


def get(key: str, default: Optional[str] = None) -> str:
    """
    Get translated string by key.
    
    Args:
        key: Translation key (e.g., "tray.history")
        default: Default value if key not found (uses key itself if not specified)
    
    Returns:
        Translated string in current language
    """
    if _current_language in TRANSLATIONS:
        text = TRANSLATIONS[_current_language].get(key)
        if text is not None:
            return text
    
    # Fallback to English if current language missing key
    text = TRANSLATIONS["en_US"].get(key)
    if text is not None:
        return text
    
    # If still not found, use default or key itself
    return default if default is not None else key


def detect_system_language() -> str:
    """
    Detect system language from Windows locale.
    
    Returns:
        "zh_CN" for Chinese, "en_US" for English, else "en_US"
    """
    try:
        # Get Windows UI language
        import ctypes
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        
        # Extract language code (lower 10 bits)
        lang_code = lang_id & 0x3FF
        
        # Chinese (Simplified): 0x04
        # Chinese (Traditional): 0x04 with region 0x0804 (PRC) or 0x0404 (Taiwan)
        if lang_code == 0x04:
            return "zh_CN"
        
        # English: 0x09
        elif lang_code == 0x09:
            return "en_US"
        
        else:
            # Default to English for other languages
            return "en_US"
    
    except Exception:
        # Fallback: try Python's locale module
        try:
            lang, _ = _locale.getdefaultlocale()
            if lang and lang.startswith("zh"):
                return "zh_CN"
            else:
                return "en_US"
        except Exception:
            return "en_US"


def initialize(language: Optional[str] = None) -> None:
    """
    Initialize i18n at startup.
    
    If language is provided, use it. Otherwise detect system language.
    Called once at app startup.
    
    Args:
        language: Language code to set (e.g., "zh_CN"), or None to auto-detect
    """
    global _current_language
    
    if language:
        set_language(language)
    else:
        detected = detect_system_language()
        set_language(detected)
