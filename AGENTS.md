# AGENTS.md — 给协作者与 AI 助手的项目约定

本文件说明改动 CC Translate 时必须遵守的流程。无论是人还是 AI 助手
（Copilot / Claude 等）接手本项目，请先读这份约定。

## 核心规则

**每次 `git push` 之前必须通过与本次改动相关的测试。**

- 测试位于 `tests/`，包括纯函数、provider 协议和 mock UI 集成测试；不访问
  网络或真实模型 CLI，共享剪贴板等系统边界必须 mock。
- 只用标准库 `unittest`，**零额外运行时依赖**，对 app 功能无影响。
- ⚠️ **测试还是自动更新的安全闸门**：应用夜间/手动 `git pull` 后会先 `py_compile`
  + 跑这套测试来校验新代码，任一失败就自动 `git reset --hard` 回滚并放弃重启。
  所以「保持测试全绿」不只是卫生，更直接决定每台机器能否安全自更新——
  推了一个测试挂掉的 commit，会让所有机器把它当成「坏更新」而回滚。

手动跑：

```bash
python -m unittest discover -s tests
```

## 这条规则是如何强制的

仓库自带一个 **pre-push 钩子**（`.githooks/pre-push`），它在每次 `git push`
时自动检查远端到待推送提交之间的文件：

- 只改文档或图片资源：不跑单元测试。
- 改独立模块或某个测试文件：只编译改动的 Python 文件，并跑映射到该区域的测试。
- 改 `translator.pyw`、`cc_core.py`、通用 app 模块、hook 等跨域文件：跑完整测试。
- 找不到安全映射时一律回退完整测试，不会静默漏测。

失败会**阻止推送**，不靠任何人的记性。开发过程中只跑聚焦测试；发布时不再
手动完整跑一遍、然后让 pre-push 重复跑第二遍。

### 换机器 / 新 clone 后，启用一次（重要）

Git 钩子存放路径不会随 clone 自动生效，需要在仓库根目录执行一次：

```bash
git config core.hooksPath .githooks
```

执行后，`git push` 会自动先跑测试。可用以下命令确认已启用：

```bash
git config --get core.hooksPath   # 应输出 .githooks
```

### 紧急绕过

极少数确有必要时（例如只改了 README，且 CI 另有保障）可以绕过：

```bash
git push --no-verify
```

请把绕过当成例外，而不是习惯。

## 文档：双语 README 必须同步

本项目有两份 README，互为翻译，**必须成对维护**：

- `README.md` — English (GitHub 默认展示)
- `README.zh.md` — 简体中文

两者顶部都有语言切换链接（`[English](README.md) | [简体中文](README.zh.md)`）。
GitHub 只渲染根目录的 `README.md`、且**没有内建的多语言切换**，这套双语完全靠手动维护。

**约定**：任何改动只要涉及 README 的内容（新增/修改功能说明、安装步骤、
文件表、开发说明等），就**必须在同一次提交里同时更新中英两份**，保持信息一致。
不要只改一份。若某次改动确实只动了其中一份的排版/文案而与另一份无关，请在提交信息里说明原因。

## 改动清单（给 AI 助手的默认工作流）

1. 改代码。
2. 若改动触及纯函数（`classify_selection`、`code_ratio`、`is_single_word`、
   `iter_rich_segments`、`highlight_code` 等），**同步更新或新增 `tests/` 用例**。
3. 若改动涉及 README 内容，**中英两份（`README.md` 和 `README.zh.md`）一起改**，保持一致。
4. 开发过程中只跑本次改动对应的聚焦测试。
5. 每次正式发布前将 `cc_update.py` 的 `VERSION_MINOR` 加 1；第三位继续使用
   Git commit count。例如 `4.0.239` 的下一次发布为 `4.1.<build>`。
6. 用个人账号 `mclight-ship-it` 提交并推送到 `origin master`
   （提交信息末尾加 `Co-authored-by: Copilot <...>`）。
7. pre-push 钩子只运行一次必要测试；被拦截就说明有回归，修好再推。

## 目录速览

| 路径 | 作用 |
|---|---|
| `translator.pyw` | 主程序（GUI 只在 `__main__` 下启动，可安全 import） |
| `README.md` / `README.zh.md` | 双语说明文档，**必须成对维护** |
| `tests/` | 单元测试（`_tr.py` 负责按路径把主程序加载为可导入模块） |
| `.githooks/pre-push` | 推送前自动跑测试的钩子（需 `core.hooksPath` 启用） |
| `.gitattributes` | 保证钩子在所有平台保持 LF 行尾，可正常执行 |
| `requirements-dev.txt` | 可选的开发依赖（pytest，仅作更好看的测试运行器） |
| `docs/ROADMAP.md` | 迭代路线图与 backlog |
