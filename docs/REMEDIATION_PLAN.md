# CC Translate — 代码整改计划 / Remediation Plan

> 最后更新：2026-07-22 · 全部改动**仅在本地**，未 commit / 未 push（HEAD 仍为 `f4ce1db`）
> 测试基线：**247 tests，全绿，exit 0**，连续多次稳定。

本文件汇总两份代码 review（外部一份 + 我自查一份）的所有问题、当前状态、以及按优先级排序的下一步。
- `r*` = 外部 review 条目
- `m*` = 我自查发现的条目

---

## ✅ 已完成（本地已改 + 已测）

| ID | 级别 | 问题 | 做法 |
|----|------|------|------|
| **r1** | High | 并发请求状态串扰 + 共享 `tmp_ocr.png` | 引入单调递增 `_job_id`（`_begin_job`/`_job_is_current`）；worker 线程捕获自己的 job_id + StreamSession，所有回 UI/历史的写入先校验 job；OCR 改 per-capture 唯一临时文件 `tmp_ocr_<uuid>.png` |
| **r2** | High | cold stream 无 watchdog/超时/清理，部分输出被当成功 | `_stream_claude` 加 `threading.Timer` watchdog kill；`finally` 关 pipe + kill 子进程；校验终止 `result` 事件，`killed`/`is_error`/非零 returncode 一律判失败（截断输出不再当成功） |
| **r3** | High | 本地 OCR 跑在 Tk 主线程冻结 UI | `_ocr_translate_local` 改在 worker 线程跑 `cc_ocr.ocr_local()`，结果 `after()` 回主线程，带 loading + 硬超时 |
| **r4** | Medium | Tk teardown / `Tcl_AsyncDelete` | 测试崩溃已在上一轮 P0 修复；运行时 diagnostics 用 `queue`+`after(poll)`，本身无 bug → 归为已解决 |
| **r5** | Medium | config/history 非原子写，配 `os._exit` 有损坏风险 | `save_config`/`add_history` 改 `_atomic_write_json`（temp + fsync + `os.replace()`）；history 读改写加 `_HISTORY_LOCK` |
| **r6** | Medium | 快捷方式 PowerShell 引号未转义，失败被吞 | `cc_update._create_shortcut` 全字段走 `_ps_squote()`；失败改 `_log()` 记录而非静默 |
| **r7** | Medium | one-shot / vision 子进程路径无 mock 测试 | 新增 `TestCallClaudeOneShot` / `TestCallClaudeVision`（正常 / 纯文本 / stderr / timeout / bad JSON 等），共 12 个 mock 测试作为重构前护栏 |
| **m2** | Hygiene | 静默 `except` | **审计结论：代码本身已较健康** —— 全库**无裸 `except:`**；68 处 `except: pass` 绝大多数是正当的尽力而为 UI/清理/轮询回调（记日志只会刷屏）。仅给 2 处一次性、非 UI 的路径补了 `log_error`（`clear_history`、`_load_tray_image`），其余保持静默是刻意且正确的 |
| **m5** | Hygiene | 缺运行时 `requirements.txt` | 新建 `requirements.txt`，声明 7 个运行时依赖（pynput / pyperclip / pystray / Pillow / Pygments / winsdk / comtypes）并带经测试的下界；已校验全部 spec 可解析且当前安装版本满足 |
| **m4**（首轮） | Hygiene | 类型注解覆盖率低 | 给 **config / history / subprocess 边界**补注解（`_atomic_write_json`/`save_config`/`load_config`/`load_history`/`add_history`/`clear_history`/`log_error`/`_user_data_path`/`_history_meta`/`_record_history`/`_call_claude`/`_call_claude_vision`/`_stream_claude`/`_humanize_error`）+ 引入 `typing`。纯注解、不改逻辑；compile OK、247 测试全绿。剩余广度仍可渐进补 |
| **r9**（installer 步骤） | Low | 依赖清单两处不同步 | `install.ps1` 改用 `pip install -r requirements.txt`（单一事实来源）；中英 README 补「`-r requirements.txt` 一键装」说明。语法已校验。**注：无法自动测，需你跑一次安装器验证** |
| — | Low | 隐藏的数据竞争（code review 发现） | worker 的 `add_history` 原先读**实时** `self._last_*`，可能配错 input↔output；改为在主线程 `_history_meta()` 快照 + `_record_history()` 落库 |
| — | — | 一个偶发 flaky 测试 | `test_trigger_translates_when_clipboard_updated` 未 mock Win32 焦点探测 → 偶发失败（会污染自动更新 gate）；已 mock 为确定性 |

**新增测试：207 → 247（+40）** — Config + stream 加固 + job 隔离 + 原子写 + 历史快照/竞争 + 快捷方式引号 + one-shot/vision mock（r7，+12）+ `clear_history` 记日志（m2，+1）+ flaky 修复。

**改动文件（均未提交）**：`translator.pyw`、`cc_update.py`、`tests/test_full.py`、`install.ps1`、`README.md`、`README.en.md`（修改）；`diagnostics.py`、`win32util.py`、`requirements.txt`（新增）。

---

## ⏸️ 已延后（需要你本地评审后再动 —— 都不是 bug，是重构/打磨，风险或改动面大）

> 这些之所以没在 autopilot 里直接改，是因为你明确说过「别把 app 搞坏了」。它们要么触及 UI 观感需人眼验证，要么是大范围结构重构最好有人 review，要么要跑安装器才能测。下面给出**具体、可小步执行**的方案，等你在场时推进。

### P2 — 架构（降低未来「屎山」风险）

#### m1 — `TranslatorApp` god class 拆分（~4700 行 / ~150 方法）
**为什么延后**：主类里的常量并非纯数据 —— 91 个模块级常量中有 ~28 个要调用模块函数（`_user_data_path`、i18n 标签构造、`re.compile`、`threading.Lock`、ctypes 包装）。因此想干净地抽 `constants.py`，会**级联**把这些函数也一起搬，属于会牵一发动全身的重构，值得有人 review。

**建议的安全小步（每步跑全测）**：
1. 先抽 `constants.py`：只放**纯数据**常量（布局尺寸、`STREAM_*`、正则、颜色键名等不依赖函数的）；用 `from constants import *` 保持 `tr.<NAME>` 兼容（测试靠这个 patch）。
2. 再抽 `paths.py`：把依赖 `_user_data_path` 的路径常量 + 那几个路径函数一起搬。
3. 用 **mixin 类**拆窗口构建（改动最小、不改方法签名、`self` 照用）：`ui_settings.py` / `ui_history.py` / `ui_diagnostics.py` / `ui_about.py`，各自 `from constants import *`；`TranslatorApp(SettingsMixin, HistoryMixin, ...)` 多继承。
4. 每搬一个 mixin 就跑一次 `discover`，绿了再搬下一个。

#### m6 — ~41 处 `self.root.after` 定时器无集中管理（依赖 m1）
**为什么延后**：上一轮 P0 修复已把 `Tcl_AsyncDelete` 稳住，边际收益下降；且集中化会碰很多定时器调用点。
**方案**：建一个 `_after(ms, cb)` 包装，登记 handle 到 `self._timers`；周期性/一次性都过它；`_shutdown`/窗口销毁时统一 `after_cancel` + drain。可在 m1 铺好模块结构后一次性接入。

### P3 — 卫生 / 打磨

#### m3 — ~63 处内联 hex 颜色绕过 `THEMES`
**为什么延后**：折进 `THEMES` 会改暗色/主题渲染，需人眼验证观感。
**方案**：逐个把 `#RRGGBB` 字面量映射成 `THEMES[...]` 键；每改一批**肉眼过一遍浅色/深色**再继续。

#### m4 — 类型注解覆盖率低（首轮已做，剩余渐进）
**已完成**：config / history / subprocess 边界已注解（见上表）。
**为什么剩余延后**：其余大范围注解价值偏低、易引入注解笔误，适合逐步补而非一次覆盖全。
**方案**：后续接入 `mypy --ignore-missing-imports` 增量收口，按模块补齐。

#### r8 — 流式每帧全量重建 Text widget
**为什么延后**：属 UI 性能优化，要交互式验证超长输出观感，非 bug。
**方案**：流式阶段 `insert` append，仅最后一帧做完整 rich reflow / highlighter。

#### r9 — `install.ps1` 供应链 / 可复现性（installer 步骤已做，剩余发布时收口）
**已完成**：`install.ps1` 改用 `-r requirements.txt`；中英 README 补一键装说明 → 依赖清单单一事实来源。
**为什么剩余部分延后**：`irm|iex`、改 execution policy、pin `@latest` Claude CLI 等属发布流程，改了要真跑安装器才能测。
**方案（发布时做）**：pin Claude CLI 版本；execution policy 改动做成更显式的用户确认；或提供 release 包 / 签名 bootstrap。

---

## 建议的执行顺序（剩余）

1. **m1 + m6** — 架构拆分：按上面 4 个安全小步走，先 `constants.py`/`paths.py` → 再逐个 mixin → 顺带接入定时器登记表。**建议你在场、每步跑全测。**
2. **m3 / r8 / m4（渐进）** — 卫生与打磨，随手推进（m3/r8 记得肉眼验证 UI；m4 继续按模块补注解）。
3. **r9 剩余** — 发布流程收口（pin CLI 版本 / 签名 bootstrap），最后做。

> ⚠️ 纪律：每一步结束都跑 `python -m unittest discover -s tests`，确认 `Ran N tests ... OK` 且 exit 0；全程本地，等你本地测试确认后再 push。
> ⚠️ r9 的 installer 改动无法自动测 —— 需你在本地跑一次 `install.ps1`（或 `CC_TRANSLATE_DRYRUN=1` 预演）确认依赖安装正常。
