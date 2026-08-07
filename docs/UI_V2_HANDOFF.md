# CC Translate — UI v2 状态与恢复手册

> 状态：**UI v2 已设为生产默认；窗口保持不透明渲染**
>
> 暂停日期：2026-08-05；恢复日期：2026-08-06
> 旧 UI 基线提交：`99378ea`（About 窗口最后一轮视觉修正）
> 原始规划：[`UI_V2_PLAN.md`](UI_V2_PLAN.md)

本文记录 UI v2 的真实进度、代码结构、本机功能开关、验证方法、遗留项和后续顺序。
继续 UI 工作时，应先阅读本文，不要仅依赖聊天记录或旧版进度表。

---

## 1. 当前产品状态

UI v2 已完成默认发布：

- 新 UI 是新安装和现有用户升级后的默认界面。
- 旧 dark-launch 配置会一次性迁移到 v2，避免历史自动保存的
  `ui_v2: false` 把普通用户留在 legacy。
- legacy 代码仍保留；`CC_UI_V2=0` 或迁移后的显式 `ui_v2: false`
  可用于诊断回退。
- 如果 Pillow 不可用，即使开关为 true，也会自动走 legacy UI。
- 毛玻璃 Beta 因导致部分本地窗口卡死，已从正式版和设置页移除。

2026-08-06 恢复工作后已完成：

- 固化 `tools/capture_ui_v2.py`，使用合成数据批量抓取真实 Tk 窗口。
- 重新抓取 8 个已完成表面的暗色 / 亮色基线，共 16 张页面图。
- 为设置页额外生成暗色 / 亮色 provider 区域 crop。
- 检查 Provider、Model、翻译方向和 Codex streaming 布局。
- 根据当前语言的最长选项动态计算所有设置下拉框的统一宽度，修复中英文翻译
  方向文案被截断；宽度同时设有高 DPI 上限，窗口最终几何不会超出显示器工作区。
- 将诊断窗口接入 v2 外壳，保留 legacy fallback、异步重检、报告复制和失败重试；
  报告文本区已改为圆角卡片，中英文暗亮主题均已真实抓图验证。
- OCR overlay 使用平滑降亮的桌面预览、正常亮度选区、宽荧光边框和四角节点；
  支持拖动或依次点击两个角，保留 Esc / 右键取消和 legacy fallback。
- OCR 使用用户确认的预览帧，不会在确认后重新截取变化中的内容；真实桌面预览窗口
  必须成功启用 Windows capture exclusion，否则安全回退 legacy overlay。
- About 下的 Support-author 和卸载确认次级窗口已接入 v2 外壳，同时保留
  legacy fallback。Support-author 使用原二维码资源；卸载确认使用明确的危险按钮，
  默认聚焦取消操作，并保留数据开关支持键盘和原生复选状态。
- 抓图工具已纳入这两个次级窗口；中文暗亮主题和卸载英文长文暗亮主题均已真实抓图，
  截图 fixture 不会执行真正卸载。
- 抓图工具会在进程内强制 v2、固定开机启动状态和历史日期，并按所选语言生成标题；
  宿主环境变量和本机状态不会污染基线。
- 当前抓图环境为约 200% DPI；图片和 manifest 保存在 session artifact
  目录，不提交用户数据或截图到仓库。

暂停前最后处理的是 About 窗口：

- 窗口改为宽幅横向布局。
- Logo、名称渐变、版本按钮和底部三个操作按钮已经重排。
- 版本绿点已完全移除光晕，只保留超采样抗锯齿的实心圆点。
- 信封和咖啡图标增加了光学垂直微调。
- 顶部、Logo、描述、版本和按钮之间的纵向间距已重新配平。
- 暗色和亮色都使用真实 Tk 窗口抓图检查。
- 当时完整测试为 **431 tests passed**。

---

## 2. 当前页面完成度

旧 `UI_V2_PLAN.md` 中的进度表已经落后，以下表格以当前代码为准。

| 页面 / 表面 | 当前状态 | 主要入口 |
|---|---|---|
| 加载中 / 翻译中 / 解释中弹窗 | ✅ v2 已实现 | `cc_app_popup.py::_make_loading_popup` |
| 翻译结果弹窗（含流式、错误态） | ✅ v2 已实现 | `cc_app_popup.py::_make_popup` |
| 结果页 Actions 下拉菜单 | ✅ v2 已实现 | `cc_app_results.py` |
| 快速翻译窗口 | ✅ v2 已实现 | `cc_app_quickinput.py::_open_quick_input` |
| 历史记录窗口 | ✅ v2 已实现，卡片式布局 | `cc_app_history.py::_open_history` |
| 设置窗口 | ✅ v2 已实现 | `cc_app_settings.py::_open_settings` |
| 关于窗口 | ✅ v2 已实现，暂停前最后精修 | `cc_app_about.py::_open_about` |
| 诊断窗口 | ✅ v2 已实现 | `cc_app_diagnostics.py::_open_diagnostics` |
| OCR 截图区域选择器 | ✅ v2 已实现，保留安全 legacy fallback | `cc_app_ocr.py::_open_region_selector` |
| 请作者喝咖啡图片窗口 | ✅ v2 已实现，保留 legacy fallback | `cc_app_about.py::_open_support_author` |
| 卸载确认窗口 | ✅ v2 已实现，保留 legacy fallback | `cc_app_about.py::_confirm_and_uninstall` |
| 托盘菜单 / 系统通知 | 🚫 系统原生，不纳入皮肤 | `cc_app_tray.py` / `cc_app_update.py` |

所有计划内应用窗口已经完成；托盘菜单和系统通知继续使用系统原生表面。

---

## 3. 功能开关是怎么实现的

### 3.1 配置键与默认值

定义在 `cc_core.py`：

```python
class CFG:
    UI_V2 = "ui_v2"


DEFAULT_CONFIG = {
    CFG.UI_V2: True,
}
```

生产默认值是 `True`。`Config` 使用 `ui_v2_default_migrated` 做一次性迁移：
旧配置中由 dark launch 自动保存的 false 会升级为 true；迁移后再显式设置的 false 会保留。

### 3.2 本机环境变量

环境变量名：

```text
CC_UI_V2
```

解析优先级：

1. `CC_UI_V2=1/true/yes/on`：强制启用 v2。
2. `CC_UI_V2=0/false/no/off/空字符串`：强制 legacy。
3. 环境变量是未知值：忽略，继续读取配置。
4. 读取 `%APPDATA%\CC Translate\config.json` 中的 `ui_v2`。
5. 都没有时使用默认值 `True`。

对应逻辑在：

```text
cc_core.py
  CFG.UI_V2
  UI_V2_ENV
  ui_v2_enabled(cfg)
```

### 3.3 页面实际判定

页面不直接只看配置，而是通过共享判定：

```python
self._v2_popup_on()
```

实际条件是：

```text
ui_v2_enabled(self.cfg) AND cc_ui_v2.PIL_OK
```

也就是说：

- 开关打开 + Pillow 正常：使用 v2。
- 显式关闭：使用 legacy。
- Pillow 导入失败：安全降级 legacy。

共享判定和主题适配主要位于 `cc_app_popup.py`。

### 3.4 页面接入模式

各窗口遵循同一原则：

```python
v2on = self._v2_popup_on()

if v2on:
    # v2 shell / v2 controls / v2 layout
else:
    # 原 legacy 路径
```

有些页面共享同一套业务和 Tk 控件，只替换外壳、配色和布局；不是每个页面都复制一整套逻辑。
这样关闭功能开关后，legacy 仍然保留。

---

## 4. 本机如何启用、关闭和恢复默认

以下命令均在 PowerShell 中运行。

### 4.1 仅本次启动启用 v2

```powershell
Set-Location 'C:\Users\skylerc\cc-translate'
$env:CC_UI_V2 = '1'

Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -in 'python.exe', 'pythonw.exe' -and
    $_.CommandLine -match 'translator\.pyw'
  } |
  ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
  }

Start-Process `
  'C:\ProgramData\Tools\Python\3.12.10\x64\pythonw.exe' `
  -ArgumentList 'translator.pyw' `
  -WorkingDirectory 'C:\Users\skylerc\cc-translate'
```

该环境变量只存在于当前 PowerShell 及其启动的 App 进程中。

### 4.2 仅本次启动强制 legacy

把上面命令中的：

```powershell
$env:CC_UI_V2 = '1'
```

改成：

```powershell
$env:CC_UI_V2 = '0'
```

然后重新启动 App。

### 4.3 跨登录持久启用 / 关闭

```powershell
setx CC_UI_V2 1
```

持久强制 legacy：

```powershell
setx CC_UI_V2 0
```

`setx` 不会改变已经运行的 App，也不会修改当前 PowerShell 的环境；需要新开终端并重启 App。

### 4.4 删除环境变量覆盖，恢复配置 / 默认优先级

```powershell
[Environment]::SetEnvironmentVariable('CC_UI_V2', $null, 'User')
Remove-Item Env:CC_UI_V2 -ErrorAction SilentlyContinue
```

随后重启 App。由于当前默认配置是 `False`，没有手动修改 `config.json` 时会回到 legacy。

### 4.5 直接使用配置文件

配置文件通常位于：

```text
%APPDATA%\CC Translate\config.json
```

可以加入：

```json
{
  "ui_v2": true
}
```

但开发阶段更推荐使用 `CC_UI_V2`，因为它不会污染用户配置，而且能明确覆盖配置值。

---

## 5. UI v2 的代码架构

### 5.1 `cc_ui_v2.py`：纯渲染层

该文件是 v2 的视觉引擎，主要使用 Pillow 生成 RGBA 图像，再转成 Tk `PhotoImage`。

主要职责：

- 暗色 / 亮色 palette。
- 品牌渐变、文字渐变、圆角 mask。
- 窗口卡片和边缘线烘焙。
- Logo、发光、状态点。
- soft pill、gradient pill、ghost icon button。
- 输入框背景、边框和 focus 状态。
- DPI 缩放和字体加载。
- 流式窗口背景的高度稳定裁切。

关键原则：

- v2 不是依赖系统原生 ttk 主题，而是把复杂视觉效果预先“烤”进图片。
- Pillow 是可选依赖；不可用时必须允许 legacy 继续工作。
- 最终窗口圆角使用二值硬 mask，避免 transparent-color 边缘出现半透明颗粒。
- 窗口边线采用与底色接近的同色相，只做明度变化，不使用彩虹边框。
- 所有正式窗口使用稳定的不透明表面。

### 5.2 `cc_app_popup.py`：Tk 与窗口外壳集成

共享 v2 集成主要放在 `PopupMixin`：

- `_v2_popup_on()`：统一功能开关。
- `_v2_palette()` / `_v2_window_theme()`：把 Pillow palette 转成 Tk 可用颜色。
- `_rounded_shell_v2()`：颜色键透明圆角、Canvas 外壳和内容 frame。
- `_v2_photo()`：按主题、尺寸和状态缓存 `PhotoImage`。
- `_v2_brand_header()`：共享品牌标题区域。
- `_v2_soft_button()` / `_v2_ghost_button()`：共享控件。
- `_v2_hero_logo()`：About 大 Logo。
- `_v2_field_photo()`：快速翻译等输入框背景。
- `_reveal_rounded_window()`：窗口完成测量和绘制后再显示，减少首帧闪烁。

各 mixin 调用这些共享能力，而不是重复实现边框、缓存和 hover。

### 5.3 业务逻辑与皮肤的边界

以下逻辑原则上不属于 `cc_ui_v2.py`：

- 翻译调用和流式数据。
- 历史记录读写。
- 设置保存。
- 更新、卸载和诊断业务。
- OCR 处理。

v2 只应改变表现，不应复制或改变这些业务逻辑。恢复工作时继续遵守这个边界，避免出现
“legacy 一套业务、v2 又一套业务”的双重维护。

---

## 6. 当前设计语言

### 6.1 品牌与背景

品牌主渐变：

```text
蓝色 → 紫色 → 粉色
(110,168,255) → (161,121,255) → (255,122,198)
```

About 名称使用更明显的紫 → 亮粉水平渐变：

```text
(150,105,255) → (255,94,178)
```

暗色底色已经降低紫色饱和度，偏中性 charcoal/navy；亮色为接近白色的浅灰。

### 6.2 边缘与圆角

- v2 统一圆角约为 24 design px。
- 外边线约 1px。
- 边线色相接近窗口底色，只通过明度变化形成轻微发光感。
- 圆角透明区必须保持干净，不能出现半透明颗粒或单独像素。

### 6.3 控件

- 常用按钮使用 30 design-point 高度的 soft pill。
- 主操作可以使用蓝 → 紫 → 粉渐变。
- caret、check 等简单图形尽量自己绘制，避免字体缺字产生 tofu 方块。
- MDL2 icon 不能只相信字体 bbox；必要时使用 per-icon 光学偏移。
- 输入框和筛选器保持统一高度、圆角、左侧内边距和对齐线。

### 6.4 布局原则

暂停前通过多轮真实截图形成的约束：

- 不为“精致感”堆大量小 padding 或嵌套缩进。
- 同一列控件共享同一左右边界，不允许无原因的 2–3px 内缩。
- 先对齐容器，再检查首项是否因为 list padding / scroll offset 偏移。
- footer 与主体间距、主体与窗口底边间距必须一起判断。
- 几何居中不一定等于视觉居中，图标和文字需要检查真实墨迹重心。
- 不能只靠单测宣布视觉完成。

---

## 7. 真实窗口验证方法

### 7.1 为什么不能只看单测或 PIL 预览

过去已确认：

- 单元测试能证明渲染函数和尺寸逻辑没坏。
- 手工合成的 PIL 预览不能证明真实 Tk 的 pack、字体、DPI、颜色键透明和 HWND 渲染正确。
- `ImageGrab.grab` 对 borderless / layered / transparent-color Tk 窗口可能抓不到正确结果。

因此 UI v2 的视觉验收必须使用真实窗口。

### 7.2 已验证可行的抓图方式

在**创建 Tk 窗口的同一 Python 进程**中：

1. 打开真实窗口。
2. `update_idletasks()`、`update()`，等待窗口完成渲染。
3. 从 `winfo_id()` 取得 HWND，并通过 `GetAncestor(..., GA_ROOT)` 找到顶层 HWND。
4. 使用 Win32 GDI：
   - `GetWindowDC`
   - `CreateCompatibleDC`
   - `CreateCompatibleBitmap`
   - `PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)`
   - `GetDIBits`
5. 用 Pillow 按 `BGRX` 转成 PNG。
6. 暗色和亮色各抓一张。
7. 对圆角、状态点、按钮、图标和文本 baseline 做局部放大。

固定的开发抓图工具现位于：

```text
tools/capture_ui_v2.py
```

它使用合成内容构造真实 Tk 窗口，不启动热键、托盘或模型进程，不读取剪贴板和用户历史。
默认输出到系统临时目录；也可通过 `--output-dir` 写入 session artifact 目录。示例：

```powershell
python tools\capture_ui_v2.py --surface all --theme both
```

抓取设置页时还会额外生成 provider / model 区域 crop，便于模型架构变化后快速复核。

### 7.3 每个页面至少检查

- 暗色 / 亮色。
- 100% / 当前 200% DPI；有条件再测 125%、150%。
- 四个圆角和 transparent-color 颗粒。
- 首帧是否闪烁。
- 控件左右边界和纵向 baseline。
- hover、focus、pressed。
- 长文本、滚动和窗口 resize。
- 流式更新期间是否出现背景接缝。
- 切换主题后是否因为 PhotoImage cache 使用旧主题。

主题切换抓图时要清空或按主题区分 `_v2_photo_cache`。

---

## 8. 自动化测试

主要测试：

```text
tests/test_ui_v2.py
  纯渲染：palette、缩放、渐变、mask、卡片、按钮、输入框。

tests/test_ui_v2_popup.py
  Tk 集成：结果窗口、流式、快速翻译、历史、设置、诊断、OCR overlay。

tests/test_full.py
  功能开关、窗口构建 smoke、About 等通用行为。

tests/test_capture_ui_v2.py
  抓图参数、合成数据、输出目录、设置页 crop 和窗口夹具状态。
```

运行：

```powershell
Set-Location 'C:\Users\skylerc\cc-translate'
Remove-Item Env:CC_UI_V2 -ErrorAction SilentlyContinue
& 'C:\ProgramData\Tools\Python\3.12.10\x64\python.exe' `
  -m unittest discover -s tests
```

注意：

- 跑 flag-off 测试前要移除 `CC_UI_V2`，否则环境变量会强制 v2，导致 legacy 断言假失败。
- 缺 Pillow、缺 Tk 或没有显示环境时，部分 GUI 测试可能跳过。
- 全绿不代表视觉通过；仍必须执行第 7 节真实窗口抓图。
- UI 回归按本次触及的表面执行；完全未修改的窗口无需重复运行视觉或窗口集成回归。

---

## 9. 已知遗留与风险

### 9.1 明确未完成

- 暂无。

### 9.2 需要持续关注

- Pillow 缺失时的 legacy 降级。
- 颜色键透明窗口在不同 DPI 下的圆角边缘。
- 主题切换后的 PhotoImage cache。
- 流式窗口动态增高时的背景接缝。
- GUI 测试在无显示环境中的跳过。
- About 主窗口及两个次级窗口均有真实窗口 smoke；次级窗口另有 v2 / legacy、
  安全卸载 fixture、默认状态和键盘路径的专用断言。

### 9.3 文档债务

- `UI_V2_PLAN.md` 的旧进度表不再代表当前状态。
- `cc_ui_v2.py` 文件头部部分“仅结果页接入”等描述可能已经过时。
- 恢复 UI 工作时应同步修正文档和文件注释。

---

## 10. 后续 UI 工作建议顺序

### Step 1 — 建立新基线（✅ 2026-08-06 完成）

1. 确认模型 provider 改造已经稳定，不要同时大改模型主链和 UI。
2. 阅读本文、`UI_V2_PLAN.md` 和最新相关提交。
3. 检查工作树，避免覆盖 provider 分支上的未提交变更。
4. 运行完整测试。
5. 用 `CC_UI_V2=1` 启动真实 App。
6. 重新抓取所有已完成页面的暗色 / 亮色基线。

### Step 2 — 使用固化的视觉验证工具（✅ 2026-08-06 完成）

运行 `tools/capture_ui_v2.py` 建立基线。该工具支持页面、主题、语言和输出目录参数，
并对设置页 provider 区域生成放大 crop；输出保持在临时/session 目录，不提交截图。

### Step 3 — 修复回归，而不是立刻做新页面（✅ 设置页完成）

模型 provider 改造可能会改变：

- 设置页模型区域高度。
- 诊断页内容。
- 加载 / 错误文案长度。
- OCR 状态和错误态。

应先保证已完成 v2 页面在新模型架构下仍然对齐，再继续新页面。

### Step 4 — 计划内表面已完成

推荐顺序：

1. **诊断窗口**（✅ 2026-08-06 完成）
   已接入 v2，并保留多 provider 报告、异步刷新、复制和重试行为。
2. **OCR overlay**（✅ 2026-08-06 完成）
   已完成平滑降亮、正常亮度选区、荧光边框、安全预览帧和两点点击路径。
3. **卸载确认和 Support-author 次级窗口**（✅ 2026-08-06 完成）
   已保留原业务与 legacy fallback，并完成中英文、暗亮主题真实抓图。
4. **下一项：评估是否公开 UI v2 开关。**

### Step 5 — 每页完成标准

每个页面必须同时满足：

- legacy 路径仍可用。
- v2 暗色和亮色真实抓图通过。
- 两轮细节自审：对齐 / 间距 / 圆角 / 图标 / 状态。
- DPI 和长文 / 错误态检查。
- 自动化测试通过。
- 无一次性抓图脚本和临时 crop 遗留在仓库；统一使用固定工具，图片输出到
  临时/session artifact 目录。
- 一页一个可回滚提交。

---

## 11. 与模型 provider 计划的关系

当前优先执行：

```text
docs/GPT_PROVIDER_PLAN.md
```

模型改造时应注意以下 UI 接口，避免未来恢复 v2 时出现大范围返工：

- 设置页不要把 provider / model 控件只写进 legacy 分支。
- 加载和错误状态继续走现有共享 popup API。
- provider 错误应先归一化成业务状态，再交给 UI 渲染。
- 诊断数据层与诊断窗口表现层分离。
- OCR 的 Claude/GPT 差异留在 provider，不要复制两套 overlay。
- 不删除 `_v2_popup_on()`、v2 helper 或 legacy 分支。

在 GPT provider 完成前，UI v2 只做阻塞性 bug 修复，不继续视觉扩张。

---

## 12. 快速恢复清单

未来重新开始 UI v2 时，可以直接按此清单：

- [x] 阅读 `docs/UI_V2_HANDOFF.md`
- [x] 阅读 `docs/UI_V2_PLAN.md`
- [x] 确认 provider 改造状态和工作树
- [x] 运行 UI v2 聚焦测试
- [x] 使用真实 Tk 构建器批量打开 v2 表面
- [x] 抓暗色 / 亮色全页面基线
- [x] 固化 PrintWindow 工具
- [x] 修复 provider 引入的设置页方向文案截断
- [x] 完成诊断 / OCR v2
- [ ] 继续 About 次级窗口
- [ ] 每页真实抓图 + 自动化测试 + 独立提交
