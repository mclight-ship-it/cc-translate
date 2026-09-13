# macOS 实施与验收清单

设计和安全契约：[MACOS_DEVELOPMENT.md](MACOS_DEVELOPMENT.md)。
基线：`148f7a1`；仅独立开发分支。更新日期：2026-09-13。
**当前路线：GitHub 免费站外分发，不要求付费 Apple Developer，不上 App Store。**
Developer ID/公证为未选择的可选增强。下文 2026-09-12 的唯一付费首开前置/冻结理由保留为历史，
已由末尾 2026-09-13 决策更新；不能据旧记录继续阻断免费首测，也不能把未实机门槛勾为通过。
当前已获准提交/正常推送 `agents/cc-translate-macos-native` 并使用公有仓库的标准免费 Mac CI；
首次云验证曾被 GitHub OAuth `workflow` scope 阻断；用户完成授权后已正常推送，
真实 Mac 自动化工程门槛通过（run/SHA 见下）；首轮用户正向探针报告已记录，完整 P0 首开/TCC 矩阵未通过。
不推送 master、不发布、不新增付费资源。
首次提交 `4102190` 的推送被旧 hook 的空树比较阻止。
已修正新分支基线为目标远端实际公布的 HEAD 与本分支的共同祖先；网络/缺对象等错误仍阻断，
空远端仍全树扫描。不豁免新增私密内容、不修改旧产品作者信息、不 bypass hooks。
相关隐私测试由 9 项增至 14 项，全部通过；修复提交为 `c655206`。

### 首次远端验证准备（2026-09-12）

| 检查/命令 | 实际结果 |
|---|---|
| `gh repo view --json nameWithOwner,visibility,defaultBranchRef,url` | 目标为项目自己的 `mclight-ship-it/cc-translate`，PUBLIC；默认分支 master 未修改 |
| Actions permissions / 官方 runner 清单 | Actions enabled；`macos-15` 是公有仓库标准免费 arm64 runner，固定 Xcode 16.4，不启用收费机器 |
| `python -B -m unittest tests.test_privacy_scan` | 14 tests，OK；新分支只排除已发布共同祖先，远端查找失败仍阻断 |
| 首次完整 pre-push | 缺少新工作树的开发词典导致 3 failures / 1 error；未删断言、未绕过 hook |
| 恢复固定词典后 `python -B -m unittest tests.test_dictionary_integration` | 18 tests，OK；从已有固定 release 恢复 67,948,544 字节文件，SHA-256 与仓库声明一致，仅放本工作树 ignored 开发数据目录 |
| 正常 push 对 `c655206` 的 pre-push | 隐私扫描、Python 编译及 `python -m unittest discover -s tests` 全部通过：871 tests，OK，48.836s；有既有 Tk teardown stderr 警告，无失败或 skip |
| GitHub 接收结果 | 拒绝 OAuth App 创建 `.github/workflows/macos-p0.yml`：缺少 `workflow` scope；没有远端 branch、run URL 或 Mac 执行结果 |

用户已完成浏览器授权；2026-09-12 本轮 `gh api --include rate_limit` 只筛选响应头，
确认现有 gh scopes 为 `gist, read:org, repo, workflow`，未读取/输出 token、换账号或绕过限制。
以该现有认证正常推送唯一开发分支，保留全部 hooks；授权成功本身不代表 Mac 验证成功。
此授权不包含 master、Release、签名私钥或付费额度。Mac 工程证据已允许并行 P1 纯核心。
勾选只表示本行完成，不代表整个阶段通过；实现和验证分开。

## P0 — Mac 自动化与首轮正向用户探针已完成；完整首开/TCC 矩阵未通过

### 文档与隔离
- [x] 独立 macOS 工作树/分支；不修改 Windows 工作树和用户数据。
- [x] 开发文档、阶段依赖、行为边界、一手来源及本清单落盘。
- [x] 路线图入口；术语表和风格仍是独立待办。

### 工程实现
- [x] 最小 SwiftPM 原生目标、菜单栏、NSPanel、显式合成 fixture 输入/结果代码。
- [x] Foundation.Process 包内 helper，双管道有界读取、EOF/失败/退出代码；静态核对打包路径。
- [x] Python v1 NDJSON 合同、握手/能力、唯一 ID/事件序号、长度限制、取消/终态竞态。
- [x] helper 无 Tk/Win32、无 AppData import 副作用；默认无网络/用户配置访问。
- [x] 显式 SQLite 实际读写、SSL/HTTPS 证书验证探针；开发包真实联网通过，签名发行形态未验收。
- [x] AX 三态、Secure Input、按需权限和 Cmd+C 双击/焦点探针代码；真实事件/TCC 未验收。
- [x] Finder CLI 候选路径发现及显式 `--version` 探针代码；监督自有进程组及留在同组的后代，
  主动逃离组的 wrapper 不支持，不跨组追杀。
- [x] ScreenCaptureKit 单帧预览确认与本地 Vision OCR 代码；真实 Vision 合成图像 XCTest 通过，真实截图/TCC 未验。
- [x] 随包 runtime 锁定/校验、架构/deployment target/dylib/资源/完整许可检查；Mac 开发包审计通过。
- [x] Mac 编译/自动测试工作流落地；已获准开始真实开发分支 CI，结果另记。

### 验证证据（执行后填命令、环境、结果）
- [x] Windows 便携协议/进程/打包规则 unittest。
- [x] Windows 隐私扫描回归及 diff 检查；恢复固定开发词典后正常 pre-push 已执行完整 871 项 unittest（含现有 GUI 单测），不代表 Mac GUI/实机验收。
- [x] macOS Swift 编译/XCTest（真实免费 arm64 CI、固定 Xcode 16.4）。
- [x] macOS 开发 .app 内真实 helper、SQLite、HTTPS、资源定位；不是签名发行验收。
- [ ] 原生触发→核心→展示，真实 EOF/取消/退出无残留。
- [x] 合成图像真实 Vision OCR 自动测试。
- [ ] Finder 原生/Node CLI 安装路径与版本调用，无 shell profile。
- [ ] 首次 TCC/AX/热键/焦点/IME/Secure Input/多屏实机矩阵。
- [ ] 可信 GitHub 下载/校验后由 Finder 首开；适用警告时用户自行选择官方单 App 例外，再运行探针。
- [ ] 可选且当前未选择：Developer ID / Hardened Runtime / 公证/stapling；不是免费首测前置。
- [ ] 确认 macOS 最低运行版本、Apple Silicon 支持；Intel 独立验证后再承诺。

### 已执行记录

| 环境 | 命令/内容 | 结果 |
|---|---|---|
| Windows，Python 3.12.10 | `python -B -m unittest tests.test_macos_protocol tests.test_macos_bundle tests.test_privacy_scan` | 最终复跑 74 tests，OK，14.995s；29 个核心测试、36 个打包/联合测试、9 个既有隐私扫描测试 |
| Windows | `python -B tools\macos\bundle.py inspect --offline` | 通过：19 份上游许可证，605 个保留 runtime 文件与锁定 full-build 逐文件哈希一致；未安装或执行 Mac runtime |
| Windows | `git diff --check` | 通过 |
| Windows | 复用 `.githooks/privacy_scan.py` 的 `scan_line` 扫描全部 30 个改动/新增文本文件 | 0 findings；不包含本机私有路径、账号或凭据 |
| Windows | Swift / Xcode 工具检查 | 未安装/不可用，不尝试全局安装工具 |
| Windows | `python -B tools\macos\bundle.py build --development`、`python -B tools\macos\smoke.py --allow-https` | 两者按预期返回 1，明确要求 arm64 Mac/Xcode，在下载/构建/联网 smoke 前阻断；这不是 Mac 测试通过 |

新增核心用例覆盖：Unicode/字节边界/重复键/非有限数/深度/残缺 EOF、握手与唯一 ID、
合成标记、取消完成竞态 40 轮、并发容量、会话上限、broken pipe、
isolated 包内入口、无 Tk/Win32/AppData 导入副作用，以及真实临时 SQLite 读写清理。
联合测试实际复制待打包 helper，使用 `-I -B` 启动宿主 Python，通过构建 smoke 的消费端
执行握手/合成输出/离线运行时探针/EOF；同时证明宿主报告不会被当成 Mac 发行证据。
EOF 会给自有 worker 有限清理时间，线程无法启动明确 failed，不遗留 success-shaped 请求。
证书成功/拒绝的联网分支使用 mock；真实离线 SSL context 校验开启。
这不构成真实 HTTPS 网络验证，也不是随包 Python 在 Mac 上的执行证据。
首次测试仅有 stderr 的 Windows CRLF 断言差异，改为逐行断言后通过，未改协议 LF。
后续修复了打包目录头边界、smoke 深度/Unicode 校验复用、runtime 四字段合同和 CI 标签；
官方当前标签 `macos-15` 为 arm64，固定 Xcode 16.4 并重复检查实际架构。
工作流只为指定 macOS 开发分支配置 push，另有手动入口；最初本地验证时未推送或运行，后来真实结果见下。
依赖归档只下载作静态检查，未安装或执行；临时 staging 中归档已清理，仅保留紧凑验收记录。

### 首次真实 Mac CI（2026-09-12）

- Run：[34697405027](https://github.com/mclight-ship-it/cc-translate/actions/runs/34697405027)，
  源码 SHA `7310aa82fd331606fc8785fa10a021905aafc85b`，结论 **success**，job 1m35s。
- 推送前正常 hook：871 Windows tests，OK，51.964s；既有 Tk teardown stderr 警告，无失败或 skip。
- 实际工具链：arm64，Xcode 16.4 / 16F6，macOS SDK 15.5，Swift 6.1.2。

| Mac 阶段 | 实际结果 |
|---|---|
| 便携协议/打包 unittest | 65 tests，OK，6.828s |
| 普通 `swift test` | 26 项中 25 通过，唯一包内集成因尚未 build 明确 skip；0 failures |
| 原生探针 | 10 tests 通过，其中 `testVisionRecognizesSyntheticInMemoryImage` 实际调用 Vision，1.742s |
| release 构建、固定 Python、许可/Mach-O/资源审计 | 全部通过；仍为 development-only、未 Developer ID 签名/公证 |
| 构建后 `CC_TRANSLATE_APP=... swift test --filter HelperIntegrationTests` | **1 test，0 failures，0 skip，0.353s**；Foundation.Process 实际运行包内 Python，fixture/SQLite/关闭通过 |
| 独立 HTTPS/SQLite smoke | 包内握手、fixture、SQLite、证书验证 HTTPS、cancel、EOF 全通过 |
| smoke 后重新审计 | Mach-O/resource 通过，bundle 未被写入；开发 zip 和脱敏报告保留 7 天，未发 Release |

首轮无需修复 Swift/API/链接问题；Actions 的 Node 20→24 / punycode 弃用提示为 warning，
并非测试失败。SDK/deployment target 通过不代表 macOS 14 真机运行通过。

### 原生交叉检查与仍需实机验证

13 个原生工程/源码/测试/资源文件已落盘；26 项 XCTest 已写并全部实际通过，
其中唯一包内集成必须以后续构建后的执行为证，不能用首轮 skip 充数。
已静态核对：`CCTranslateMac` product、开发 Bundle ID/Info.plist、`Helpers/python/bin/python3`、
`Resources/Core/launch.py`、`-I -B`、四字段 runtime 报告、序号/终态和前端停止宽限。
原生 P0 结果窗口只用于非激活探针，完整可选择结果/IME/浮窗产品交互仍属于 P2。
P0 被动 Cmd+C 的 AX-only 路线显式要求辅助功能及输入监控均通过；缺失时不假装已经监听。

CI 先运行普通 Swift 测试，此时包尚不存在，唯一包内集成测试会明确 skip；
构建 `.app` 后再设置 `CC_TRANSLATE_APP`，用 `--filter HelperIntegrationTests` 真正执行
Foundation.Process→包内 helper→fixture/SQLite/关闭流程。初次 skip 不能计为集成通过。
Python HTTPS smoke 是另一步，不替代原生客户端链路。

首次 Mac 编译/自动化已覆盖这些此前仅静态检查的 API/生命周期边界：
`SCScreenshotManager.captureImage` / `SCShareableContent` 的 async 导入，
`MainActor.assumeIsolated` 与 OCR 任务的并发诊断，Carbon/AX 的 CF 桥接，
`F_SETNOSIGPIPE`、DispatchSourceRead 和关闭管道的顺序。
实际编译通过；真实事件/TCC/焦点行为仍须实机，不凭自动化结果扩大 P2–P6。

首轮 CI 当时已运行 macOS 编译和 CI；尚未运行真实用户 GUI/TCC、Developer ID 签名、公证或发布。
上述证据只解锁依赖安全、可独立回归的 P1 纯核心。

## P1 — 已完成切片见下；其余依赖继续待办，完整 P1 未完成

Mac 编译/XCTest/原生包内 IPC/Mach-O/HTTPS/SQLite 通过后，可推进独立纯核心抽取。
自动化不代表完整 P0 首开/TCC 矩阵通过，也不解锁完整 P2–P6 UI；原包的首轮正向用户报告另记。
当前采用免费 GitHub 分发路线，Developer ID/公证为未选择的可选增强，不是 P1 或免费首测的强制准入。
- [ ] 平台路径，Application Support/Caches 分工，业务配置/历史单一写入者。
- [ ] 抽取分类/方向/提示词、请求快照、缓存签名与词典结构；保留 Windows 兼容入口。
  - [x] 本地分类抽到 `cc_classify.py`，Windows 导出相同函数/阈值，helper 包含同一份模块；
    不导入 Tk/Win32/`cc_core`，不改 P0 协议/UI/provider 能力。
  - [x] 抽取前后 29 个函数/常量 AST 一致（忽略 docstring），没有更改规则。
  - [x] 新共享分类、isolated 无副作用、Windows 兼容以及包内 Python 差分回归通过并记录。
  - [x] 方向路由/方向 prompt 抽到 `cc_direction.py`，9 个函数/常量及 mode 生成 AST 与原
    `cc_core` 完全一致；显式传入 UI 语言，i18n 标签 wrapper 留原层，兼容导出同一对象。
  - [x] 方向切片的完整 Windows hook 与最新 Mac/包内 Python 回归通过并记录。
  - [x] 词典触发 `is_single_word` 抽到现有 `cc_classify`，不新增抽象；AST 与旧实现完全一致，
    Windows 主入口/`cc_core`/结果操作为同一函数，本地词典候选和 AI/历史/摘要路由未改。
  - [x] 词典触发切片的完整 Windows hook、最新 Mac 和包内 Python 回归通过并记录。
  - [x] 静态文本提示词目录 `cc_prompts.py` 接入既有 `cc_core`/Windows/warm/结果操作，
    12 个赋值 AST 和抽取前 UTF-8 快照一致，revision 不变，无新增 provider 调用。
  - [x] 提示词目录的完整 Windows hook、真实 Mac/包内回归通过并记录。
  - [x] provider 包初始化只导入纯 base/registry；显式后端导出仍使用原对象并传播失败。
    包内只携带三个契约文件，不携带 CLI 后端/用户认证，不宣称 native Mac provider 已实现。
  - [x] provider 契约初始化边界的完整 Windows hook、真实 Mac/包内回归通过并记录。
  - [x] 只读词典存储的原生路径/线程局部连接、合成 runtime probe 及包内同源验证。
  - [x] 共享缓存签名与历史类型规则：显式值纯模块、Windows 兼容 wrapper 和 Mac 同源回归；
    源码 `fa6a0b8` / [run 34762885485](https://github.com/mclight-ship-it/cc-translate/actions/runs/34762885485) 通过，
    见[共享规则证据](#共享规则可靠检查点2026-09-13)。
    保留旧拼接字节/版本、local route 优先级和 OCR > code > dict > text，不创建完整 RequestSnapshot。
- [ ] Codex native 配置/认证/目录/工具/hook 边界；严格事件流，不做 exec 假兼容。
  - [x] 第一步：只接既有 `read_native_config` 的 Darwin 分支；共享已验证 C 组信号/
    zombie-only 判断原语，随包装载失败必须明确失败，不回退裸 PID kill。
  - [x] 第二步（依赖第一步）：在同一调用链以非阻塞有界管道驱动 initialize/config/read，
    成功/错误/8 秒期限均先清自有组再回收；保持 Windows 路径及完整 native env/cwd/安全覆盖。
  - [x] 第三步（依赖第二步）：真实 Mac fake app-server 验证协议、洪泛/阻塞/后代/兄弟存活，
    并从现有显式合成诊断入口验证包内接入；不运行真实账号/模型，不宣称完整 provider 可用。
  - [x] catalog/cache 显式路径与日志切片：真实构造链、完整 hook / 真 Mac 验收已通过；
    旧默认路径/日志仍保留给 Windows，不能直接把整个 exec 当便携。
    - [x] 保留 Windows 默认表达式，复用 catalog 的 cache_dir/work_dir，仅补 logger 与 provider 转交。
    - [x] 合成 fixture 替代 CLI 输出边界，但实际运行 fingerprint、写入、roundtrip、state 激活和重开；
      显式路径之外不写入、不导入 cc_core，也不调用未监督 CLI。
    - [x] Windows 构造链/原行为及 Mac 包内同源验收通过。
  - [x] catalog version/debug-models 真进程自有组监督：源码 `a0c2df6` /
    [run 34761449362](https://github.com/mclight-ship-it/cc-translate/actions/runs/34761449362) 已通过，
    见[完整证据](#catalog-可靠检查点2026-09-13已完成本次单一切片)。不改已提交 turn 或调用真实账号。
    - [x] 复用已有 C ABI/所有权规则，已接入既有 catalog `_run` 的 Darwin 分支；
      stdout/stderr 在读取期间限额，deadline 后清组再 reap，不按进程名追杀。
    - [x] 真 Mac fake CLI 覆盖冷缓存三调用、缓存重开 roundtrip、超时/洪泛/退出后后代/兄弟存活；
      原 argv/env/cwd/安全覆盖与缓存回归保持，16 项 catalog 真进程测试实际通过；不扩到 exec/app-server。
  - [ ] 前置完成后再分别处理 exec、常驻 app-server、预热/取消与提交快照；
    现有 poll/wait 会提前 reap，不能仅在旧 `_kill_process` 里补一行 killpg。
- [ ] Claude 独立生命周期/流式/诊断适配；未知认证明确展示。
- [ ] POSIX 本 App 自有进程组取消/回收；warm 不创建付费 turn、请求不自动重试。
  - [x] 原生显式版本探针先接入自有组监督；fake CLI 正常/取消/超时/输出超限/后代回归。
- [ ] 新核心直接 import 测试及 Windows 集成回归，Mac 差分测试。

### P1 分类切片已验证（2026-09-12）

- 首次针对性执行 91 项：3 failures / 1 error，原因是合成 bundle fixture 未同步新必需文件，
  以及 Windows isolated Python 自身已预加载 `winreg`。已补齐 fixture、增加缺失/篡改分类模块
  的失败关闭测试；隔离测试比较新增模块并拦截 import（含缓存模块）/写入/网络，不删除保护断言。
- 修复后 Windows 针对性联合回归：93 tests，OK，15.237s。
- 正常 pre-push：隐私扫描、编译和完整 Windows unittest **878 tests，OK，57.120s**；
  包含加强后的缓存模块 import 拦截，仍只有既有 Tk teardown stderr 警告，无失败/skip。
- Run：[34697828518](https://github.com/mclight-ship-it/cc-translate/actions/runs/34697828518)，
  源码 SHA `81db76583cb6ef6589f3b9b79605f310c977d3a2`，**success**，job 1m53s。
  实际 macOS 15.7.9 arm64，runner image `20260907.0337.1`，Xcode 16.4；
  宿主 Python 3.14.7 不作为随包 Python 证据。

| 第二轮 Mac 阶段 | 实际结果 |
|---|---|
| 便携协议/打包/分类/隔离 unittest | 91 tests，OK，7.681s；不含 Windows 专属兼容入口测试 |
| 普通 XCTest | 25 通过，唯一包内集成先 skip；合成 Vision 实际通过，1.901s |
| 构建后强制 Foundation.Process 包内集成 | 1 test 通过，0 skip，0.434s |
| 包内 Python 的同一分类矩阵 + 隔离测试 | **22 tests，OK，0.060s，0 skip**；断言模块来自 `Resources/Core`，不回退源码或宿主 |
| bundle 构建、运行时/许可审计、HTTPS/SQLite/cancel/EOF、最终不可变检查 | 全部通过 |

已下载并核对本轮合成审计报告：5 个 Mach-O 全为 arm64，645 个制品库存项，19 份
Python 上游完整许可证；`source_tree_dirty=false`，source manifest 的 SHA 与上述源码提交一致。
包内 CPython **3.12.14** / SQLite **3.53.1** / OpenSSL **3.5.8**，isolated/禁写字节码/
bundle_runtime 均为 true；HTTPS 证书验证、SQLite 读写、显式取消、EOF 取消、临时探针清理、
bundle 未写入均为 passed/true。发行门槛仍明确 `NOT PASSED`。
分类模块哈希与该提交的 Git LF blob 一致；Windows 工作副本 CRLF 不作为制品哈希基准。
本地已清理下载的开发 app zip，仅保留会话内脱敏 JSON 证据；远端开发制品按 7 天过期。

本轮到达可重复的 P1 分类检查点，不是完整 P1 完成。下一可自动推进切片为方向/提示词的
无副作用抽取；配置路径、词典、缓存/历史和 provider 初始化边界仍独立验收。
POSIX 自有进程组监督、native 配置/认证/工具/hook、零付费预热及不重试契约未因本次抽取改变。

### 上一轮证据文档收尾复验

文档提交 `9bb8fc26dd48dd8cd5792bc9d6c8124d4c500da3` 与 `81db765` 的可执行源码、
测试和工作流完全相同；仍正常触发并等待了第三轮
[34698003528](https://github.com/mclight-ship-it/cc-translate/actions/runs/34698003528)，
**success**，job 1m26s。Mac 便携 91 tests（6.178s）、普通 XCTest 25 通过 + 1 初次 skip、
构建后包内集成 1 test 真正通过且 0 skip（0.426s）、包内分类 22 tests（0.055s）；
完整 bundle/许可/Mach-O/HTTPS/SQLite/cancel/EOF/不可变审计再次通过。

记录本段的最后提交只改本清单，使用文档专用 `[skip ci]` 避免“记录 run 又生成新 run”的循环；
保留正常隐私 hook，不改/跳过任何源码测试断言或真实集成步骤。
最后已验证的执行源码 SHA 为上述 `9bb8fc2`，不把纯文档提交冒称另一轮 Mac 测试。

### 方向纯核心与首轮用户 Mac 交接（2026-09-12 已验证）

- Windows 方向/分类/隔离/打包/协议及原有方向/摘要针对性联合测试：
  **134 tests，OK，16.466s**。方向新测试覆盖固定目标、自动路由、混合文本、日文/韩文、
  ASCII-Latin 计数、0.34 阈值、精确 prompt 和既有未知 mode 回退。
- 两个共享模块均随包且纳入必需资源/哈希；缺失或篡改任一个均失败关闭。
  不以针对性测试代替完整 Windows hook 和真实 Mac CI。
- 正常 pre-push：隐私扫描、Python 编译和完整 Windows **893 tests，OK，51.136s**；
  无失败/skip，只有既有 Tk teardown stderr 警告。
- 最新真实 [run 34698580547](https://github.com/mclight-ship-it/cc-translate/actions/runs/34698580547)，
  源码 SHA `f526459add6d287ac3402be93cf925cce0c506c9`，**success**，job 1m33s。
  实际 macOS 15.7.9 arm64、image `20260907.0337.1`、Xcode 16.4 / 16F6、Swift 6.1.2。

| 方向切片 Mac 阶段 | 实际结果 |
|---|---|
| 便携分类/方向/隔离/协议/打包 unittest | **103 tests，OK，6.716s** |
| 普通 XCTest | 25 通过 + 唯一包内集成初次 skip；合成 Vision OCR 实际通过，1.973s |
| 构建后强制包内 Foundation.Process 集成 | **1 test，0 failures，0 skip，0.433s** |
| 包内 CPython 同一分类/方向/隔离回归 | **34 tests，OK，0.063s，0 skip**；两模块均断言来自 bundle |
| 固定运行时/完整许可/资源/Mach-O、HTTPS/SQLite/cancel/EOF、最终不可变审计 | 全部通过 |

已下载核对审计报告与 source manifest：646 个库存项、5 个 arm64 Mach-O、19 份完整
runtime 许可证；源提交为上述 `f526459` 且 clean，两纯模块哈希与已提交 Git LF blob 一致。
包内 Python 3.12.14 / SQLite 3.53.1 / OpenSSL 3.5.8；smoke 的 bundle_unchanged、
probe_files_cleaned、显式/EOF 取消及真实 HTTPS 证书验证均通过；发行门槛仍 `NOT PASSED`。
开发 app zip 已从本地临时下载中清理，只留会话内脱敏 JSON；远端 artifact 到期自动清理。

- [首轮用户 Mac 交接（开发指南对应小节）](MACOS_DEVELOPMENT.md)
  当时写明具体 run/artifact/SHA、不能承诺普通下载首开、签名缺失条件、本机源码开发路径、
  五组显式探针及手动白名单 TXT 脱敏回报。现已按 2026-09-13 决策更新为免费首测路线。
- 不让普通用户自行去隔离属性、关闭 Gatekeeper/SIP、重签下载包、直跑二进制或脚本重置 TCC；
  当时无开发环境且无签名分发条件时安装测试阻断，纯核心继续；该首开前置现已取消，
  改为可信下载及适用时用户本人选择官方单 App 例外，仍不保证首次实机成功。
- 收尾仅更新两份文档，正常 privacy hook，提交注明 `[skip ci]`；执行源码/测试/workflow
  与已绿色的 `f526459` 完全相同。下一独立纯核心候选是请求快照/提示词剩余部分与平台路径；
  不把本轮结果误标为全部 P1 或正式 P0 已完成。

### 词典触发纯核心（2026-09-12 已验证）

- 新共享入口复用 `cc_classify.is_single_word`，无新增模块；三处 Windows 兼容导出有 identity
  断言。保留原 1–2 token / 30 字符、短 CJK、末尾标点、换行和特殊符号语义。
- 针对性 Windows 联合测试 **130 tests，OK，5.075s**：分类/方向/词典触发、无副作用、
  打包、18 项现有本地词典路由集成及原有词典/摘要边界；没有调用真实 provider。
- 8 个新增边界用例固定现有行为，不把解耦伪装成语言识别算法修复。AST（含 docstring 和
  Unicode 句末标点常量）与原 `cc_core.is_single_word` 完全相同。
- 正常 pre-push：**902 Windows tests，OK，51.856s**，仅既有 Tk teardown stderr 警告，
  无失败/skip，隐私扫描和编译通过。
- [run 34699029742](https://github.com/mclight-ship-it/cc-translate/actions/runs/34699029742)，
  SHA `aa06ad8f48371d0cdff011b01830b932b2b55e66`，**success**，job 1m35s。
  Mac 便携 **121 tests，5.571s**；普通 XCTest 25 通过 + 唯一集成初次 skip，
  构建后原生包内集成 **1 test 真正通过、0 skip、0.473s**；
  包内分类/词典/方向/隔离 **52 tests，0.108s，0 skip**。
  bundle/完整许可/Mach-O/HTTPS/SQLite/cancel/EOF/不可变审计全部通过。
- 当时用户 Mac 芯片/OS 未确认，首次实机/签名门槛保留（签名前置现由免费路线取代）。此块完成后顺序推进下面的提示词，
  没有因为用户不在场而暂停，也不把两个候选同时铺开。

### 静态文本提示词目录（2026-09-12 已验证）

- 只移动已存在的文本提示词/独立 provider revision；Unicode、空白、数据边界、
  verbatim-code 指令和所有输出要求逐字不变。原有 OCR 专属文案/动态组装暂不扩大范围。
- 针对性联合 **131 tests，OK，5.478s**：抽取前 UTF-8 快照、Windows 消费者 identity、
  隔离/打包、现有词典补充/摘要/warm profile/缓存签名/后续结果/单次 Claude 请求测试。
- 规范 JSON UTF-8 的旧目录 SHA-256：
  `e3acdc8589d0182b6c800450d6c5fa6eb7c033f69e45e8f976716403d412a844`；
  与新模块相同。新增模块实际经旧入口消费，不是只放一个没有接线的 helper。
- 三份共享模块均纳入包内必需资源/哈希审计、逐项缺失/篡改的失败关闭测试；
  不以静态证明代替运行验证。
- 正常 pre-push：**909 Windows tests，OK，56.194s**；仅既有 Tk teardown stderr 警告，
  无失败/skip，隐私扫描和编译通过。
- [run 34699378363](https://github.com/mclight-ship-it/cc-translate/actions/runs/34699378363)，
  SHA `0d5f3181f66a3bcc348a6ceef2ce64358d961334`，**success**，job 1m11s。
  Mac 便携 **126 tests，5.444s**；普通 XCTest 25 通过 + 唯一集成初次 skip，
  构建后原生包内集成 **1 test 真正通过、0 skip、0.303s**；
  包内纯核心 **57 tests，0.078s，0 skip**。bundle/完整许可/Mach-O/HTTPS/SQLite/
  cancel/EOF/不可变审计全部通过。开发 artifact `10300220807`，7 天过期，未发布。

### provider 纯契约初始化边界（2026-09-12 已验证）

- 在前两块完整 Mac 通过后顺序推进此项：旧 `cc_providers.__init__` 的 eager CLI 导入
  改为按需后端导出；纯契约与 registry 导入不再连带加载 CLI/config/catalog，
  并在隔离测试中禁止平台/网络/文件写入副作用。
  未修改 base 数据类、registry 算法或具体 Codex/Claude 实现；`__all__` 保留，后端对象同一。
- 包内显式只复制 `__init__.py`、`base.py`、`registry.py`；每个文件纳入必需资源/哈希和
  缺失/篡改失败关闭回归。Mac CLI 后端仍不存在，不能因纯导入通过而假称 native 兼容。
- 针对性 **142 tests，OK，5.275s**：纯契约冻结/未知认证/registry、导出/导入失败、
  fresh isolated 进程、打包以及原有 provider/native config/catalog/事件协议测试。
- 正常 pre-push：**921 Windows tests，OK，54.212s**，无失败/skip；
  仅既有 Tk teardown stderr 警告。隐私扫描、编译通过，未绕过 hooks。
- [run 34699791572](https://github.com/mclight-ship-it/cc-translate/actions/runs/34699791572)，
  SHA `7def21bd84a44e164345097c8726861e43cbeb30`，**success**，job 1m10s。
  Mac 便携 **135 tests，4.629s**；普通 XCTest **25 通过 + 唯一包内集成初次 skip**，
  构建后原生包内集成 **1 test 真正通过、0 skip、0.309s**；
  包内纯核心/契约 **64 tests，0.112s，0 skip**。完整 build/Mach-O/许可及
  HTTPS/SQLite/显式取消/EOF/不可变审计均通过，未执行 GUI/TCC/签名测试。
- 已实际下载核对 artifact `10300306104`：干净源码 manifest 对应上述 SHA，
  六份共享模块/契约文件与 Git blobs 逐字一致、哈希一致；provider 目录仅三个白名单文件，
  无旧 CLI 后端。审计含 **5 个 arm64 Mach-O、650 个库存项**；随包 Python
  **3.12.14 / darwin arm64 / isolated**，SQLite **3.53.1**，真实 HTTPS 证书验证通过。
  下载的临时 app zip 已清理，脱敏 JSON 留会话制品目录；不执行 Mac 二进制。
- 首轮实机交接已更新到上述 run/artifact/SHA。文档证据收尾采用 `[skip ci]`，
  源码/测试/工作流保持该绿色提交不变，不把文档提交另算为 Mac 验证。
- 用户 OS/CPU、账号、TCC、签名状态均未获确认；当前仍是 fixture/诊断包。
  配置/历史单一写入者、完整请求快照、POSIX 自有进程组监督和 native 后端仍未完成，
  这些与正式 P0 实机/发行门槛分别追踪，不能将本小块通过标作全部 P1 完成。

### 原生 CLI 版本探针自有组监督（2026-09-12 已验证）

- 从实际已有的 `CLIVersionRun` 调用链切入，不增加未调用的 ProviderRuntime 抽象。
  C/Swift 私有边界原子 spawn 新组，显式空 stdin/有界丢弃输出/固定 HOME 与 PATH；
  不 source profile、不登录、不读取认证、不发送模型文本。
- 保留未回收 leader 的 PID/PGID，正常退出也清理同组后代，取消/5 秒超时/输出超限均
  TERM→KILL→reap；不按名称/全系统 PID 搜索，不处理自行逃离组的 wrapper。
- 五项新增 XCTest：正常 leader 先退出、TERM-resistant 后代的取消/超时、输出超限、
  启动失败/重复取消。合成 sibling 必须存活；所有脚本/数据仅临时 synthetic fixture。
- Windows 针对性 **137 tests，OK，16.143s**，覆盖便携协议、bundle 和原 provider 契约/
  实现回归。Windows 生产实现没有变化；Mac C/Swift 编译及真实进程断言尚待本轮 CI。
- 用户无需为所选云构建路线安装 Xcode；签名资格/OS/CPU 未确认，普通下载首开/TCC 仍 blocked。
  此项通过也不表示真实 Codex/Claude 兼容或完整 P1 生命周期完成。
- 第一轮 [run 34700390995](https://github.com/mclight-ship-it/cc-translate/actions/runs/34700390995)，
  SHA `c1d15443357bbdacebe012a42aa5db42619f7044`，**failure**：Mac C/Swift 编译及
  五项新进程组测试通过，但旧 echo/yes 两项断言失败；后续 bundle steps 未执行，不能计为通过。
  该次正常 pre-push 完整 **921 Windows tests，OK，52.158s**，不代替失败的 Mac 结果。
- 已定位 Darwin `killpg1` 会对只剩 zombie 的组返回 EPERM（并非 ESRCH），错误覆盖了原
  成功/输出超限状态。修正为只有 leader 已退出、且对该固定自有组的内核快照确认全部成员
  都是 zombie/空组时才视为清理完成；真实权限错误仍失败。保留原断言与 leader 防复用措施，
  不改用全系统扫描或忽略所有 EPERM；等待修正提交的实际 Mac 复验。
- 修正 [run 34700626688](https://github.com/mclight-ship-it/cc-translate/actions/runs/34700626688)，
  SHA `014ab9ff478f0afed533ade2b3ca05d7492ab4e5`，**success**，job 1m18s。
  正常 pre-push **921 Windows tests，OK，49.755s**（仅既有 Tk teardown 警告）；
  Mac 便携 **135 tests，4.949s**，普通 XCTest **30 通过 + 唯一初次集成 skip**，
  新增五项真实进程回归 **6.982s**，旧 echo/yes 原断言恢复通过；
  构建后包内集成 **1 test，0 skip，0.402s**，包内核心 **64 tests，0 skip，0.118s**。
  release bundle、Mach-O/完整许可、HTTPS/SQLite/cancel/EOF/不可变审计均通过。
- 开发 artifact `10299638691`，2026-09-19 14:55 UTC 到期。实机交接固定到该 run/SHA，
  文档-only 收尾不改任何执行源码，不额外重复相同 CI。正式首次权限/签名门槛仍未过。

### 只读词典存储与线程生命周期（2026-09-12 已验证）

- 顺序在原生进程组完整 Mac 通过后开始。复用原 `DictionaryStore`，不另造未调用的存储层：
  现有 Windows `LocalDictionary` 继续使用它，Mac 显式 runtime probe 实际打开合成 SQLite，
  不访问用户词库、不新增业务 IPC/GUI、不改变配置/历史的持久化规则。
- 发现旧 `.replace("\\", "/")` 会破坏 POSIX 文件名的字面反斜杠；改用 `Path.as_uri`，
  Windows 常规路径不变，Mac 特有合法反斜杠/问号与两平台 Unicode/空格/`#`/`%` 均纳入测试。
- 原 builder-v3 DDL 逐字移动至 store 并兼容导出，AST 字符串/对象 identity 已核对；
  UTF-8 SHA-256 `2946f5367ba1cb1f2e3da591f683826785cb5aa59e62c6c7a59fe7dfa15dea66`，
  schema/data 版本、来源/许可字段及词典构建算法不变，未重新下载/构建真实词库。
- 九项便携测试覆盖只读 URI（关闭 query_only 后仍不可写）、三类匹配/来源、线程连接独立、
  close 幂等/重新打开、缺失/哈希错误不回退、SQL 参数和固定脱敏错误。新增模块纳入必需资源/
  哈希审计；Foundation.Process 与 HTTPS smoke 必须确认词典状态，不接受缺失或 false。
- 首次针对性 131 tests 有一项失败：旧隔离 launcher 的合成装配只复制 helper，不含新增
  共享依赖。已改复用正式 `copy_core_sources`，保留隔离断言；随后 **133 tests，OK，19.944s**。
  完整 Windows hook、真实 Mac/包内生命周期仍待本轮 CI，不以本地结果代替。
- 仅证明受控合成文件的只读/线程局部生命周期；真实词库安装/更新、全局 store 替换、
  ProviderRuntime、用户配置/历史单写边界仍是后续 P1，不把此次切片标作全部完成。
- 首轮 [run 34701230439](https://github.com/mclight-ship-it/cc-translate/actions/runs/34701230439)，
  SHA `f2355258a4537a3effdd9654c44420515ec6d1a1`，**failure**：Windows 正常完整 hook
  **931 tests，54.929s，OK**；Mac 便携/普通 Swift/bundle 构建通过，但真正 Foundation.Process
  包内集成返回 `invalidPayload`，后续 smoke/包内核心未运行。
  原因是漏接 Swift runtimePayload 的严格顶层字段清单，不是词典查询失败。
- 修正原生校验和固定 `dictionary_probe_failed` 显示码，新增缺失字段、假布尔、私有路径、
  终态序号保持与有效报告的回归；smoke 同步严格四字段白名单。
  不放宽未知字段、不跳过失败集成；同包 native/helper 必须匹配，旧诊断报告不会伪装成功。
- 修正后针对性 **81 tests，16.889s，OK**；正常 pre-push 完整
  **931 Windows tests，51.633s，OK**（无失败/skip，仅既有 Tk teardown stderr 警告）。
- [run 34701509226](https://github.com/mclight-ship-it/cc-translate/actions/runs/34701509226)，
  SHA `826ba99571d572ca059ba02134797c79e1e9584a`，**success**，job 1m37s。
  Mac 便携 **145 tests，6.693s**；普通 XCTest **31 通过 + 唯一初次包内集成 skip**，
  构建后 Foundation.Process 集成 **1 test，0 skip，0.474s**；
  随包 Python 同源测试 **73 tests，0 skip，0.316s**。原生进程组五项回归持续通过；
  release build、完整许可/Mach-O、词典/HTTPS/SQLite、取消/EOF/不可变审计全部通过。
- 已下载核验 artifact `10299469909`：clean manifest 对应正确 SHA，八份共享模块/契约/probe
  与 Git blobs 逐字、哈希一致；**5 arm64 Mach-O / 652 库存项**，没有捆绑真实词库数据库。
  `helper-smoke.json` 的 dictionary 四字段、清理/不可变均通过；Python **3.12.14**。
  临时 app zip 已清理，仅会话目录留脱敏 JSON。实机交接更新到该固定制品，
  仍不代表首次 TCC/签名/真实账号完成。

### Codex 只读配置探针监督（2026-09-12 UTC 已验证）

- 已按依赖先拆步骤，再接实际 `read_native_config` Darwin 调用链；未同时移植 exec、
  常驻 app-server、Claude 或 catalog。Windows 原默认路径保持，新增可选取消参数不会被其他
  平台静默忽略；只为已接入的 Darwin 分支提供事件取消。
- 复用 C 已验证的 `waitid(WNOWAIT)` / 只查自有组的 zombie-only EPERM 处理，新增 ABI 1
  项目自有 dylib 随包。动态库固定路径/ABI/必要资源/Mach-O 检查，缺失即失败，无宿主回退。
  每次组信号先证明仍是本进程子进程，Python 收到 ECHILD 后不再发信号或 wait。
- stdin/stdout 非阻塞，8 秒 RPC / 8 MiB 接收预算；取消、错误、超时和正常返回都只有一处
  TERM→KILL→wait/关闭。没有 reader daemon；不自动重试、不 source profile、不执行 auth helper/
  MCP/model turn。实际 native loader argv/env/cwd/merged layers及原安全覆盖不改变。
- 现有显式 runtime probe 在真正包内 Mac 中实际调用一个临时 synthetic app-server，
  验证两条非模型 RPC、完整环境字段与返回 routing，并将取消事件接入 helper EOF 清理；
  宿主/Windows 只报告 not_run，不能充作 Mac 证明。原生协议、smoke 和诊断标签同步接入。
- 新增十项 Windows 可运行的 dispatch/ABI/清理顺序/幂等/权限与所有权失败/取消契约回归；
  Mac 专属九项真实进程测试在 `macos/PythonTests`，只由包内 Python 显式执行且禁止 skip/
  宿主替代，包含 TERM-resistant 后代、提前退出、静默超时、输出洪泛、取消、错误脱敏和
  helper 真 EOF；旧原生版本探针五项进程回归保持。
- 针对性 **147 tests，18.667s，OK**；下一步正常完整 hook 与真实 Mac build/进程/bundle
  验证。此时还没有新的 Mac 通过证据，不能把源码接入或模拟测试当作阶段验收完成。
- 补齐 setup 失败脱敏、helper 取消接线和最终装配后再次针对性 **148 tests，20.655s，OK**。
- 尚未解决：catalog 路径/logger 的 cc_core 耦合、完整 exec/app-server/Claude 生命周期及
  paid-turn 快照/取消接线；真实用户账号、签名/首次权限仍单独待验。没有新增付费请求或大 UI。
- 正常 pre-push 完整 **941 Windows tests，55.497s，OK**，无失败/skip（仅既有 Tk teardown
  stderr 警告），隐私扫描与 Python 编译通过。
- [run 34703866435](https://github.com/mclight-ship-it/cc-translate/actions/runs/34703866435)，
  SHA `951f4f7a2a8b7e03953a6ec61c7d34a86ae40e78`，**success**，job 1m42s。
  Mac 便携 **145 tests，6.876s**；普通 XCTest **32 通过 + 唯一初次包内集成 skip**；
  新增包内真实配置进程 **9 tests，12.128s，0 skip**（含取消、洪泛、helper EOF 及兄弟存活）。
  构建后 Foundation.Process 包内集成 **1 test，0 skip，0.894s**；
  包内同源核心 **73 tests，0 skip，0.275s**。旧原生五项自有组回归持续通过；
  release app/共享库、完整许可/Mach-O、词典/配置 fixture/HTTPS/SQLite、取消/EOF/不可变均通过。
- 已下载核验 artifact `10301102393`：clean manifest 对应上述 SHA，12 份共享模块/
  provider 白名单/probe 资源与 Git blobs 逐字及哈希一致；**6 arm64 Mach-O / 657 库存项**。
  项目自有 dylib 最低目标 **14.0**，仅依赖系统 libSystem，安装名可在包内解析；
  实际 dylib SHA-256 与审计库存一致：
  `a9320393f7421ac122398bb0d02b2ff14ba20798077b463f39cf71701d6bb21e`。
- 包内 Python **3.12.14** 的 `codex_config_fixture` 四字段、清理/不可变全部通过；
  provider 包只含三份纯契约 + 两份 config reader + 原 instructions，不含 exec/app-server/
  Claude/catalog。临时 zip 已清理，脱敏 JSON 留会话目录，未执行任何用户真实 CLI/账号。
  文档-only 收尾固定该已通过代码，不额外反复运行相同 CI。

### Catalog 显式存储与日志切片（2026-09-12 已验证）

- `CodexCliProvider` 构造直接转交既有 manager，实际 build_command 回归不再覆盖私有 manager。
  stream/warm 继续共享；默认 APPDATA/展开 HOME 路径和延迟 logger 不变。
- 原 `_models/_read/_atomic_write/_run/overrides/_resolve/_validate` 七个函数 AST 完全一致；
  未改 fingerprint、TTL、配置层保护、缓存命中/重开校验，也不放行真实 catalog CLI。
- 显式 runtime_probe 接入合成 catalog：CLI 输出模拟，缓存实际写入/重开/roundtrip；
  错误不激活 state，日志只接收固定错误码，不触碰用户配置、真实模型或真实认证。
- 首轮 Windows 120 项有 1 error：隔离 smoke 未提供 Windows `Path.home` 所需 USERPROFILE。
  补为合成 home 后，121 项仍有 1 error：正确触发了实际祖先配置保护。
  最终 Windows smoke 保留原平台用户主目录边界（只用于跳过该祖先的 project 检查），
  catalog 的 CODEX_HOME、cwd/cache 仍全部显式为临时 fixture；没有绕过配置保护。
  缺少平台 home 现明确转为固定 `catalog_fixture_failed`，不再漏出线程 traceback。
- 修正后针对性联合回归 **122 tests，OK，21.783s**；新增项目配置拒绝、原日志/路径、
  真存储/fingerprint/TTL/失效/失败退避/原子替换失败/隔离审计共 9 项。
  isolated 子进程阻止 cc_core/Tk/Win32 导入、进程/网络及 fixture 外写入；报告不含路径。
- 首轮代码 `6473de470dc5ccd798a24f2c026ef081dc9e6229` 正常 pre-push **950 tests，OK，54.864s**，
  无 failure/skip，只有既有 Tk teardown stderr 警告。
  [34704980796](https://github.com/mclight-ship-it/cc-translate/actions/runs/34704980796) 全 steps success：
  Mac 154 / 包内 native config 9 / 包内核心 82；构建后集成 1 真通过，0 skip。
  但逐项核验发现新增 catalog XCTest 错误嵌套，未被 XCTest 发现；普通测试仍为旧 32 通过
  + 1 初次集成 skip。不能把这一轮当成新增负例的验收；已修正为类成员，由下述新 run 明确执行。
- 修正源码 `96dbaa9350975a647533eaecba5bcb8d2d1cd1fe` 的正常 pre-push 再次
  **950 tests，OK，56.416s**，只有既有 Tk teardown stderr 警告。
  [34705266928](https://github.com/mclight-ship-it/cc-translate/actions/runs/34705266928)
  **success**，job 1m42s。实际日志明确记录新增 catalog XCTest started / passed（0.005s）。
- 最新 Mac 宿主便携 **154 tests，6.519s**；普通 XCTest **34 总数 = 33 通过 + 1 初次包内集成 skip**，
  0 failure；构建后 Foundation.Process 包内集成 **1 真通过，0 skip，0.747s**。
  包内 native config **9 tests，12.180s**，包内同源核心/存储 **82 tests，0.423s**，均 0 skip。
  app/C 库构建、许可/Mach-O、HTTPS/SQLite/词典/config/catalog、取消/EOF/不可变审计全部通过。
- 已下载核验 [artifact 10301448938](https://github.com/mclight-ship-it/cc-translate/actions/runs/34705266928/artifacts/10301448938)：
  clean manifest 对应上述 SHA，**14 份 Git blobs / 49 份资源 hash / 6 arm64 Mach-O / 659 库存项**。
  provider 白名单只新增 catalog 文件，不含 exec/app-server/Claude 或真实词库/账号数据。
  包内 Python 3.12.14 的 `catalog_storage_fixture` 四字段及清理/不可变均通过；
  明确 `cli_simulated=true`，没有运行真实 catalog CLI。两轮临时 zip 已清理，脱敏 JSON 留会话目录。
- 此历史记录是可重复的 P1 存储依赖检查点，不是全部 P1 完成；当时按收尾指令暂停下一进程监督依赖。
  当时用户 Mac/签名资格未确认，普通下载首开/真实 TCC/账号仍未验，不因该轮绿色改为通过。

## P2 — 等待 P1
- [ ] 双击 Cmd+C 关联状态机及保守复制回退；不吞复制、不哨兵、不读历史。
- [ ] 原生结果/输入、IME、流式合并刷新、选择滚动、取消、迟到事件隔离。
- [ ] 词典/普通翻译/代码解释/摘要/重译/复制完整闭环。
- [ ] 来源按钮稳定 identity，按下时异步更新不吞 click。

## P3 — 等待 P2
- [ ] 同帧区域截图、多显示器坐标转换、Vision 语言与视觉 provider 明确发送。
- [ ] URLSession 下载；核心校验安装/删除互斥；离线/损坏/取消。
- [ ] 历史/搜索/筛选、设置/主题/语言、关于/完整第三方许可、诊断。
- [ ] 纯文本粘贴；剪贴板多格式/延迟数据/Universal Clipboard/访问拒绝验收。

## P4 — 等待 P3（可行性已在 P0 提前检查）
- [ ] 免费分发完整性/资源/归档检查、最小权限和干净用户 Gatekeeper 首开；不全局关闭保护。
- [ ] 可选付费增强（未选择、未通过）：Developer ID、公证/stapling；不得作为购买要求。
- [ ] SMAppService 实际状态、独立 Mac 资产/版本与 Sparkle 更新签名。
- [ ] N→N+1 更新保护数据/权限；失败/取消/重启；不在 bundle git pull。
- [ ] 卸载可选清理自身数据，不删除共享 CLI/账号/Node。
- [ ] 应用自身许可确认；Python/依赖/词典/更新框架完整许可齐全。

## P5 — 等待 P4
- [ ] 第一轮平台探针验收（应在 P0 尽早组织）。
- [ ] 第二轮完整候选：TextEdit/Safari/Chrome/PDF/VS Code/Terminal、IME、多屏全屏。
- [ ] 固定样本分类/IPC/词典/首屏 P95，模型首字/完成 A/B。
- [ ] 空闲 CPU/内存/长稳/休眠，拒绝权限、并发 UI 时序、provider 失败。
- [ ] 预授权 CI 与首次用户 TCC 区分；未解决高优故障为零。

## P6 — 等待 P5 和另行发布授权
- [ ] Mac 安装说明、截图、已验 CLI/OS/CPU 范围、已知局限与诊断。
- [ ] 用户确认后才推送发布渠道/Release；不影响 Windows 产品。

## 历史冻结交接（2026-09-12 UTC；首次测试路线已由 2026-09-13 更新）

**当时指令：完成现有切片后暂停新增代码，下一阶段先由协调者落实正常下载包签名/首次实机入口。**
这取代前面历史记录中的“立即继续下一切片”，不把暂停伪称所有剩余工作都被签名阻断。
随后获准的收尾仅同步既有监督范围的旧文案：取消状态、CLI 提示及对应注释/指南；
不更改 App/delegate 生命周期、退出回调或进程算法，不扩大 UI。该源码文案修正另跑正常 CI。

- Codex 只读配置监督已于 `951f4f7` / run `34703866435` 完整通过；不是尚未实施的工作。
  最新源码 `eec92a5794dd9a78ccf91f6f594e0d189e44d4e1` / run `34706318638` 再次运行了全部
  9 项 Mac 真进程测试（含 helper EOF）并通过，配置监督源码/C ABI/该测试文件相对前轮无变化。
  “只读”指 initialize/config/read RPC；不承诺未测官方 CLI 初始化零磁盘或认证副作用。
- 本轮同时已完成分类、方向、词典触发/提示词、provider 纯契约、只读词典生命周期、
  原生版本探针自有组监督，以及 catalog 显式 cache/logger 与合成存储回归。
  它们均有上文真实 Windows/Mac 证据；P0 界面仍仅 fixture/诊断，非完整翻译。
- 最新 Windows 正常 hook **950 tests，OK，60.336s**；Mac **154**，普通 XCTest **33 通过 +
  1 初次集成 skip**；后置包内集成 **1 真通过、0 skip**，包内配置 **9** / 同源核心 **82**，
  无未修复失败。资源/许可/Mach-O/HTTPS/SQLite/清理和不可变审计均通过。
  源码/code SHA 与文档-only 收尾 HEAD 分开；本次仅更新交接，不重复触发相同源码 CI。
- 最新 [run](https://github.com/mclight-ship-it/cc-translate/actions/runs/34706318638) /
  [artifact 10301738307](https://github.com/mclight-ship-it/cc-translate/actions/runs/34706318638/artifacts/10301738307)
  已核验；artifact 于 **2026-09-19 16:49 UTC** 到期。下载 zip 均已清理，仅会话目录保留脱敏 JSON。
  开发分支保持正常提交/非 force 推送，不动 master、Windows 正式应用或 Release。
- 文案收尾前一轮 `397380a` / run `34705975202` 已全绿（Windows 950、Mac 154、Swift 33 通过
  + 初次 skip 1、后置集成 1、包内配置 9 / 核心 82），该轮修正了两处过时范围描述。
  最后按用户指定原文，仅改 `ProbeModel.swift` 取消提示为
  `Cancelling the selected CLI and its owned process group...`；
  提交 `eec92a5` 的实际 diff 明确包含该文件，只有一行文案变化。
- 最后验证：最新 run **success，job 1m29s**；Mac 便携 **154 / 5.841s**，
  普通 XCTest **34 总数，1 初次 skip，0 failure**，后置集成 **1 / 0.654s，0 skip**；
  包内 native config **9 / 11.940s**，同源核心 **82 / 0.366s**，均通过。
  核验 **14 Git blobs / 49 资源 hash / 6 arm64 Mach-O / 659 库存项**；
  实际编译 App 中指定取消文案存在，旧的 direct-child 取消文案不存在，逃离组 wrapper
  不支持的提示仍保留。没有更改生命周期、进程或 UI 行为。
  同组后代受监督、主动逃离组的 wrapper 不支持；本次没有扩大保证或改变生命周期逻辑。
  本轮委派到此结束；除本次验证记录收尾外停止任何新增实现。后续 P1/P2–P6 不属于本轮。

### 历史：2026-09-12 当时尚可独立推进，但主动暂停

- 真实 catalog version/debug-models 的实时输出限额、期限/取消与自有进程组监督；
  可用 fake CLI 做 Mac CI，不需要签名或真实账号。当时只有存储隔离完成，尚无该进程实现；
  后续已完成 catalog 监督，当前状态以 P1 清单和末尾可靠检查点为准。
- 现有纯核心的额外边界/错误矩阵、协议资源契约与离线故障回归；仍可自动验证。
- 配置/历史路径与唯一写入者、完整 ProviderRuntime 技术上也有可拆分自动化部分，
  但属于本轮明确不再开启的大链路，不是已完成项，也不因缺签名自动变成技术阻断。

### 当时提出的条件（历史；付费身份前置现已取消）

1. 用户 Mac 的实际 **OS 版本/CPU**；Apple Silicon/macOS 15 优先，macOS 14 仅候选、Intel 未验。
2. **Apple Developer Program/团队资格和可用 Developer ID 身份**，不视为已有或批准购买；
   证书/私钥/账号秘密不进入聊天。协调正常签名、Hardened Runtime、公证/stapling 与下载首开验证。
   当前 artifact 没有该证据，不让普通用户猜安装办法或为云构建路线安装 Xcode。
3. **首次 TCC、AX/主动 Cmd+C、焦点/IME/多屏、同帧截图**须在真实 Mac 按开发指南集中验收。
4. **官方 Codex/Claude 的实际版本/native 配置及账号兼容**须另行协调；只读合成测试和
   `--version` 不能证明认证/真实模型可用。P0 五组探针不要求登录；真实账号测试时用户自行登录，
   不上传认证、屏幕或工作内容，不自动提交付费 turn。

上述历史冻结时，完整 P1 尚缺平台配置/历史单写、请求快照、真实 catalog/exec/常驻 app-server/Claude 生命周期、
预热/取消/付费不重试接线和真实词库安装切换等。P2–P6 的完整交互、业务闭环、多屏下载、
发行签名更新、系统矩阵/性能长稳及发布均未完成。**不得把整个移植标完成。**

## 已完成的准备切片：免费 GitHub 分发与首轮实机验证（2026-09-13）

- [x] 采用免费站外分发默认路线；Developer ID/公证是未选择的可选增强，无会员不阻断首测。
- [x] 核对 [Apple 官方 Open Anyway 说明](https://support.apple.com/en-us/102445)
  （2026-05-27 发布）：先正常尝试打开，可信来源的未识别/未公证警告可由用户亲自保存单 App 例外。
  这不是全局关闭 Gatekeeper；没有 Apple 身份/公证保证。
- [x] 指南区分可适用警告与恶意软件/损坏/组织策略阻断；后者停止检查，不指导强开。
  不清 quarantine、不关闭 Gatekeeper/SIP、不 reset TCC、不重签用户下载包、不直跑包内二进制。
- [x] 重新核验现有 artifact 有效性、bundle/Python/项目 dylib/归档模式与哈希；无确凿缺口就复用，
  不凭猜测增加 ad-hoc 签名。若需构建端 ad-hoc，它仅作完整性签名，不代表 Apple 认证/公证，
  不是 Personal Team 七天设备签名，也不要求用户安装开发工具。
- [x] 固定唯一 GitHub 下载、内层 zip SHA-256、到期及最小 Finder/静默/helper/TCC 步骤，见开发指南。
- [ ] 首次真实 Finder/Gatekeeper/TCC 和 macOS 26 兼容性待用户实测。设备自报不等于已验，
  不在公开仓库记录个人主机或身份信息；Intel 未验。跨版本更新是否保留授权另待实测。

该准备切片当时不新增 catalog 进程、exec/app-server、配置历史、完整 ProviderRuntime 或大 UI。
真实账号/模型不参与，P1 其余部分和 P2–P6 仍待办。先最小静默/helper 验证，再集中逐项 TCC；
辅助功能/输入监控/屏幕内容权限与首次打开例外分开。完成本切片后停在待实机结果处。

### 本轮制品审计与复用结论

- 2026-09-13 再次从项目 GitHub 下载并只读核验 artifact **10301738307**，未过期；
  固定 run **34706318638**、源码 **eec92a5794dd9a78ccf91f6f594e0d189e44d4e1**、
  到期 **2026-09-19T16:49:22Z**。不创建新包、Release 或新的源码 CI。
- 内层 **CCTranslateMac-P0.zip**：**18,351,930 字节**；
  SHA-256 **5443c28e048d93da1551c8627f3292528de239dc0a9f63d3bd5516a6d24c450c**。
  唯一用户下载入口为[该 artifact](https://github.com/mclight-ship-it/cc-translate/actions/runs/34706318638/artifacts/10301738307)。
  GitHub API 的外层归档摘要为 `sha256:3e2e4ff83759c4b23d7fa4a3b414f5b2a4eba919766a9065e71fed3423f4f20f`；
  它与内层 App zip 是不同文件，用户流程只需校验内层。
- ZIP CRC、唯一路径/安全路径、659 个文件/链接条目与原审计清单完全一致，
  49 份资源哈希与 14 份 Git 源码 blobs 一致；只含 App 与 ditto 的 AppleDouble 元数据，
  主程序和包内 Python 可执行权限为 0755，`python3 -> python3.12` 是包内相对链接。
- 6 个 Mach-O 均为 arm64；已有 Mac CI 的系统/包内动态链接和 deployment-target 审计保持有效。
  本轮静态核对其 6 个 CodeDirectory（ad-hoc 标志）共 **9,255 个代码页摘要**全部匹配。
  这不是运行 `codesign --verify` 或取得 Apple 认证，也不是 Gatekeeper/TCC 实机验收。
  `.app` 没有完整资源签名 seal；未发现必须修补的压缩/可执行模式/引用或代码页损坏，
  因而不为猜测问题增加构建端重签，不改变旧测试/打包契约。
- 真实包内 Python **3.12.14**、SQLite **3.53.1**、OpenSSL **3.5.8** 和项目 dylib 已在原 Mac run
  实际通过 helper/HTTPS/SQLite/config/catalog/取消/EOF；本次核对原 smoke 的清理和不可变证据。
  路径来自 Bundle 自身而非开发机固定路径；用户无需安装 Xcode/Python/Git/Apple 开发者账号。
- 复用的验证数仍是：Windows **950**，Mac 便携 **154**，普通 XCTest **33 通过 + 1 初次 skip**，
  构建后集成 **1 真通过/0 skip**、包内 config **9** / 核心 **82**；没有将旧运行冒称新测试。
  本次仅改文档并进行下载归档核验，未运行新的单测/构建或 Mac 二进制，不以预授权 CI 代替首次测试。
- 原报告 `development_only=true`、`release_gate=NOT PASSED`、`not_tested` 保持原样；
  它们诚实表示完整产品/旧付费增强/人工门槛未通过，不表示免费官方单 App 例外路线被禁止。
  缺少付费身份不再是阻断；下载首开、macOS 26 兼容性、真实 TCC 和跨版本权限保留仍待实测。
- 临时下载 App zip 已清理，仅会话目录保留原始脱敏审计/smoke JSON；本地未安装或启动 Mac 程序。
  文档提交采用 `[skip ci]`，正常 hooks/非 force 推送，不把无源码变化的 CI 跳过当新验证。

## 首轮正向实机报告与下一单一切片（2026-09-13）

- [x] 匿名用户实测证据归属 **eec92a5794dd9a78ccf91f6f594e0d189e44d4e1** /
  [run 34706318638](https://github.com/mclight-ship-it/cc-translate/actions/runs/34706318638) /
  artifact **10301738307**，不归属文档 HEAD 或未来代码。详情见[指南的用户报告表](MACOS_DEVELOPMENT.md#首轮匿名用户报告2026-09-13仅原始固定包)。
- [x] 用户确认顶部菜单静默启动；离线包内 runtime（arm64/darwin、Python 3.12.14）、
  SQLite 3.53.1 读写、OpenSSL 3.5.8 / bundle CA / 证书验证、词典只读/重开/来源、
  config fixture 方法/路由、catalog 合成存储 cache/reopen 通过。离线 HTTPS not_run 是预期，
  随后显式 HTTPS 用户回报 passed。
- [x] 授予 AX 后 PRESENT/合成文字正确，面板不抢 TextEdit 输入焦点；双 Cmd+C 及普通粘贴正常，
  Stop 后不再触发新 B 结果；主屏保留 A 帧在屏幕改 B 后 OCR 仍为 A，Cancel/clear 清空图文。
- [x] 用户报告正常 Quit 菜单消失、Finder 重开正常。没有人工进程树检查，不能证明全部后代退出。
- [ ] 尚无独立 OS 版本佐证；只记录匿名 Apple Silicon / macOS 26 候选自报，不作完整兼容结论。
- [ ] 未观察 Open Anyway，缺少干净用户/quarantine 来源证据；用户未明确回报内层 hash MATCH
  或单独 synthetic-stream 结果，不能用此前 CI/静态核验代替这些用户检查。
- [ ] 授权前 UNKNOWN、各权限拒绝/重启、多屏/Spaces/IME/完整焦点矩阵、跨版本授权保留未验。
- [ ] 用户无 Codex/Claude CLI；版本、真实账号/native 配置/模型 NOT RUN，不要求现在安装。

首轮正向探针完成不代表全部 P0/P1 完成。免费 GitHub/零付费预算及官方单 App 例外路线不变。
旧“暂停 catalog”现仅由本次单一切片授权取代；不启动 exec/app-server、Claude、UI、配置/历史改造。

### Catalog 真进程监督：按依赖完整接入与验证

1. [x] 从现有配置监督提取实际共享的 C 自有组/pipe/退出观察边界，配置原行为保持；
   leader 未回收前完成 TERM/KILL/reap，ECHILD 后不得再 signal/wait，不追杀逃离组进程。
2. [x] 接入现有 catalog `--version`/`debug models` Darwin 调用，明确 stdout+stderr 实时总限额、
   期限、取消、helper EOF；取消/监督错误不得被缓存降级吞掉后继续请求。Windows 默认路径不变，
   cache/logger/解析/路由/override 安全语义不变。
3. [x] 隔离 synthetic CLI 走真实 catalog 冷缓存及磁盘重开路径，不伪造 validated 状态；
   包内 helper/smoke/Swift 严格协议与负例同步，真实 Mac 验证正常/失败/超限/期限/取消/EOF、
   后代清理及无关 sibling 存活，确认 XCTest 被发现执行。
4. [x] 针对性 Windows、正常 hooks、唯一分支推送、免费 Mac CI 与包内资源审计真实通过，
   记录新源码/文档 SHA、run/artifact/hash；失败保留并修复，切片完成后停在可靠检查点。

### Catalog 实现与首次本地检查（历史；实际 Mac 结果见下）

- 配置与 catalog 现在共同调用 `darwin_process.OwnedProcess` 的固定包内 ABI-1 桥接与唯一清理所有者；
  配置 RPC 协议保持不变。catalog 双非阻塞 pipe 总预算 **8 MiB**，每个 CLI 探针 **8 秒**，
  取消检查间隔至多 50ms（清理另需 TERM 后 200ms、KILL 与至多 2 秒 wait）。
  leader 早退时先处理同组后代再 drain EOF；关闭 pipe 但未退出仍受期限控制。
- catalog 的事件仅在 manager 锁内绑定到当前请求，退出作用域清空；等锁也可取消/限时。
  所有监督错误为固定 `CatalogProbeError`，不被原 native-discovery 降级捕获。
  Windows 默认仍执行原 `subprocess.run`；显式在非 Darwin 给 catalog 新取消参数会拒绝而非忽略。
- 现有 provider command-building 仅转交 Darwin 的取消事件并接收该固定错误，防止失败后提交请求；
  没有改写 exec/app-server/Claude 的长驻进程或模型 turn。其完整监督仍待后续阶段。
- `_models`、`_read`、`_atomic_write`、`_resolve`、`_validate` 五个函数的 AST 与 `a6f64f4` 完全一致：
  版本、TTL、fingerprint、缓存/解析/路由、冷三次及新 manager 磁盘重开 roundtrip 算法不变。
- 新 `catalog_process_fixture` 使用包内 Python 创建临时合成 CLI，真实走 catalog 原路径，
  不替换 `_run` 或填充 `_validated`；原 `catalog_storage_fixture.cli_simulated=true` 保留。
  runtime 新独立字段包含 `fixture/process_verified/cache_verified/reopen_verified`，非包内 Darwin 为
  `not_run`；Swift 严格键/布尔/平台规则、负例与包内集成/smoke 同步，不增加 UI 功能。
- 首次 Windows 针对性命令误选两个不存在的测试模块：142 项中 2 个 import errors；
  没有安装包或隐藏失败。改用现有 `tests.test_providers` 后 **196 tests，OK，20.655s**；
  加入最终 ECHILD 负例后联合复跑 **197 tests，OK，20.475s**，覆盖 config/catalog、原 provider、
  便携存储/导入、协议与打包。当时仅静态确认 Mac 新 suite 的 16 个顶层测试方法；
  随后已按下表完成真实执行，不将这次静态检查算作 Mac 通过证据。

### Catalog 可靠检查点（2026-09-13，已完成本次单一切片）

- 原包匿名用户报告已先以文档提交 **a6f64f4e420c762fb3ff3702b64f93f5817c8968** 正常推送；
  该报告仍只属于 `eec92a5` / run `34706318638` / artifact `10301738307`。
- 新执行源码 **a0c2df6fe7fa41d8e6c8034cfdc9303a6636453b**，正常 hooks 完成 privacy/编译及
  **959 Windows tests，OK，56.867s**，无失败/skip；只有既有 Tk teardown stderr 警告。
- [run 34761449362](https://github.com/mclight-ship-it/cc-translate/actions/runs/34761449362)，
  **success，job 2m30s**，标准免费 macos-15 arm64、固定 Xcode 16.4。此次真实 Mac CI 无失败重跑；
  前述本地选错两个测试模块的错误记录保留，未跳过断言或绕过 hooks。

| 实际执行 | 结果 |
|---|---|
| Mac 便携/打包及 Darwin host contracts | **173 tests，OK，7.316s** |
| 普通 XCTest | **35 总数：34 通过 + 1 初次包内集成 skip，0 failures** |
| 新 catalog 协议 XCTest | `testCatalogProcessRequiresCompleteSyntheticProcessEvidence` 明确发现并通过，0.008s |
| 包内 Python 真进程 | **25 tests：9 config + 16 catalog，OK，52.830s，0 skip** |
| 构建后 Foundation.Process 强制包内集成 | **1 test 真通过，0 skip，2.347s**；不是将初次 skip 当通过 |
| 包内同源纯核心 | **82 tests，OK，0.446s，0 skip** |
| bundle、完整许可/资源/Mach-O、HTTPS/SQLite、取消/EOF、不可变审计 | 全部通过 |

16 项 catalog 真进程用例逐项实际执行：包内路径/真实桥接、冷三次/命中不启动/重开一次、
早退成功和非零退出、TERM-resistant 后代持 pipe、关闭全部输出但 leader 仍活、
静默及 roundtrip 八秒期限、stdout/stderr/双流共享八 MiB 洪泛、开始后的取消、预取消隔离、
原始 stderr 不外泄、真实 helper EOF 与 stdin 未关闭时协议取消、固定诊断入口。
每例验证独立 sibling 存活；leader 已回收，孤儿后代只接受消失或 launchd 名下不可运行 zombie。
测试不在失败后凭记录的 PID 补杀/补 wait，不替生产代码回收后伪造通过。
ECHILD 的首信号、末信号和非回收观察路径另由 host contracts 检查，禁止后续 signal/wait。

- 新[artifact 10319141756](https://github.com/mclight-ship-it/cc-translate/actions/runs/34761449362/artifacts/10319141756)
  名称 `macos-arm64-p0-development-NOT-A-RELEASE`，已核验未过期；到期 **2026-09-20T14:03:44Z**。
  内层 **CCTranslateMac-P0.zip** 为 **18,356,322 字节**，
  SHA-256 **26803ef34f2b07bb55491a570991f615538321eefb21a1ad823f0c7a54a47881**。
  GitHub 外层 artifact SHA-256 为 `4e550d17ddf00fcace3be8f85e858d4a21eec4849ec81a2698972bdfbb3491fd`，
  不与内层值混用。
- 独立下载复核 **661 库存项 / 51 资源哈希 / 16 Git blobs / 6 arm64 Mach-O**，
  source manifest 为上述源码 SHA 且 clean；ZIP CRC、唯一路径、安全相对链接和执行模式通过。
  原存储 `cli_simulated=true` 和新增真实合成进程 `fixture/process_verified/cache_verified/reopen_verified=true`
  同时存在；smoke 的 handshake、fixture、explicit_cancel、eof_cancel、bundle_unchanged、
  probe_files_cleaned 均为 true。未运行用户真实 CLI、登录、模型请求或修改用户配置。
- 只读生产差异复核未发现直接引入的高置信错误；临时 App zip 已清理，仅会话目录保留脱敏 JSON。
  最后文档-only 提交使用 `[skip ci]`，正常推送，不为未变源码重跑 CI。

**历史 catalog 委派在此暂停新增代码，随后另行授权下述纯规则切片。**
该 catalog 包只经过自动化，不能继承旧包的用户正向实测或 macOS 26 兼容性。
仍无用户 CLI/账号/模型验收，不要求现在安装。干净用户/quarantine/Open Anyway 实际路径、
权限拒绝/重启、多屏/Spaces/IME、跨版本权限保留和人工进程树检查继续待办。
完整 P1 仍缺配置/历史单写、请求快照、exec/常驻 app-server/Claude 生命周期、预热/付费不重试
完整接线和真实词库安装切换等；P2–P6 仍未完成。技术上可独立回归的小项不因签名而被阻断，
只是本次单一切片已结束，不自动开启下一项。免费 GitHub/零付费预算、不发布 Release/不动 master 或 Windows 部署不变。

## 当前单一切片：共享缓存签名与历史类型规则（2026-09-13，已完成）

catalog 检查点及其只读 review 已完成，不重做等价监督改造。本次仅获准完成下列纯规则前置，
不创建完整请求快照、配置/历史 writer 或 UI，不改模型 prompt/缓存版本、不清用户缓存。

1. [x] 将签名字符串和 history-kind 优先级放入无副作用的 `cc_result_rules.py`；
   UI 保留 route/本地对象状态、配置默认、i18n fallback、provider selection 与注入边界。
   本地签名不查询 provider；字段顺序、字符串转换/真假判断、可选 revision 与异常传播保持。
2. [x] 原 `_history_meta` 主线程捕获、缓存命中、截图/本地与 AI 词典/摘要消费者使用真实 wrapper；
   不重写 metadata dict。history kind 复用 `cc_classify.is_single_word`，保留测试替换该判断的语义。
3. [x] 旧实现差分及固定字节矩阵、惰性优先级/错误传播、隔离导入无用户磁盘网络访问；
   包含同源模块的 Mac 包用 `-I -B` 实际跑相同纯规则测试，不为此扩展 runtime JSON。
4. [x] 正常 Windows targeted/hooks、唯一分支推送、标准免费 Mac CI、制品资源/内层 hash 审计；
   回写准确源码/文档 SHA 与计数后清理并停止，不继承 `eec92a5` 的旧包用户实测结论。

### 规则实现与本地验证（阶段记录，最终 Mac 证据见下）

- 三个共享入口为 `local_cache_signature`、`provider_cache_signature`、`history_kind`，
  只依赖已有 `cc_classify.is_single_word`。Windows 原方法调用同一入口，未增加版本/转义/规范化。
  UI 按原顺序解析并转换 model/direction/summary/language，再查询 revision；
  核心接收已解析字符串，不再次执行 `str` 或 model auto fallback；
  local 路线保持原短路，OCR/code wrapper 不读取较低优先级状态，单词判断仍可按旧入口注入。
- 旧两方法以 `855b73f` 为冻结差分基准；已静态比对 AST（仅忽略名称/docstring）完全一致。
  纯规则包含固定 UTF-8 字节矩阵、2,160 个 provider 字段组合、缺省/空值/异常/优先级检查。
  `_history_meta`、`_show_loading`、`_show_result`、`_do_translate`、`_system_prompt_for`
  五个真实消费者/提示词方法 AST 未变；`ProviderRequest` 和 metadata dict 无改动。
- 新模块纳入 bundle 必需文件/资源哈希清单、缺失和篡改回归及包内同源测试；
  isolated 导入测试禁止平台/provider/Tk、用户数据读取/写入或联网。没有新增 IPC 字段。
- 首轮便携/隔离/打包 **50 tests，OK，5.963s**；加入完整字段组合及既有 Windows
  词典/缓存/历史元数据/截图与 job-isolation 消费者后 **112 tests，OK，7.813s**。
  当时尚未执行新增 Windows 差分/完整 hooks/Mac，后续结果分别记录如下。
- 新 Windows 差分首轮 **26 tests，25 pass，1 个测试含 1 FAIL + 1 ERROR**：
  核心曾二次执行 model 转换，把已转换空字符串改成 auto，且对 str 子类触发额外异常。
  已修正为只拼接 caller-resolved 字段，保留空值/子类字节和 UI 原求值顺序；
  保留原失败用例，并增加其他已转换字段的回归，不改变旧缓存协议。
- 修复后首次联合 **140 tests，2 个失败 subtest**：便携负例尚把未解析 model=None 传给
  显式字段接口，导致在 revision 之前报错。现使用与原 UI 解析结果一致的 model="auto"，
  保留对非法 provider/revision 的原异常类型、完整消息和字段序号断言。
- 最终针对性联合 **140 tests，OK，7.230s**，包含 **9 项**便携规则和
  **27 项**新增 Windows wrapper/真实消费者回归；原有失败断言全部保留并通过。

### 共享规则可靠检查点（2026-09-13）

- 实际执行源码 **fa6a0b87a6d9caa6e6b863cd850060518e5a1d51**；
  正常 privacy/编译/pre-push 完整 **995 Windows tests，OK，58.240s**，无失败/skip，
  只有既有 Tk teardown stderr 警告。仅推送开发分支，没有绕过 hooks。
- [run 34762885485](https://github.com/mclight-ship-it/cc-translate/actions/runs/34762885485)
  **success，job 2m18s**；标准免费 macos-15 arm64、Xcode 16.4，
  image `20260907.0337.1`。本切片真实 Mac CI 一次通过；本地失败与修复记录保留。

| 实际执行 | 结果 |
|---|---|
| Mac 便携/打包与既有 Darwin contracts | **182 tests，OK，5.760s** |
| 普通 XCTest | **35 总数：34 通过 + 1 初次包内集成 skip，0 failures，9.140s** |
| 包内 Python config/catalog 真进程 | **25 tests（9 + 16），OK，52.619s，0 skip** |
| 构建后 Foundation.Process 强制包内集成 | **1 test 真通过，0 skip，2.490s** |
| 包内 `-I -B` 同源纯核心 | **91 tests，OK，0.402s，0 skip**；新增规则 9 项逐项发现并通过 |
| 构建、资源/许可/Mach-O、HTTPS/SQLite、取消/EOF、不可变及归档 | 所有 steps 通过 |

- 新[artifact 10318888821](https://github.com/mclight-ship-it/cc-translate/actions/runs/34762885485/artifacts/10318888821)，
  名称 `macos-arm64-p0-development-NOT-A-RELEASE`，已核验有效，
  到期 **2026-09-20T14:33:14Z**。内层 **CCTranslateMac-P0.zip，18,357,050 字节**，
  SHA-256 **a2fec6d9205b44baf858f2d621cf0dbdf4ab9a655285458d26b087bca7474cb8**。
  GitHub 外层 artifact SHA-256 `6e7c763f2144e6855bbe7d140ec1fb1de82a0cec2a4fdd5778c0dcd0e9729013`
  是另一层归档的摘要，不与内层值混用。
- 独立下载复核 **662 库存项 / 52 资源哈希 / 17 Git blobs / 6 arm64 Mach-O**；
  新 `cc_result_rules.py` 与上述固定 Git blob 字节相同，source manifest 为该 SHA 且 clean。
  19 份 runtime 许可、605 个保留文件的原 full-build 覆盖记录仍在；ZIP CRC、唯一路径、
  相对 python3 链接及 App/Python 的 0755 模式均通过，不在 Windows 冒充执行 Mac 二进制。
- 包内 Python 3.12.14 / SQLite 3.53.1 / OpenSSL 3.5.8，隔离、禁 bytecode、HTTPS 证书验证通过；
  handshake、fixture、explicit_cancel、eof_cancel、bundle_unchanged、probe_files_cleaned 均为 true。
  未扩 runtime JSON/Swift 契约，没有新增真实 CLI、账号、模型调用或写入用户配置/历史。
- 临时 App zip 已删除，只保留会话内脱敏审计 JSON。最后证据文档单独正常提交/push，
  纯文档使用 `[skip ci]`，不为未变源码重复 CI；源码与文档提交在交接时分别标识。

**本次单一切片结束，暂停新增代码。** 不要求用户现在重装或安装 CLI；
首轮用户正向报告仍只属于 `eec92a5` / `34706318638`，新包只有自动化证据。
这不是完整 RequestSnapshot、P1、P0 或产品完成：平台路径/配置历史单写、完整快照、
exec/常驻 app-server/Claude 生命周期及真实词库安装切换、完整 P2–P6 仍待办。
后续纯规则/显式路径的小步与 synthetic provider 回归技术上仍可独立推进，不因付费签名而阻断，
但不属于本次委派。真实用户 CLI/账号、干净首开与官方例外路径、权限拒绝/重启、多屏/Spaces/IME、
跨版本授权和 macOS 14/26 完整兼容仍需后续集中验证。
GitHub 免费分发/零预算不变，Developer ID/公证只是未选择的可选增强；不发布 Release、不改 master/Windows 部署。
