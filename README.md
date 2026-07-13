# CC Translate

[简体中文](README.md) | [English](README.en.md)

一个本地划词翻译小工具：**双击 Ctrl+C** 翻译当前选中的文字，弹窗显示译文。基于 Claude Code CLI，复用你已有的 Claude 订阅，无需单独的 API key，全部本机运行。

灵感来自 DeepL 的双击 Ctrl+C 划词翻译，但翻译引擎是大模型，并额外提供**代码解释模式**、**单词词典模式**、**富文本排版**、**长文流式**、**翻译历史**、**主题切换**等。

## 功能

- **双击 Ctrl+C** 翻译剪贴板/选中文字，鼠标旁弹窗显示
- **代码解释模式**：选中的是代码时，不强行翻译，而是用中文解释这段代码的用途；文字与代码混排时正常翻译并保留代码原样，弹窗提供「解释代码」按钮，按需解释其中的代码
- **词典模式**：选中单个单词时，返回中英双语词条（音标、词性、释义、例句）
- **富文本排版**：结果弹窗支持轻量 Markdown（行内代码、代码块、加粗、斜体、标题、列表、链接），像代码编辑器那样对代码、标识符、路径等分色显示；代码块可选 Pygments 按 token 语法高亮（未安装时自动降级为单色代码样式），让译文更清晰；复制出的仍是无标记的纯文本
- **多目标语言**：自动检测中↔英，或固定译成中/英/日/韩/法/德/西
- **弹窗内换向重译**：普通译文弹窗提供「重译」菜单，一键把当前选中内容强制重译成中/英/日/韩/法/德/西
- **长文流式**：长文本逐步显现译文
- **翻译历史**：托盘打开历史窗口，可开关、可设条数上限
- **弹窗布局**：经典（屏幕居中、固定大小）或动态（跟随鼠标、自适应大小），可在设置中切换
- **主题**：跟随系统 / 浅色 / 深色
- **系统托盘**：左键设置，右键历史/检查更新/暂停/退出（右键「检查更新」会打开设置并在其中触发检查，两个入口收束到同一处体验）
- **自动更新**：本身即 `git clone` 部署，可从 GitHub 检查并 `git pull` 更新后自动重启。设置里的「检查更新」只检查不改动，发现新版本时显示「更新并重启」按钮，由用户决定是否更新；「夜间自动更新」开关（默认开启，凌晨 3 点）会在后台静默完成更新。更新前先编译 + 跑测试校验，失败自动回滚，绝不会更新成起不来的状态；重启完成后弹出托盘气泡确认「已更新并重启」（因为新进程的托盘图标可能被 Windows 收进溢出区，用气泡给出可见反馈）
- 弹窗可选中文字、复制、拖动、缩放、滚动；加载窗点外部消失
- 可设开机自启

## 运行环境

- Windows（用到 Windows API 做 DPI 感知、多屏定位、注册表读主题）
- Python 3.12+
- Node.js（用于安装 Claude Code CLI）
- 一个可登录的 Claude 订阅（Pro/Max）
- 先将 Claude Code CLI 升级到最新版本（避免参数不兼容）

## 安装（人工步骤）

```bash
# 1. 获取项目代码
git clone https://github.com/mclight-ship-it/cc-translate.git
cd cc-translate

# 2. 安装 Node.js 和 Python（若已装可跳过）
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12

# 3. 安装/升级 Claude Code CLI 并登录（走浏览器 OAuth，用你的订阅，不额外收费）
npm install -g @anthropic-ai/claude-code@latest
claude --version
claude   # 首次运行按提示在浏览器登录，然后 Ctrl+C 退出交互模式

# 4. 安装 Python 依赖
pip install pynput pyperclip pystray Pillow
# 可选：代码块语法高亮（缺失时自动降级为单色代码样式）
pip install Pygments

# 5. 首次运行（确保当前目录是项目根目录 cc-translate）
pythonw translator.pyw   # 首次运行会自动创建开始菜单里的“CC Translate”图标
```

> 提示：`translator.pyw` 会自动探测 `claude` CLI 的位置（先查 PATH，再查 npm 全局目录）。
> 若找不到，请确保 `claude` 在 PATH 中，或 npm 全局 bin 目录已加入 PATH。

## 启动方式

首次运行后会自动在开始菜单创建 **CC Translate** 图标，后续可直接在开始菜单启动（无需命令行）。

## 开机自启（可选）

在应用的**设置**里勾选“开机自动启动”即可（会在启动文件夹创建快捷方式）。
或手动把 `run.vbs` 的快捷方式放进启动文件夹。`run.vbs` 依赖 `pythonw.exe` 在 PATH 中。

## 文件说明

| 文件 | 作用 |
|---|---|
| `translator.pyw` | 主程序 |
| `run.vbs` | 静默启动器（可移植，定位同目录的 translator.pyw） |
| `cc.ico` | 托盘/快捷方式图标 |
| `config.json` | 用户配置（存于 `%APPDATA%\CC Translate\`，本地生成，不入库） |
| `history.json` | 翻译历史（存于 `%APPDATA%\CC Translate\`，本地生成，不入库） |

## 给 AI 助手的一键安装说明

见 [INSTALL_FOR_LLM.md](INSTALL_FOR_LLM.md)：把该文件内容交给新机器上的 Claude/AI 助手，它会按步骤完成依赖安装、登录、依赖库安装并启动。

## 开发 / 测试

改动流程与约定见 [AGENTS.md](AGENTS.md)。要点：

- 跑测试：`python -m unittest discover -s tests`（标准库，无需额外依赖）。
- 仓库自带 pre-push 钩子，推送前会自动跑测试、失败即阻止推送。
- **新 clone 后启用一次**：`git config core.hooksPath .githooks`。
