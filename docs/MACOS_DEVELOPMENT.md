# macOS 原生客户端开发指南

状态：P0 真实 Mac 自动化工程门槛已通过，P1 纯核心并行开发；首次实机/签名门槛未验收。最低版本暂定 macOS 14，
Apple Silicon 优先；Intel 只有独立构建及实测通过后才承诺支持。
进度与证据以 [MACOS_TODO.md](MACOS_TODO.md) 为准。

## 1. 产品边界与开发方式

目标是保留 Windows 产品能力的原生 Mac App，不是 Tk 换皮、WebView 主界面或一次性全 Swift 重写。
Windows 原入口继续工作；不将 macOS 半成品接入 Windows 安装、更新或发布通道。
术语表、风格预设、系统词典及 Apple Translation 是独立后续方向，不捆绑此次移植。

开发者可在 Windows 编码和测试便携核心。原生编译、链接、资源、GUI、TCC 和签名必须在真实
macOS 环境验证。推荐经授权后使用云端 macOS CI，用户 Mac 只承担 CLI 登录、授权和两轮集中验收。
最终发行目标是用户安装运行不需要 Xcode、Python 或 Git；开发机器/CI 则需要 Xcode 和构建用
Python。当前未签名开发包不满足普通用户双击即用条件，首轮测试路径见下方实机交接。
Codex/Claude CLI 与用户账号仍是外部前提；不是所有安装方式都需要 Node 或 Homebrew。

当前已获准提交并正常推送唯一开发分支 `agents/cc-translate-macos-native`，
使用项目公有仓库的标准免费 macOS runner 验证。真实 run/SHA 和结果写入验收清单；
工作流存在或提交成功本身不代表 CI 通过。不发布、不推送/合并正式分支、不部署覆盖 Windows 应用、
不购买额度或启用收费大机器。不配置签名私钥、不要求在聊天粘贴凭据、不绕过 Gatekeeper。
标准免费 arm64 runner 已实际验证；尚待确认验收 Mac 的 OS/CPU、Developer ID、
真实 CLI/账号和远程 GUI 条件。

## 2. 架构和目录责任

```text
macos/
  Package.swift             SwiftPM 原生可执行目标与可测试支持库
  Sources/                  SwiftUI + AppKit；协议、原生探针与进程适配
  Tests/                    XCTest 协议/状态机/原生探针测试
  Resources/                Info.plist 等显式打包资源
cc_macos/
  __main__.py               私有 stdin/stdout helper；只启动无界面服务
  protocol.py               有界、版本化 NDJSON 校验
  server.py                 握手、请求、事件序号、取消和 EOF
  probes.py                 SQLite / SSL 等显式运行时自检，不获取 TCC
cc_classify.py               P1 共用本地分类/词典触发判断；仅依赖 re，无平台/数据路径副作用
cc_direction.py              P1 共用方向路由/方向提示词；UI 语言由调用方显式传入
cc_prompts.py                P1 既有文本提示词及独立 provider revision；无导入副作用
cc_providers/base.py         冻结请求/结果/状态契约；纯导入不加载 CLI
cc_providers/registry.py     显式注册/获取/退出的纯 registry
cc_dictionary_store.py      显式路径的只读 SQLite/原 schema；无默认用户词库加载
tools/macos/                锁定运行时、组装 .app、静态制品检查及 smoke
tests/test_macos_*.py       使用仓库现有 unittest，直接导入便携模块
.github/workflows/         仅 macOS 开发工作流，与 Windows 发布隔离
```

- SwiftUI：设置、历史、关于、管理和诊断；AppKit：NSStatusItem/NSMenu、
  NSPanel、原生文本、鼠标键盘与焦点。异步 UI 更新回主线程，按稳定 identity 更新来源按钮。
- 原生系统适配：AX 三态选区、快捷键、NSPasteboard、ScreenCaptureKit、Vision、
  URLSession、SMAppService，未来 Sparkle。Python 不监听系统事件，不申请权限。
- Python：逐步复用分类、方向、提示词、词典、缓存、历史、provider 生命周期与请求快照。
  P0 只提供明确标注的合成 fixture 和诊断，绝不把 fixture 当真实翻译。
- `translator.pyw`、`cc_core.py` 和 UI mixin 不是服务入口。导入便携核心不能导入 Tk/Win32，
  不能创建/迁移 AppData。`cc_providers` 现在只直接导入纯 base/registry；
  CLI 后端导出在显式访问时才加载，Windows 仍获得原类/函数对象，导入失败原样传播。
  Mac 开发包只保留该包的 `__init__.py` / `base.py` / `registry.py`，不携带旧 CLI 后端。
  不能据此宣称原 Windows Codex/Claude 后端已适配 Mac；其 POSIX 监督及真实 native 配置/
  账号验证仍在 P1。每次抽取保留兼容导出并跑 Windows 回归。
- SwiftPM 是 P0 最小可重复编译入口，不引入工程生成器。发行 Bundle/资源由独立脚本组装；
  后续需要 XCUITest 时可增加 Xcode 测试宿主，不以未经编译的大量 UI 替代平台探针。

P1 首个切片将既有本地分类直接移到 `cc_classify.py`，Windows 主入口保留兼容导出，
不复制第二套规则。该模块随包放入 `Resources/Core` 并纳入资源哈希和必需文件审计。
便携回归直接导入模块；Windows 另验证函数 identity；Mac CI 用包内 isolated Python
执行同一分类矩阵/无副作用测试。此切片尚未增加业务 IPC、真实翻译或 provider 调用。
第二个切片将方向目录、路由和方向提示词移入 `cc_direction.py`，保留 `cc_core` /
`translator.pyw` 的同一对象兼容导出；依赖 i18n 的界面标签 wrapper 留在 Windows 层。
函数、阈值、提示词以及未知 mode 的既有回退保持不变，不增加模型请求，不改缓存签名。
两个纯模块一起随包、进行资源哈希审计，并在包内 isolated Python 中运行同一组回归。
`is_single_word` 也复用该分类模块，不另建抽象；`cc_core`、Windows 主入口和结果操作继续
导出/调用同一函数。本地词典候选、AI 词典模式、摘要排除和历史标记的现有语义不变。
这次只解耦，不重新设计旧启发式对特殊符号/空白的判断；边界行为用回归用例固定。
静态文本提示词目录随后抽到 `cc_prompts.py`，12 个赋值（包含 provider/词典补充 revision）
与旧实现 AST 和规范 UTF-8 快照完全一致。`cc_core` / Windows 主入口 / warm / 结果操作
继续使用相同对象，不增加 API、预热 turn、重试或账号访问。三份纯模块一起随包验证。
OCR 专属文案、动态摘要提示词组装、请求快照和平台数据路径仍是另外的边界，不宣称全部 P1 完成。
已有 `ProviderRequest` / `ProviderResult` 等数据类和 `ProviderRegistry` 也能在隔离环境直接
导入；类字段、冻结语义、未知认证状态和 registry 退出错误传播均保持原样。
这只完成纯契约的初始化边界，不是完整请求快照/配置版本协议或可运行的 Mac provider。
包内 `__all__` 保留兼容 API 名称，但未提供的 CLI 后端访问会显式导入失败，不降级到其他后端。
`DictionaryStore` 保持原有线程局部连接和 `close_thread` 契约；SQLite URI 使用原生 `Path.as_uri`，
保留 POSIX 文件名中的字面反斜杠并正确转义空格/`#`/`%`。原 builder-v3 DDL 移到同一模块，
builder 仍导出同一个 `SCHEMA`，表/索引/来源 identity/许可字段和数据版本不变。
显式 `runtime_probe` 现在还在自己的临时目录创建合成词典，验证 exact/form/alias、只读约束、
来源保留、关闭重开及文件不变；跨线程独立关闭由同源测试覆盖。报告只含固定状态/布尔值，
不返回路径、词条或用户资料；没有捆绑真实词库或提供词典 UI，也不是动态换库/全局 writer 协议。

## 3. IPC v1 合同

原生通过 Foundation.Process 启动包内运行时；只有私有 stdin/stdout，无监听端口。
运行时使用 isolated 模式，helper 使用明确的 bundle 资源路径，不搜索用户 Python 包。
原文只在管道载荷中传输，不放参数或普通日志；stderr 仅固定脱敏错误码。

UTF-8 NDJSON，每帧必须以 LF 结尾。上限 **65,536 字节（含 LF）**。拒绝 BOM、空行、
无效 UTF-8/JSON、重复 JSON 键、非有限数、过深嵌套、不完整 EOF 和未知 envelope 字段。
客户端 envelope：`{"v":1,"id":"...","type":"...","payload":{...}}`。
服务端另含 `seq`，每个 ID 从 0 严格递增。ID 为 1–64 个 ASCII 字母/数字/下划线/短横线，
同一连接不复用；最多 4096 个控制/业务 ID，达到限额显式关闭并建立新会话，不自动重放请求。

| 客户端 type | payload | 服务端结果 |
|---|---|---|
| `hello`（必须第一帧） | `{}` | `ready`：protocol、capabilities、max_frame_bytes、fixture=true |
| `request` | `operation` 为 `fixture` 或 `runtime_probe` | `accepted`，零或多次 `delta`，恰好一个终态 |
| `cancel` | `request_id` 为目标 ID | 控制 ID 上 `completed`（cancel_requested）；目标若仍运行则 `cancelled` |
| `shutdown` | `{}` | 取消本连接工作，控制 ID 上 `completed`，退出 |

`fixture` 接受 `text`（最多 8192 UTF-8 字节）和可选 `delay_ms`（0–2000），
产生明确合成标识的 `delta`，最终 `completed`。`runtime_probe` 接受可选布尔 `https`；
默认不联网；显式 HTTPS 自检只访问固定公开测试端点，不包含原文、账号或截图。
`accepted.payload` 包含 `operation`；`delta.payload` 包含 `text`、`fixture`；
`completed.payload` 包含操作结果；`failed.payload` 只包含固定 `code`；`cancelled.payload` 为空。
运行时结果有 `python / sqlite / ssl / https` 四个对象。`python` 报告版本、平台、架构、
isolated/禁写字节码/是否从 bundle runtime 运行；不输出本机绝对路径。`ssl` 的 context
通过不等于实际 TLS 通过；只有 `https.status == passed` 且证书验证开启才算联网探针完成。
未来 P1 增加 `local_result`、配置版本和真实 provider 能力时同步更新合同及测试。
P0 没有业务配置/历史写操作，不能将无配置版本的探针当作最终业务协议。

最多 4 个并行任务，超限在该请求上返回 `failed/busy`。取消只作用于目标请求；
控制请求的完成不等于模型取消成功。每个业务请求恰好一个终态；完成与取消竞态由核心串行决定。
前端保留每 ID 序号验证，切换当前请求后忽略旧请求的 UI 结果；未知 ID/乱序为协议错误。
握手超时、异常退出或协议错误要可见，禁止 success-shaped fallback。原生持续排空两个输出管道，
有界读取，避免 pipe 堵塞；退出关闭 stdin，超时仅终止本 App 的 helper。
P0 helper 不创建 CLI 子进程。原生显式 `--version` 探针的 P1 监督切片用
`posix_spawn` 原子创建独立进程组，在正常退出、取消、超时和输出超限后清理同组后代；
TERM 宽限后升级 KILL。先用 `waitid(WNOWAIT)` 保留 leader，再发最后一个组信号、reap，
避免 PID/PGID 复用误伤。只对自己创建且尚未回收的组发信号，不按名称搜索/结束用户 CLI。
这不是完整 ProviderRuntime；主动 `setsid`/改进程组逃逸的 wrapper 不支持，也不跨组追杀。
无法确认子进程所有权时显式失败且不再发信号；系统无法完成清理时显示失败并保留回收责任，
不报告成功。Windows provider 未改；实际 native 配置/认证和模型请求仍待单独实现与验证。

协议结构性错误为连接级失败（保留 ID `protocol`），取消所有本连接任务并退出非零。
完整帧后的正常 stdin EOF 取消工作并退出；残缺帧 EOF 为错误。原生不得自动重发已提交的付费请求。
预热不发送文本、不创建付费 turn。

## 4. P0 原生探针和安全行为

普通启动只显示菜单栏图标，不弹窗、不申请权限、不联网、不探测用户选区。
用户通过显式菜单打开 P0 面板、运行合成 IPC、运行时自检或平台探针。
结果窗口与快速输入是最小探针界面，不是已完成产品 UI。

### 权限、选区、快捷键和剪贴板

- 选区建模为 `present / absent / unknown`。AX attribute 不支持、权限拒绝、Secure Input、
  目标失焦/退出均不能解释成“确认没有”。unknown 不读旧剪贴板。
- AX 只读取选中的文本，不读取整个控件 value；探针从菜单切换焦点前固定目标 PID。
- 双击 Cmd+C 只观察用户事件，不吞正常复制，不写哨兵，不模拟复制，不撤销主动复制结果。
  探针可保守仅支持 AX；P2 再引入与本次目标/时间/changeCount 可证明关联的复制回退。
- 全局事件监听与 AX 授权分别报告；不以 AX trusted 推断所有权限已允许。
  按需请求辅助功能/输入监控/屏幕内容；通知和登录项是另外的状态，P0 不请求。
- Secure Input 下停用相关能力，不绕过。纯文本粘贴是后续显式操作。
- 临时剪贴板事务只保守恢复；`changeCount` 不是原子 CAS。多格式、延迟数据、
  Universal Clipboard 和更新系统的 ask/allow/deny 单独验收。P0 不写剪贴板。
- 浮窗不激活并不等于无法接收键盘；快速输入主动激活、结果展示不抢焦点分别验证。

### 截图与 OCR

macOS 14 使用 ScreenCaptureKit 的 filter/configuration API，不误用 15.2 区域 API。
先显式取得权限，再捕获一次、展示同一帧预览，用户确认后 Vision 只识别保留的 CGImage；
不在确认后重新截图、不自动发送到模型。取消/关闭销毁持有的图像，不持久化真实屏幕。
P0 可只选一个显示器；跨屏框选与坐标/像素/缩放转换在 P3 独立实现并验收。
Vision 查询实际支持语言，OCR 本地执行不意味着后续翻译离线。

### CLI 和运行时

Finder 不继承终端 shell profile。只检查明确可执行路径（原生安装、用户配置、
常用 Homebrew/npm 目录），显示未找到/不可执行；不执行 shell profile，不扫描凭据。
P0 的 `--version` 是显式无模型调用探针；路径找到不等于已认证。
Node wrapper 需正确 PATH，但不能据此宣称每个人都需要 Node。
Codex native 配置/认证、工具禁用、hook 检查、目录策略、严格事件解析及请求快照保持不变。
Claude 仍耦合，列入 P1 单独适配；P0 不做 `exec` 假兼容、不静默切账号/模型。

## 5. 数据和版权

核心是配置/历史的唯一写入者。原生 UI 通过协议修改；仅窗口等展示偏好使用 UserDefaults。
Application Support 放持久配置/历史/词典，Caches 放可重建缓存和私有临时文件；
日志限长、脱敏。平台路径显式传入或通过平台 API 获取，不回落源码或签名 bundle。
P0 SQLite 探针使用临时目录并关闭、删除，不创建业务资料。

Mac 词典下载由 URLSession 完成，核心校验固定哈希、来源、版本并原子安装；
不同时启动 Python 下载器，不让原生端覆盖正在查询的数据库。
继续携带 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES) 和
[完整词典许可证](../data/dictionary/licenses)，即使词典按需下载也保留来源入口。
不提取或重分发系统词典，不捆绑用户 CLI 配置/认证。

Python/其构建中 OpenSSL、SQLite 等组件、必要第三方包以及未来 Sparkle 的完整许可和来源
必须随包。运行时制品需固定 URL/version/SHA-256、生成来源清单并拒绝缺失许可证的输入。
应用自身授权条款尚待确认，第三方 notices 不代替应用许可；确认前不对外发布。
P0 不增加 Windows runtime requirements，不全局安装工具。

## 6. 可重复构建与验证

### Windows 可做

在专用工作树运行现有标准库 runner（无须安装 pytest）：

```powershell
python -B -m unittest tests.test_macos_protocol tests.test_macos_bundle tests.test_privacy_scan
git diff --check
```

后续抽取业务模块时把它们原有测试加入同一 unittest 调用；只有涉及集成入口/共享行为时
升级到完整 Windows 回归。测试不应依赖用户配置、网络、真实 CLI 或 Tk。
不得把 Windows 测试通过写成 Mac 通过。
分类切片的针对性命令为 `python -B -m unittest tests.test_classify tests.test_classify_import tests.test_classify_windows tests.test_macos_bundle tests.test_macos_protocol`。
方向切片追加 `tests.test_direction tests.test_direction_windows tests.test_full.TestDirectionModes tests.test_full.TestSummaryHelpers`；
Mac 只运行不依赖 Windows 入口的分类/方向/隔离用例。

### macOS 开发/云环境

1. 经授权协调推送开发分支；确认 runner 价格/额度、CPU、Xcode 实际可用版本。
2. 固定 macOS runner/Xcode/arm64，验证 `uname -m`、Xcode 与 SDK 信息并保存日志。
3. `swift test --package-path macos`；便携 Python unittest；构建 release 可执行文件。
4. 锁定并校验独立 Python 运行时；组装 `.app`，验证所有 Mach-O 的 CPU、最低系统、
   install name/rpath、外部动态库依赖、资源与许可位置。只允许系统或包内链接。
5. 在组装包内运行 helper，实际 SQLite 建表/写入/读取/删除与 HTTPS 证书校验；
   最小进程集成测试确认握手、fixture、EOF；不得仅 import SSL/SQLite。
6. 保存构建、测试、制品检查报告。没有 GUI/TCC 时该层明确未执行，不把 skip 算通过。

当前可执行入口（在 Mac 开发环境的仓库根目录运行，不要求用户 Mac 安装这些工具）：

```sh
export DEVELOPER_DIR=/Applications/Xcode_16.4.app/Contents/Developer
export MACOSX_DEPLOYMENT_TARGET=14.0
python3 -B -m unittest tests.test_macos_protocol tests.test_macos_bundle
/usr/bin/xcrun swift test --package-path macos --triple arm64-apple-macosx14.0
python3 tools/macos/bundle.py build --development
CC_TRANSLATE_APP="$PWD/tools/macos/.build/CCTranslateMac-P0.app" \
  /usr/bin/xcrun swift test --package-path macos --triple arm64-apple-macosx14.0 \
  --filter HelperIntegrationTests
python3 tools/macos/smoke.py --allow-https
python3 tools/macos/bundle.py verify
```

开发制品输出到 `tools/macos/.build/CCTranslateMac-P0.app`。构建拒绝覆盖已有 App，
尤其不能原地修改已签名制品；重复构建先归档/移走自己生成的开发输出。
`inspect` 下载固定输入并只做静态检查，`inspect --offline` 只使用已校验的缓存；
缓存位于 `tools/macos/.staging/`，可在本轮检查后清理，下轮按锁文件重新获得。
`build --development --offline` 可复用该缓存。构建工具要求 Python 3.9+ 和支持 zstd
转 tar 流的 bsdtar；不安装全局工具作为隐式 fallback。应用用户不需要构建用 Python。

[锁文件](../tools/macos/runtime-lock.json) 固定 PBS 20260901 / CPython 3.12.14 arm64、
install-only 和完整 full-build 的 SHA-256。install-only 缺少依赖的完整许可，不能单独分发：
检查器读取同版本 full-build 的 `PYTHON.json` 和全部许可证，并逐字节哈希比对保留的
runtime 文件。保留 19 份上游许可证；zlib-ng 元数据例外仅在该项确实指向系统 zlib 时成立，
不作为普遍的缺许可豁免。运行时裁剪 pip/ensurepip/Tk/Tcl/IDLE 等不需要组件。
CA 固定来自 certifi 2026.7.22，随包保留其 notice 和另外校验的完整 MPL 2.0。
来源 manifest 包含锁文件、工具链、源码 commit/dirty 状态、资源哈希和许可覆盖，
不包含认证或用户资料。脚本不执行 Developer ID 签名、公证或发布。

[CI 配置](../.github/workflows/macos-p0.yml) 只匹配 `agents/cc-translate-macos-native`
的 push，并保留 `workflow_dispatch`；master、Windows 开发分支和 PR 不会触发此工作流。
该开发分支已获得推送/免费 CI 授权；实际推送和 run 状态以验收清单为准，不预先宣称通过。
纯手动新 workflow 通常需先存在于默认分支，故提供专用分支 push 入口，不为了注册工作流
提前合并 master。官方当前 `macos-15` 标签对应 arm64，仍用 `uname -m` 硬校验，
不用不存在的 `macos-15-arm64` 标签；固定 Xcode 16.4 路径并显式失败，不自动改工具链。
未来镜像版本会滚动，启动前检查可用性和额度，不能把标签当成 OS 镜像不可变 pin。

不能使用开发机系统 Python 冒充随包核心。smoke 检查实际解释器是否在 bundle、版本/架构、
isolated 状态、SQLite 读写、真实 TLS、取消/EOF 及 bundle 不被写入。
Windows 是原生编译外部门槛，不通过大规模写未经编译 UI 来掩盖。

### P0 App 的显式验收入口（以下操作尚未在 Mac 执行）

1. Finder 启动后仅应出现 `CC P0` 菜单栏项目，不自动弹窗、申请权限或联网。
2. `Open P0 input / probes...` → `Bundled core` → `Start bundled helper`，
   再 `Run synthetic fixture`；结果必须标为 SYNTHETIC，不是翻译。用新输入和 Cancel/Stop
   验证迟到结果不污染当前显示，关闭窗口不退出菜单栏应用。
3. `SQLite / SSL probe (no network)` 与 `HTTPS probe (explicit network)` 分别执行；
   四字段报告不暴露本机绝对路径。后者只有 bundle CA 验证真实 TLS 后才能 passed。
4. `Permissions / AX` 分别请求权限；回到目标 App 选择文本，通过菜单 AX 入口读取，
   或显式启动被动双击 Cmd+C。AX-only 探针不读/写剪贴板；全局监听要求 AX 与输入监控。
5. `CLI locator` 只发现路径；用户选择后才能运行 `--version`。输出丢弃，仅报告退出状态，
   认证始终 unknown。当前源码增加自有组清理；不要测试主动脱离进程组的 wrapper，
   不宣称真实 provider 兼容。测试包能力以对应固定 SHA 的验收记录为准。
6. `Screen / local OCR` → `Grant + capture main display once` → 检查预览 →
   `Confirm preview: local OCR`。只处理这一个保留帧，最长边最多 4096 像素；
   Cancel/关闭清空图像，没有磁盘保存或模型上传。
7. 退出应用后检查本 App 的 helper/版本探针结束。此前运行的用户 CLI 不应受影响。

这些是开发探针，不是完整产品 UI/本地化。签名、首次 TCC、多屏、IME 和发行包的验收清单
仍全部保留，不能因为菜单或 API 代码存在而勾选实机通过。

开发 `.app` 与发行 `.app` 明确区分。P0 正式门槛要求 Developer ID + Hardened Runtime +
公证/stapling 后，从干净用户 Finder 启动，执行包内 HTTPS 证书验证、SQLite 读写、
资源定位和 CLI 调用；没有身份/凭据时继续未通过。不要添加未经证明必要的宽泛 entitlement。
最低 OS deployment target 的编译通过不等于 macOS 14 运行通过。

### 首轮用户 Mac 验证交接（固定开发样本；正常打开后约 10–15 分钟）

**先由协调者确认测试路径，不让用户猜安装问题：**

- 用户测试 Mac 的 OS/芯片尚未确认；以下是条件要求，不是已知用户配置。等待用户在场后
  由协调者统一确认，不因此阻断能独立验证的纯核心开发。
- [已通过的 run 34701509226](https://github.com/mclight-ship-it/cc-translate/actions/runs/34701509226)；
  固定源码 SHA `826ba99571d572ca059ba02134797c79e1e9584a`。
- [下载开发 artifact](https://github.com/mclight-ship-it/cc-translate/actions/runs/34701509226/artifacts/10299469909)
  （GitHub 登录后下载，名称 `macos-arm64-p0-development-NOT-A-RELEASE`，
  2026-09-19 15:13 UTC 到期）。外层归档含 `CCTranslateMac-P0.zip`、
  `bundle-audit.json`、`helper-smoke.json`；不是 Release/安装器。
- 首轮优先 **Apple Silicon / arm64 + macOS 15**；CI 实际为 15.7.9、Xcode 16.4。
  macOS 14 只是 deployment target 候选，Intel 未验，不让 Intel 用户试装 arm64 包。
- **仅 fixture/诊断，不是完整翻译产品。** 不登录账号、不发送模型请求，不测试真实翻译能力。
- 构建脚本未对 `.app` 执行开发证书/Developer ID 签名，也未开启并验证 Hardened Runtime、
  公证或 stapling；单个 Mach-O 可能有工具链产生的 ad-hoc 签名，不等于 `.app` 已签名。
  **当前下载包没有通过干净用户的 Gatekeeper/Finder 首开验收，不能交给普通用户当作双击即用包。**
  CI 的 XCTest/helper 成功不证明 Finder 能打开，也不证明首次权限可用。

| 用户条件 | 本轮可执行路径 |
|---|---|
| 没有开发环境，只愿意下载运行 | **先阻断安装测试**：还缺协调者提供的 Developer ID 签名、Hardened Runtime、公证/stapling 包及其首开证据；不要让用户自行签名、修改安全设置或猜绕过方法 |
| 已有受支持的 Mac 开发环境 | 仅开发者可选下面的**本机源码开发构建**路径；不是所选云构建路线的用户必需步骤，不代替发行 Gatekeeper 验收 |

开发者路径要求本机已有完整 Xcode 16.4（许可/首次组件已正常完成）、Git、Python 3.9+，
并同意下载锁定的构建输入。普通最终用户不需要这些工具。若没有这些条件，交回协调者，
不要求为了本轮临时全局安装工具。以下在 Mac Terminal 中新建专用测试目录；已有同名目录或
测试 App 时停止，不覆盖/删除。不要在 Windows 仓库或正式应用目录执行。

```sh
(
  set -eu
  test "$(uname -m)" = arm64
  export DEVELOPER_DIR=/Applications/Xcode_16.4.app/Contents/Developer
  export MACOSX_DEPLOYMENT_TARGET=14.0
  test -d "$DEVELOPER_DIR"
  test "$(xcodebuild -version | head -n 1)" = "Xcode 16.4"
  python3 -c 'import sys; assert sys.version_info >= (3, 9)'
  test ! -e CCTranslate-P0-test
  git clone --single-branch --branch agents/cc-translate-macos-native \
    https://github.com/mclight-ship-it/cc-translate.git CCTranslate-P0-test
  cd CCTranslate-P0-test
  git checkout --detach 826ba99571d572ca059ba02134797c79e1e9584a
  python3 -B tools/macos/bundle.py build --development
  python3 -B tools/macos/smoke.py --allow-https
  target="$HOME/Applications/CCTranslateMac-P0.app"
  test ! -e "$target"
  mkdir -p "$HOME/Applications"
  ditto tools/macos/.build/CCTranslateMac-P0.app "$target"
  open "$target"
)
```

这是本机开发来源的常规 `open`，不是已验证的用户安装流程。若 `open` 被系统/组织策略阻止，
或出现无法验证开发者、损坏、恶意内容等提示，**停止并回报提示类别**，不要运行去隔离属性、
关闭 Gatekeeper/SIP、`tccutil reset`、重签下载包或直接执行包内二进制来绕过首开检查。
安全背景见 [Apple：安全地打开 Mac App](https://support.apple.com/en-us/102445)。

**正常打开后只做以下五组检查；全部使用新建 TextEdit 中的合成文字，不使用工作文档/真实截图：**

1. **静默与核心**：启动只出现 `CC P0`，不自动弹窗或请求权限。菜单
   `Open P0 input / probes...` → `Bundled core` → `Start bundled helper` →
   `Run synthetic fixture`；默认合成文字应有 SYNTHETIC 标记。依次执行 SQLite/SSL 与显式 HTTPS
   探针。关闭面板仍保留菜单，重新打开不能显示上次残留结果。
2. **权限拒绝与 AX 焦点**：首次不要先授予全部权限；在 TextEdit 选中 `P0 synthetic selection`，
   用菜单 `Read current AX selection (local only)`；缺 AX 权限应 UNKNOWN、不能取旧剪贴板。
   然后 `Permissions / AX` → `Request Accessibility`，按系统设置只批准本测试 App；
   必要时正常退出重开并 `Refresh states`。重新选择并调用菜单，结果应 PRESENT 且仍可在
   TextEdit 输入；无选区为 ABSENT 或有明确原因的 UNKNOWN，不能伪装旧文本。
3. **主动复制不被吞**：单独 `Request Input Monitoring`，按系统要求批准/重启；
   `Start passive double Cmd+C` 后回 TextEdit，半秒内按两次 Cmd+C，再在新行主动 Cmd+V。
   粘贴必须仍是合成选中文字，结果面板不能抢走输入焦点。`Stop monitor` 后复制仍正常，
   不应再触发探针。输入监控拒绝时应明确未启动。可在同一显示器 TextEdit/Safari 间各试一次；
   IME/多屏有条件时只记录焦点是否异常，不宣称覆盖完整矩阵。
4. **同帧截图**：先隐藏真实窗口/通知，让主屏仅有 TextEdit 合成内容 `P0 FRAME A 12345`。
   `Screen / local OCR` → `Grant + capture main display once`（屏幕内容权限独立批准；拒绝应停止）。
   预览出现后把 TextEdit 改成 `P0 FRAME B 67890`，再点 `Confirm preview: local OCR`；
   结果应来自保留的 A 帧而不是 B。`Cancel / clear` 或关面板后预览与 OCR 都清空。
   不上传预览、不保存屏幕；这里仅主显示器，不承诺跨屏框选。
5. **CLI 与退出**：仅当用户已有官方 Codex/Claude 可执行文件，才在 `CLI locator` 选择名称、
   `Locate known paths`/`Choose executable...`，再 `Run selected --version (5s limit)`。
   只报告成功/固定失败码，版本输出丢弃、authentication 仍 unknown；不要为了此探针登录或复制认证。
   不测试会遗留后代进程的自定义 wrapper。菜单 `Quit CC Translate P0` 后，在活动监视器确认
   本次 App/helper 退出，不按名称结束用户原有 CLI；无 CLI 时记 NOT RUN，不影响其他四组。

**失败信息怎么导出（当前没有自动诊断导出按钮）：**

在 TextEdit 用“格式 → 制作纯文本”，填写以下白名单模板并存为 `CCTranslate-P0-report.txt`，
只把这个文件交给协调者。OS/CPU 可用 `sw_vers -productVersion` 和 `uname -m` 获取；
显示器只填数量/缩放档，不填序列号。实际状态只填固定错误码/状态或简短的合成步骤结果。

```text
Build: 826ba99571d572ca059ba02134797c79e1e9584a / run 34701509226
Route: local-source-development / blocked-before-open
macOS: <version>   CPU: arm64   Displays: <count, scaling>
Open: PASS / BLOCKED / FAIL; system alert category: <category only>
Core: PASS / FAIL / NOT RUN; fixed error code: <code only>
AX: granted / not granted; Selection: PRESENT / ABSENT / UNKNOWN(<reason>)
Input monitoring: granted / not granted; Copy preserved: YES / NO / NOT RUN
Focus: PASS / FAIL / NOT RUN; IME: <language, no typed text>
Screen permission: granted / not granted; Retained A frame: YES / NO / NOT RUN
CLI: codex / claude / none; Version probe: PASS / <fixed error code> / NOT RUN
Quit: PASS / FAIL / NOT RUN
```

不附整个 Console/系统日志、`ps` 命令行、CLI stdout/stderr、路径下拉框、用户目录、邮箱、
认证文件、真实选区、剪贴板或屏幕。只在上述合成步骤重现；需要更多信息时由协调者提出最小
定向采集，而不是让用户打包全部日志。本轮报告中 NOT RUN/拒绝/阻断必须保留，不能填 PASS。

## 7. 功能对齐矩阵

| Windows 能力 | 原生实现 | 验收 |
|---|---|---|
| 托盘/暂停/召回/退出 | NSStatusItem + NSMenu | 关闭窗口不退出、退出无残留 |
| 划词/快捷键 | AX + 原生事件 + Cmd+C 双击 | 三态、不吞复制、权限拒绝、Secure Input |
| 结果/快速输入/流式 | NSPanel + 原生文本 | 不抢焦点、IME、选择/滚动、取消不串话 |
| 翻译/代码/摘要/重译 | 共享规则和 provider | 不额外分类调用、不变更原文、不静默切模型 |
| 词典/AI 补充/来源 | SQLite + 稳定 identity | 本地首屏不等 AI、异步时来源仍可点击 |
| 下载/删除/管理 | URLSession + 核心安装 | 显式下载、哈希、并发删除/查询、离线可用 |
| 截图/本地 OCR/视觉模型 | ScreenCaptureKit + Vision | 同帧、语言查询、多屏坐标、明确发送 |
| 历史/搜索/筛选 | SwiftUI + 核心持久化 | 条数、开关、复用、复制语义 |
| 设置/主题/语言/关于 | 原生组件与语义色 | 双语、深浅色、键盘、VoiceOver、来源许可 |
| 诊断/纯文本粘贴 | 原生入口与适配 | 脱敏、认证 unknown、用户主动操作 |
| 登录项/更新/卸载 | SMAppService / Sparkle | 真实批准状态、升级保护数据权限、只删自己的数据 |

## 8. P0–P6 与依赖

| 阶段 | 范围 | 通过条件 |
|---|---|---|
| P0 平台可行性 | 本文骨架、私有 IPC、权限/AX/热键、Finder CLI、随包 Python、截图/OCR、CI | 真 Mac 触发→核心→展示；签名公证发行形态探针；确定 OS/CPU |
| P1 共享核心抽取 | 路径、分类/提示词、缓存历史、provider 生命周期、自有进程组监督 | 无 Tk/Win32；Windows/macOS 规则一致；Windows 回归 |
| P2 原生主流程 | 结果、输入、流式取消、词典翻译解释摘要重译复制 | 所有主流程闭环，不伪装兼容 |
| P3 剩余功能 | OCR/截图、管理许可、历史设置诊断、粘贴主题语言 | 功能矩阵每项有测试或真实验收 |
| P4 分发生命周期 | 打包签名公证、登录项、Sparkle 更新、卸载 | N→N+1，协议/数据/权限一致 |
| P5 加固集中验收 | UI 时序、性能长稳、拒绝权限、多屏/休眠 | 未解决高优故障为零，缺环境不算通过 |
| P6 正式发布 | 安装文档截图、许可证、支持/局限、专属资产 | 用户另行确认后发布，不污染 Windows 通道 |

默认顺序 P0→P1→P2→P3→P4→P5→P6；打包、签名和权限可行性提前在 P0 阻断检查。
P0 自动化工程门槛通过后，可并行推进能独立验证的 P1 纯核心抽取；正式 P0 实机/签名门槛
仍须单独验收，不得据此盲目扩张 P2–P6 UI。P0 代码完成、Windows 验证、Mac 验证和发行验证
是四个不同状态。

性能目标（均非实测）：分类 P95 ≤2ms；常驻 IPC ≤5ms；词典查询+格式化 ≤10ms；
触发到完整词典首屏 ≤150ms。固定样本/冷热分开，LLM 首字与完成交错比较；
记录空闲 CPU、内存、长稳与休眠，不用单次均值替代 P95。

## 9. 集中验收、风险与停止条件

第一轮平台探针：首次权限拒绝/允许、TextEdit/Safari/Chrome/VS Code/Terminal/PDF 选区、
Finder CLI、账号、输入法、跨应用焦点、多屏/Spaces/Secure Input。
第二轮完整候选：全部主流程、安装/登录项/休眠、深浅色双语长内容、浏览器新下载公证包、
N→N+1 更新及权限/资料保留。目标是两轮，不为省次数隐藏故障或取消必要发行验收。

自动化优先覆盖来源按下期间 AI 更新、关闭时迟到结果、拖动时菜单、下载进度更新、
损坏/离线词典、provider EOF/超时/取消。预授权 runner 不代表真实首次 TCC 通过。
报告只包含合成输入和脱敏错误，不上传真实屏幕、认证、历史或机器私有路径。

| 阻断 | 处理 |
|---|---|
| 无可靠选区/权限 | unknown + 快速输入，不读取历史剪贴板冒充选区 |
| 签名包不能运行 runtime/CLI | 停止扩展 UI，先修打包/调用边界 |
| 核心抽取破坏 Windows | 不进入正式分支，保留兼容并回归 |
| 协议错配/迟到 | 显式失败、拒绝旧事件，不自动重放模型请求 |
| runner 无 GUI/TCC/最低 OS | 明确未验证，转有权限的真实 Mac |
| 更新丢权限/数据 | 阻止发布 |
| Claude/Intel 无验证 | 单独列未测，不宣布完整支持 |

## 10. 一手资料

以下是 API 能力来源，不是本 App 的验证证据：

- 原生界面：[MenuBarExtra](https://developer.apple.com/documentation/swiftui/menubarextra)、
  [NSPanel](https://developer.apple.com/documentation/appkit/nspanel/becomeskeyonlyifneeded)、
  [SwiftUI 无障碍](https://developer.apple.com/documentation/swiftui/accessibility-fundamentals)。
- 隐私/选区：[WWDC19](https://developer.apple.com/videos/play/wwdc2019/701/)、
  [AX trusted](https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrustedwithoptions)、
  [事件监听限制](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/EventOverview/MonitoringEvents/MonitoringEvents.html)、
  [Secure Input](https://developer.apple.com/library/archive/technotes/tn2150/_index.html)。
- 剪贴板：[changeCount](https://developer.apple.com/documentation/appkit/nspasteboard/changecount)、
  [多格式与延迟数据](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/PasteboardGuide106/Articles/pbConcepts.html)、
  [AccessBehavior](https://developer.apple.com/documentation/appkit/nspasteboard/accessbehavior-swift.enum)。
- 截图：[WWDC23](https://developer.apple.com/videos/play/wwdc2023/10136/)、
  [系统 picker](https://developer.apple.com/videos/play/wwdc2023/10053/)、
  [pointPixelScale](https://developer.apple.com/documentation/screencapturekit/sccontentfilter/pointpixelscale)。
- OCR：[Vision](https://developer.apple.com/videos/play/wwdc2019/234/)、
  [支持语言](https://developer.apple.com/documentation/vision/vnrecognizetextrequest/supportedrecognitionlanguages())。
- CLI：[Foundation.Process](https://developer.apple.com/documentation/foundation/process)、
  [Codex 安装](https://github.com/openai/codex#installation)、
  [Codex 非交互](https://developers.openai.com/codex/noninteractive/)、
  [Claude 安装](https://code.claude.com/docs/en/setup)、
  [Claude 非交互](https://code.claude.com/docs/en/headless)。
- 发行：[公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)、
  [Hardened Runtime](https://developer.apple.com/documentation/security/hardened-runtime)、
  [签名诊断](https://developer.apple.com/documentation/security/resolving-common-notarization-issues)、
  [SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)、
  [Sparkle](https://sparkle-project.org/documentation/)、
  [Python standalone](https://gregoryszorc.com/docs/python-build-standalone/main/)。
- CI：[runner 系统/架构](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、
  [macOS 15 arm64 镜像/Xcode 清单](https://github.com/actions/runner-images/blob/main/images/macos/macos-15-arm64-Readme.md)、
  [Swift Windows](https://www.swift.org/install/windows/)、
  [XCUIAutomation](https://developer.apple.com/documentation/xcuiautomation)。
- 可选未来能力：[系统词典](https://developer.apple.com/documentation/coreservices/1446842-dcscopytextdefinition)、
  [TranslationSession](https://developer.apple.com/documentation/translation/translationsession)、
  [Apple 翻译生命周期](https://developer.apple.com/videos/play/wwdc2024/10117/)。
