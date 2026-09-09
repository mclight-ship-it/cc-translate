# CC Translate 高级配置

[返回中文 README](../README.zh.md) | [English](ADVANCED_CONFIGURATION.md)

本文集中说明手动安装、provider 认证、自定义 Codex provider、翻译安全边界、
可选依赖和离线词典。多数用户只需使用
[一条命令安装](../README.zh.md#一条命令安装)。

## 系统与 Provider 要求

- Windows 10/11
- Git
- Python 3.12+
- Node.js LTS
- 至少一个可用 provider：
  - 官方 Codex CLI，已完成 ChatGPT 登录、API-key 认证或配置兼容的自定义 provider
  - 最新 Claude Code CLI，已连接 Claude 订阅或兼容本地代理

CC Translate 没有独立账号或 API key。Provider 是否可用、套餐限制、模型权限和
费用仍由 OpenAI、Anthropic 或你配置的自定义端点决定。

## 手动安装

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

这里的 Codex 版本是当前经过 CC Translate 流式路径验证的版本，安装器也固定为
同一版本。Claude 应保持最新版，因为旧版 `claude -p` 参数不兼容。

首次启动会创建轻量品牌启动器和开始菜单快捷方式。应用仍直接运行 Git checkout，
因此也能安全自更新。

## Provider 认证

### 通过 Codex 使用 OpenAI GPT

使用 ChatGPT 浏览器授权：

```powershell
codex login
codex login status
```

CC Translate 使用 `CODEX_HOME`（通常为 `~/.codex`）中的 Codex 原生认证和配置，
不读取、复制或保存 Codex token。已配置 API key 或兼容自定义 provider 的用户
可以继续使用现有可用配置。

默认模式是**智能路由（极速）**，设置中仍可选择**自动选择（优质）**。实际可用
模型取决于账号套餐、组织策略、端点和 Codex CLI 版本。

### Claude Code

```powershell
npm install -g @anthropic-ai/claude-code@latest
claude
```

在浏览器完成登录，退出交互会话，再到 CC Translate 设置中选择 Claude。
兼容的本地 Claude 代理同样可以使用。

如果 PowerShell 提示脚本被禁用，请运行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

也可以直接使用 `claude.cmd` 和 `codex.cmd`；CC Translate 本身会调用 `.cmd`
启动器。

## 自定义 Codex Provider

请在 Codex 原生 `config.toml` 中配置兼容 provider。CC Translate 的 exec、
流式、预热和诊断都使用同一份实际生效的原生配置，不要求额外建立专属目录。

如需兼容性隔离，可显式为 CC Translate 指定独立目录：

```powershell
setx CC_TRANSLATE_CODEX_HOME "$env:APPDATA\CC Translate\codex-provider"
```

修改环境变量后需要重启。覆盖目录必须包含 `config.toml`；无效配置会明确失败，
不会悄悄切换账号或目录。删除该变量即可恢复原生 `CODEX_HOME`。

对于普通自定义 provider，CC Translate 可从用户自己安装的 Codex 导出实际生效的
模型元数据，存入 `%APPDATA%\CC Translate\codex-catalogs`。它不会安装统一模型
列表、复制凭据或改变 provider、模型与推理强度。设置
`CC_TRANSLATE_CODEX_CATALOG=off` 并重启可关闭此行为。

模型目录只描述能力，不代表账号权限或端点一定可用。诊断会把自定义认证标为
“尚未验证”，也不会仅为探测权限而执行凭据 helper 或提交模型请求。

## 翻译安全边界

模型 CLI 只作为翻译后端使用：

- 选中文字通过 stdin 传入，不进入命令行参数
- Codex 使用临时会话、专用工作目录和只读沙箱
- 翻译 turn 不可使用个人指令、技能、记忆、插件、通知命令、用户 hook、MCP
  server 或工具
- 开始 turn 前预检可执行 hook
- 遇到未知 JSONL 或工具事件时失败关闭
- 不改写全局 Codex 配置，也不绕过强制管理策略
- Claude 翻译调用明确禁用工具

这些限制只作用于翻译子进程，不会修改用户正常的交互式 Codex 或 Claude 环境。

## 可选能力

`requirements.txt` 安装应用常规依赖。以下增强缺失时会安全降级：

- Pygments：代码块语法着色
- winsdk：截图翻译的 Windows 离线 OCR
- comtypes：跨进程选中文字检测

没有本地 OCR 时仍可使用视觉模型截图翻译；缺少 Pygments 或 comtypes 也不影响
核心翻译。

## 离线词典数据

应用启动时绝不下载词典。用户在设置中明确触发后，应用才下载固定 Release 制品，
校验精确大小、SHA-256、SQLite schema 与数据版本，再原子安装到
`%APPDATA%\CC Translate\dictionary\cc_dictionary.sqlite3`。

词典使用以下固定输入：

| 来源 | 版本 | 许可证 |
|---|---|---|
| WikDict eng-zho | 2025.11.21 | CC BY-SA 3.0 |
| CC-CEDICT | immutable 2017-04-28 快照 | CC BY-SA 3.0 |
| Chinese Open Wordnet / Princeton WordNet | OMW 2.0 对齐 | 分别对应的 WordNet 许可证 |
| Unicode Unihan | 17.0.0 | Unicode License v3 |

每个 entry 和 sense 都保留来源。应用不会虚构缺失的例句、音标或词性。应用代码与
词典数据许可彼此分离；数据制品可直接复制，不加密，也不受 DRM 限制。

完整信息见 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES) 与
[原始许可证文本](../data/dictionary/licenses/)。开发者可复现制品：

```powershell
python tools\build_dictionary.py --cache <source-cache> --download
```

`--download` 只用于开发者显式构建，正常应用启动绝不会执行。

## 安装器参数与故障排查

可选安装变量：

```powershell
$env:CC_TRANSLATE_DIR = "D:\Apps\cc-translate"
$env:CC_TRANSLATE_DRYRUN = "1"
```

- 找不到 CLI 时，确认其 `.cmd` 启动器位于 npm 全局 bin；常见路径为
  `%APPDATA%\npm`。
- 双击 `Ctrl+C` 没反应时，确认 CC Translate 正在托盘运行且没有暂停翻译。
- 设置中的诊断会显示 provider、流式、词典与近期本地性能状态，不会提交测试模型
  请求。
- 卸载请使用**设置 → 卸载 CC Translate**。共享运行时和 provider CLI 会保留。

