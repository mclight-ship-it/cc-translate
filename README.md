# CC Translate

一个本地划词翻译小工具：**双击 Ctrl+C** 翻译当前选中的文字，弹窗显示译文。基于 Claude Code CLI，复用你已有的 Claude 订阅，无需单独的 API key，全部本机运行。

灵感来自 DeepL 的双击 Ctrl+C 划词翻译，但翻译引擎是大模型，并额外提供**单词词典模式**、**长文流式**、**翻译历史**、**主题切换**等。

## 功能

- **双击 Ctrl+C** 翻译剪贴板/选中文字，鼠标旁弹窗显示
- **词典模式**：选中单个单词时，返回中英双语词条（音标、词性、释义、例句）
- **多目标语言**：自动检测中↔英，或固定译成中/英/日/韩/法/德/西
- **长文流式**：长文本逐步显现译文
- **翻译历史**：托盘打开历史窗口，可开关、可设条数上限
- **主题**：跟随系统 / 浅色 / 深色
- **系统托盘**：左键设置，右键历史/暂停/退出
- 弹窗可选中文字、复制、拖动、滚动；加载窗点外部消失
- 可设开机自启

## 运行环境

- Windows（用到 Windows API 做 DPI 感知、多屏定位、注册表读主题）
- Python 3.12+
- Node.js（用于安装 Claude Code CLI）
- 一个可登录的 Claude 订阅（Pro/Max）

## 安装（人工步骤）

```bash
# 1. 安装 Node.js 和 Python（若已装可跳过）
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12

# 2. 安装 Claude Code CLI 并登录（走浏览器 OAuth，用你的订阅，不额外收费）
npm install -g @anthropic-ai/claude-code
claude   # 首次运行按提示在浏览器登录，然后 Ctrl+C 退出交互模式

# 3. 安装 Python 依赖
pip install pynput pyperclip pystray Pillow

# 4. 运行
pythonw translator.pyw
```

> 提示：`translator.pyw` 会自动探测 `claude` CLI 的位置（先查 PATH，再查 npm 全局目录）。
> 若找不到，请确保 `claude` 在 PATH 中，或 npm 全局 bin 目录已加入 PATH。

## 开机自启（可选）

在应用的**设置**里勾选“开机自动启动”即可（会在启动文件夹创建快捷方式）。
或手动把 `run.vbs` 的快捷方式放进启动文件夹。`run.vbs` 依赖 `pythonw.exe` 在 PATH 中。

## 文件说明

| 文件 | 作用 |
|---|---|
| `translator.pyw` | 主程序 |
| `run.vbs` | 静默启动器（可移植，定位同目录的 translator.pyw） |
| `cc.ico` | 托盘/快捷方式图标 |
| `config.json` | 用户配置（本地生成，不入库） |
| `history.json` | 翻译历史（本地生成，不入库） |

## 给 AI 助手的一键安装说明

见 [INSTALL_FOR_LLM.md](INSTALL_FOR_LLM.md)：把该文件内容交给新机器上的 Claude/AI 助手，它会按步骤完成依赖安装、登录、依赖库安装并启动。
