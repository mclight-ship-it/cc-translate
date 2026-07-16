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
        "tray.screenshot_menu": "截图翻译",
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
        "result.rewrite_summary": "提炼要点",
        "result.explain": "解释代码",
        "result.explaining": "解释中…",
        "result.processing": "翻译中…",
        "result.processing_screenshot": "截图翻译中…",
        "result.explained": "已解释",
        "result.retranslate_to": "译成{language}",
        "result.language_chinese": "中文",
        "result.source_label": "原文",
        "result.output_label": "结果",
        "result.explain_failed": "代码解释失败，请重试。",
        "result.explain_divider": "\n\n────────  代码解释  ────────\n\n",
        "result.title_code": "代码解释",
        "result.title_dict": "词典",

        # Error messages
        "error.screenshot_failed": "截图失败，请重试。",
        "error.no_text_detected": "未识别到文字。",
        "error.translation_failed": "翻译失败",
        "error.title": "翻译失败",
        "error.ocr_timeout": "识别超时，请重试。",
        "error.translation_timeout": "翻译超时，请重试。",
        "error.unexpected": "出错了：{error}",
        "error.login_required": "Claude 未登录。请在终端运行 claude 登录后重试。",
        "error.rate_limited": "请求过于频繁，请稍后重试。",
        "error.no_result": "没有返回结果，请重试。",
        "error.translation_failed_with_reason": "翻译失败：{error}",

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
        "settings.label.close": "关闭",

        # About dialog
        "about.title": "关于",
        "about.name": "CC Translate",
        "about.description": "由大语言模型驱动的划词翻译 App",
        "about.version": "版本",
        "about.contact_author": "联系作者",
        "about.author_email": "mclight@foxmail.com",
        "about.github": "GitHub 仓库",
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
        "diagnostics.unknown": "未知",
        "diagnostics.model_not_set": "未设置",
        "diagnostics.json_root_not_object": "JSON 根对象不是对象",
        "diagnostics.value_set": "[已设置]",
        "diagnostics.backend.agent_maestro": "Agent Maestro（本地代理）",
        "diagnostics.backend.anthropic_api": "Anthropic API / 官方端点",
        "diagnostics.backend.custom_endpoint": "自定义兼容端点",
        "diagnostics.backend.api_token": "API Key / Token 模式",
        "diagnostics.backend.subscription": "官方 Claude CLI / 订阅直连",
        "diagnostics.routing.same_model": "App 会把设置中的模型作为 `--model` 传给 Claude CLI；当前代理/端点也声明了同名模型。",
        "diagnostics.routing.proxy_override": "App 会把设置中的模型作为 `--model` 传给 Claude CLI；但当前代理/端点还声明了模型 `{backend_model}`，最终是否覆写取决于代理/端点实现。",
        "diagnostics.routing.no_proxy": "当前未检测到代理级模型声明；正常情况下会使用设置中的模型。",
        "diagnostics.endpoint.parse_failed": "端点地址无法解析：{error}",
        "diagnostics.endpoint.missing_host": "端点地址缺少主机名",
        "diagnostics.endpoint.missing_port": "端点地址缺少端口/协议",
        "diagnostics.endpoint.reachable": "{host}:{port} 可连接",
        "diagnostics.endpoint.refused": "{host}:{port} 拒绝连接",
        "diagnostics.endpoint.unreachable": "{host}:{port} 不可达：{error}",
        "diagnostics.endpoint.not_configured": "未配置自定义端点（走 CLI 默认链路）",
        "diagnostics.read_failed": "读取失败：{error_type}: {error}",
        "diagnostics.settings.user_json": "用户级 settings.json",
        "diagnostics.settings.user_local_json": "用户级 settings.local.json",
        "diagnostics.settings.app_json": "应用目录 settings.json",
        "diagnostics.settings.app_local_json": "应用目录 settings.local.json",
        "diagnostics.login.not_detected": "未检测到订阅登录",
        "diagnostics.login.meta_read_failed": "登录元数据读取失败",
        "diagnostics.login.complete": "订阅登录已完成",
        "diagnostics.login.account_incomplete": "检测到账号，但登录未完成",
        "diagnostics.login.meta_missing": "未找到订阅登录元数据",
        "diagnostics.exit_code": "退出码 {code}",
        "diagnostics.advice.agent_unreachable": "检测到 Agent Maestro 本地代理，但对应端口当前不可连接。请先启动 VS Code / Agent Maestro，或移除 ~/.claude/settings.json 里的代理配置。",
        "diagnostics.advice.agent_maybe_down": "当前翻译走 Agent Maestro 本地代理；如果 VS Code / Agent Maestro 没有运行，翻译可能会失败。",
        "diagnostics.advice.api_mode": "当前运行不是订阅直连，而是 API / 自定义端点模式。",
        "diagnostics.advice.login_overridden": "已检测到订阅登录，但它当前会被 API / 自定义端点配置覆盖。",
        "diagnostics.advice.login_missing": "未检测到 Claude 订阅登录。请在终端运行 claude 或 claude.cmd 完成登录。",
        "diagnostics.advice.ps_policy": "PowerShell 执行策略较严格；如果手动运行 claude 报脚本被禁用，可改用 claude.cmd，或执行 Set-ExecutionPolicy -Scope CurrentUser RemoteSigned。",
        "diagnostics.advice.cli_failed": "claude CLI 调用失败；请检查 CLI 安装、PATH 或 npm 全局命令。",
        "diagnostics.advice.no_obvious_issue": "未发现明显异常。当前环境看起来基本正常。",
        "diagnostics.action.fix_cli": "先在终端执行 `claude --version` 检查 CLI 是否可用；若失败，请修复 PATH 或重新安装 Claude Code CLI。",
        "diagnostics.action.login_subscription": "当前是订阅直连模式，请先在终端执行 `claude` 或 `claude.cmd` 完成登录。",
        "diagnostics.action.start_agent_maestro": "请先启动 VS Code / Agent Maestro，并确认本地代理端口可访问后再重试。",
        "diagnostics.action.keep_agent_maestro_running": "请保持 VS Code / Agent Maestro 运行，再点击“重试翻译”快速验证。",
        "diagnostics.action.check_endpoint": "请检查自定义端点地址、端口和代理服务状态，确认连通后再重试。",
        "diagnostics.action.use_claude_cmd": "执行策略较严格时，优先使用 `claude.cmd`，或执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。",
        "diagnostics.action.retry_after_timeout": "上次失败像是超时：先检查网络/代理稳定性，然后点击“重试翻译”。",
        "diagnostics.action.retry_after_rate_limit": "上次失败像是限流：等待片刻后再点击“重试翻译”，或切换到可用后端。",
        "diagnostics.action.retry_after_login": "上次失败像是认证问题：先确认登录/订阅状态，再点击“重试翻译”。",
        "diagnostics.action.retry_generic": "修复上述问题后，点击“重试翻译”快速验证是否恢复。",
        "diagnostics.action.no_action_needed": "当前未发现必须处理的问题；如仍偶发失败，可直接点“重试翻译”。",
        "diagnostics.summary.cli_ok": "CLI 正常",
        "diagnostics.summary.cli_bad": "CLI 异常",
        "diagnostics.summary.link_ready": "链路已配置",
        "diagnostics.summary.pending_login": "待登录",
        "diagnostics.last.none": "暂无",
        "diagnostics.last.success": "成功",
        "diagnostics.last.failed": "失败",
        "diagnostics.last.no_preview": "（无预览）",
        "diagnostics.last.unknown_type": "未知类型",
        "diagnostics.section.advice": "【建议】",
        "diagnostics.section.next_steps": "【建议操作】",
        "diagnostics.section.paths": "【关键路径】",
        "diagnostics.section.env": "【进程环境变量】",
        "diagnostics.section.configs": "【Claude 配置文件】",
        "diagnostics.section.recent_errors": "【最近错误日志】",
        "diagnostics.path.work_dir": "工作目录",
        "diagnostics.path.login_meta": "登录元数据",
        "diagnostics.env.none": "（未检测到相关环境变量）",
        "diagnostics.config.missing": "不存在",
        "diagnostics.config.read_failed": "读取失败（{error}）",
        "diagnostics.config.no_env_override": "未设置 env 覆盖",
        "diagnostics.error_log.empty": "（error.log 暂无内容）",
        "diagnostics.redetect_failed_title": "诊断失败",
        "diagnostics.redetect_failed_detail": "诊断失败：{error_type}: {error}",
        "diagnostics.retry_translate": "重试翻译",
        "diagnostics.retrying": "重试中…",
        "diagnostics.retry_unavailable": "无可重试内容",

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
        "history.tag.text": "译",
        "history.tag.dict": "词",
        "history.tag.code": "码",
        "history.tag.ocr": "图",
        "history.preview_empty": "（空白记录）",
        "history.no_match": "没有匹配的历史记录。",
        "history.matches_zero": "0 条匹配",
        "history.matches_count": "{shown} / {total} 条",
        "history.cleared": "历史已清空",
        "history.copied_result": "已复制结果",
        "history.copy_failed": "复制失败",
        "history.copied_bilingual": "已复制双语",
        "history.no_source": "这条记录没有可重译的原文",
        "history.clear": "清空历史",
        "history.copy_bilingual": "复制双语",
        "history.copy_result": "复制结果",

        # Status/UI elements
        "ui.loading": "翻译中…",
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
        "update.in_progress": "更新进行中…",
        "update.non_git": "非 git 部署，无法自动更新",
        "update.check_failed_remote": "检查失败：无法连接远程",
        "update.found_version": "发现新版本 {sha}",
        "update.downloading": "正在下载更新…",
        "update.download_failed": "更新失败：下载出错",
        "update.local_changes": "本地有改动，未自动更新",
        "update.merge_failed": "更新失败：合并出错",
        "update.rollback": "更新有误，已回滚",
        "update.done_restarting": "更新完成，正在重启…",
        "update.failed": "更新失败",
        "update.notice_with_version": "已更新到 {version} 并重启",
        "update.notice_no_version": "已更新并重启",

        # OCR
        "ocr.drag_select_hint": "拖动选择要翻译的区域 · Esc 取消",

        # Misc
        "misc.custom_endpoint": "自定义兼容端点",
        "misc.powered_by_maestro": "Powered by Agent Maestro",
    },

    "en_US": {
        # Tray menu
        "tray.history": "History",
        "tray.screenshot": "Screenshot",
        "tray.screenshot_menu": "Screenshot Translate",
        "tray.pause": "Pause Translation",
        "tray.resume": "Resume Translation",
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
        "result.copy_bilingual": "Copy bilingual",
        "result.rewrite_casual": "More casual",
        "result.rewrite_formal": "More formal",
        "result.rewrite_professional": "Professional",
        "result.rewrite_summary": "Key Points",
        "result.explain": "Explain",
        "result.explaining": "Explaining…",
        "result.processing": "Processing…",
        "result.processing_screenshot": "OCR…",
        "result.explained": "Explained",
        "result.retranslate_to": "To {language}",
        "result.language_chinese": "Chinese",
        "result.source_label": "Source",
        "result.output_label": "Result",
        "result.explain_failed": "Explain failed, please retry.",
        "result.explain_divider": "\n\n────────  Code Explanation  ────────\n\n",
        "result.title_code": "Code Explain",
        "result.title_dict": "Dictionary",

        # Error messages
        "error.screenshot_failed": "Screenshot failed",
        "error.no_text_detected": "No text",
        "error.translation_failed": "Failed",
        "error.title": "Error",
        "error.ocr_timeout": "OCR timeout, please retry.",
        "error.translation_timeout": "Translation timeout, please retry.",
        "error.unexpected": "Error: {error}",
        "error.login_required": "Claude not logged in. Run claude in terminal and retry.",
        "error.rate_limited": "Too many requests. Please retry later.",
        "error.no_result": "No result returned. Please retry.",
        "error.translation_failed_with_reason": "Translation failed: {error}",

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

        # About dialog
        "about.title": "About",
        "about.name": "CC Translate",
        "about.description": "LLM-powered select-and-translate app",
        "about.version": "Version",
        "about.contact_author": "Contact Author",
        "about.author_email": "mclight@foxmail.com",
        "about.github": "GitHub Repository",

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
        "diagnostics.unknown": "Unknown",
        "diagnostics.model_not_set": "Not set",
        "diagnostics.json_root_not_object": "JSON root is not an object",
        "diagnostics.value_set": "[configured]",
        "diagnostics.backend.agent_maestro": "Agent Maestro (Local Proxy)",
        "diagnostics.backend.anthropic_api": "Anthropic API / Official Endpoint",
        "diagnostics.backend.custom_endpoint": "Custom Compatible Endpoint",
        "diagnostics.backend.api_token": "API Key / Token Mode",
        "diagnostics.backend.subscription": "Official Claude CLI / Subscription",
        "diagnostics.routing.same_model": "The app passes the configured model via `--model`; the proxy/endpoint declares the same model.",
        "diagnostics.routing.proxy_override": "The app passes the configured model via `--model`; the proxy/endpoint also declares `{backend_model}`, and final override behavior depends on proxy/endpoint implementation.",
        "diagnostics.routing.no_proxy": "No proxy-level model declaration detected; the configured model is used normally.",
        "diagnostics.endpoint.parse_failed": "Endpoint parse failed: {error}",
        "diagnostics.endpoint.missing_host": "Endpoint host missing",
        "diagnostics.endpoint.missing_port": "Endpoint port/scheme missing",
        "diagnostics.endpoint.reachable": "{host}:{port} reachable",
        "diagnostics.endpoint.refused": "{host}:{port} refused",
        "diagnostics.endpoint.unreachable": "{host}:{port} unreachable: {error}",
        "diagnostics.endpoint.not_configured": "No custom endpoint configured (using default CLI path)",
        "diagnostics.read_failed": "Read failed: {error_type}: {error}",
        "diagnostics.settings.user_json": "User settings.json",
        "diagnostics.settings.user_local_json": "User settings.local.json",
        "diagnostics.settings.app_json": "App settings.json",
        "diagnostics.settings.app_local_json": "App settings.local.json",
        "diagnostics.login.not_detected": "Subscription login not detected",
        "diagnostics.login.meta_read_failed": "Failed to read login metadata",
        "diagnostics.login.complete": "Subscription login complete",
        "diagnostics.login.account_incomplete": "Account detected, onboarding incomplete",
        "diagnostics.login.meta_missing": "Subscription login metadata not found",
        "diagnostics.exit_code": "Exit code {code}",
        "diagnostics.advice.agent_unreachable": "Agent Maestro local proxy detected, but the port is currently unreachable. Start VS Code / Agent Maestro, or remove proxy settings from ~/.claude/settings.json.",
        "diagnostics.advice.agent_maybe_down": "Translation is routed through Agent Maestro local proxy. If VS Code / Agent Maestro is not running, translation may fail.",
        "diagnostics.advice.api_mode": "Current runtime is API/custom-endpoint mode, not direct subscription mode.",
        "diagnostics.advice.login_overridden": "Subscription login is detected, but currently overridden by API/custom endpoint settings.",
        "diagnostics.advice.login_missing": "Claude subscription login not detected. Run claude or claude.cmd in terminal to complete login.",
        "diagnostics.advice.ps_policy": "PowerShell policy is strict. If claude is blocked by scripts, use claude.cmd or run Set-ExecutionPolicy -Scope CurrentUser RemoteSigned.",
        "diagnostics.advice.cli_failed": "claude CLI call failed. Check CLI install, PATH, or npm global commands.",
        "diagnostics.advice.no_obvious_issue": "No obvious issue detected. Environment looks healthy.",
        "diagnostics.action.fix_cli": "Run `claude --version` in terminal first. If it fails, fix PATH or reinstall Claude Code CLI.",
        "diagnostics.action.login_subscription": "You are in direct subscription mode. Run `claude` or `claude.cmd` in terminal to complete login first.",
        "diagnostics.action.start_agent_maestro": "Start VS Code / Agent Maestro and confirm the local proxy port is reachable, then retry.",
        "diagnostics.action.keep_agent_maestro_running": "Keep VS Code / Agent Maestro running, then click 'Retry Translation' to verify quickly.",
        "diagnostics.action.check_endpoint": "Check custom endpoint host/port and proxy service health, then retry after connectivity is restored.",
        "diagnostics.action.use_claude_cmd": "With strict PowerShell policy, prefer `claude.cmd`, or run Set-ExecutionPolicy -Scope CurrentUser RemoteSigned.",
        "diagnostics.action.retry_after_timeout": "Last failure looks like a timeout. Check network/proxy stability, then click 'Retry Translation'.",
        "diagnostics.action.retry_after_rate_limit": "Last failure looks rate-limited. Wait briefly, then click 'Retry Translation', or switch backend.",
        "diagnostics.action.retry_after_login": "Last failure looks auth-related. Confirm login/subscription state, then click 'Retry Translation'.",
        "diagnostics.action.retry_generic": "After fixing the issue above, click 'Retry Translation' to validate recovery quickly.",
        "diagnostics.action.no_action_needed": "No must-fix issue detected. If failures are intermittent, click 'Retry Translation' directly.",
        "diagnostics.summary.cli_ok": "CLI OK",
        "diagnostics.summary.cli_bad": "CLI Error",
        "diagnostics.summary.link_ready": "Path ready",
        "diagnostics.summary.pending_login": "Login pending",
        "diagnostics.last.none": "None",
        "diagnostics.last.success": "Success",
        "diagnostics.last.failed": "Failed",
        "diagnostics.last.no_preview": "(No preview)",
        "diagnostics.last.unknown_type": "Unknown type",
        "diagnostics.section.advice": "[Advice]",
        "diagnostics.section.next_steps": "[Actionable Steps]",
        "diagnostics.section.paths": "[Key Paths]",
        "diagnostics.section.env": "[Process Environment]",
        "diagnostics.section.configs": "[Claude Config Files]",
        "diagnostics.section.recent_errors": "[Recent Errors]",
        "diagnostics.path.work_dir": "Working Directory",
        "diagnostics.path.login_meta": "Login Metadata",
        "diagnostics.env.none": "(No related environment variables found)",
        "diagnostics.config.missing": "Missing",
        "diagnostics.config.read_failed": "Read failed ({error})",
        "diagnostics.config.no_env_override": "No env overrides",
        "diagnostics.error_log.empty": "(error.log is empty)",
        "diagnostics.redetect_failed_title": "Diagnostics failed",
        "diagnostics.redetect_failed_detail": "Diagnostics failed: {error_type}: {error}",
        "diagnostics.retry_translate": "Retry Translation",
        "diagnostics.retrying": "Retrying…",
        "diagnostics.retry_unavailable": "Nothing to retry",

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
        "history.tag.text": "TR",
        "history.tag.dict": "DICT",
        "history.tag.code": "CODE",
        "history.tag.ocr": "OCR",
        "history.preview_empty": "(Empty)",
        "history.no_match": "No matching history records.",
        "history.matches_zero": "0 matches",
        "history.matches_count": "{shown} / {total}",
        "history.cleared": "History cleared",
        "history.copied_result": "Result copied",
        "history.copy_failed": "Copy failed",
        "history.copied_bilingual": "Bilingual copied",
        "history.no_source": "This record has no source to retranslate",
        "history.clear": "Clear",
        "history.copy_bilingual": "Bilingual",
        "history.copy_result": "Copy Result",

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
        "update.in_progress": "Update in progress…",
        "update.non_git": "Not a git deployment; auto update unavailable",
        "update.check_failed_remote": "Check failed: cannot reach remote",
        "update.found_version": "New version {sha}",
        "update.downloading": "Downloading update…",
        "update.download_failed": "Update failed: download error",
        "update.local_changes": "Local changes detected; skipped auto update",
        "update.merge_failed": "Update failed: merge error",
        "update.rollback": "Update invalid; rolled back",
        "update.done_restarting": "Update complete, restarting…",
        "update.failed": "Update failed",
        "update.notice_with_version": "Updated to {version} and restarted",
        "update.notice_no_version": "Updated and restarted",

        # OCR
        "ocr.drag_select_hint": "Drag to select area to translate · Esc to cancel",

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
