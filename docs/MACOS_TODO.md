# macOS 实施与验收清单

设计和安全契约：[MACOS_DEVELOPMENT.md](MACOS_DEVELOPMENT.md)。
基线：`148f7a1`；仅独立开发分支。更新日期：2026-09-12。
当前已获准提交/正常推送 `agents/cc-translate-macos-native` 并使用公有仓库的标准免费 Mac CI；
首次云验证曾被 GitHub OAuth `workflow` scope 阻断；用户完成授权后已正常推送，
真实 Mac 自动化工程门槛通过（run/SHA 见下）。正式 P0 实机/签名门槛仍未通过。
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

## P0 — Mac 自动化工程门槛通过；实机/签名门槛未通过

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
- [x] Finder CLI 候选路径发现及显式 `--version` 探针代码；直属 PID 监督，遗留后代的 wrapper 不支持。
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
- [ ] Developer ID + Hardened Runtime + 公证/stapling 后干净用户 Finder 运行探针。
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

已运行 macOS 编译和 CI；尚未运行真实用户 GUI/TCC、Developer ID 签名、公证或发布。
上述证据只解锁依赖安全、可独立回归的 P1 纯核心。

## P1 — 分类/方向纯核心切片通过，其他依赖继续待办

Mac 编译/XCTest/原生包内 IPC/Mach-O/HTTPS/SQLite 通过后，可推进独立纯核心抽取。
这不代表正式 P0 的真实 TCC、Finder、签名公证已通过，也不解锁完整 P2–P6 UI。
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
  - [ ] 词典触发切片的完整 Windows hook、最新 Mac 和包内 Python 回归通过并记录。
- [ ] Codex native 配置/认证/目录/工具/hook 边界；严格事件流，不做 exec 假兼容。
- [ ] Claude 独立生命周期/流式/诊断适配；未知认证明确展示。
- [ ] POSIX 本 App 自有进程组取消/回收；warm 不创建付费 turn、请求不自动重试。
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
  已写明具体 run/artifact/SHA、普通下载包尚不能承诺首开、签名缺失条件、本机源码开发路径、
  五组显式探针及手动白名单 TXT 脱敏回报。用户配置和签名条件由协调者统一询问。
- 当前不让普通用户自行去隔离属性、关闭 Gatekeeper/SIP、重签下载包、直跑二进制或脚本重置 TCC；
  无开发环境且无签名分发条件时安装测试明确阻断。纯核心工作不因该人工门槛停摆。
- 收尾仅更新两份文档，正常 privacy hook，提交注明 `[skip ci]`；执行源码/测试/workflow
  与已绿色的 `f526459` 完全相同。下一独立纯核心候选是请求快照/提示词剩余部分与平台路径；
  不把本轮结果误标为全部 P1 或正式 P0 已完成。

### 词典触发纯核心（本轮验证中）

- 新共享入口复用 `cc_classify.is_single_word`，无新增模块；三处 Windows 兼容导出有 identity
  断言。保留原 1–2 token / 30 字符、短 CJK、末尾标点、换行和特殊符号语义。
- 针对性 Windows 联合测试 **130 tests，OK，5.075s**：分类/方向/词典触发、无副作用、
  打包、18 项现有本地词典路由集成及原有词典/摘要边界；没有调用真实 provider。
- 8 个新增边界用例固定现有行为，不把解耦伪装成语言识别算法修复。AST（含 docstring 和
  Unicode 句末标点常量）与原 `cc_core.is_single_word` 完全相同。
- 用户 Mac 芯片/OS 仍未确认，首次实机/签名门槛仍保留。下一步正常 hook 和真实 Mac CI。

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
- [ ] 完整签名清单、最小 entitlement、公证、stapling、干净用户 Gatekeeper。
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

## 下一外部动作

已确认仓库为 PUBLIC、Actions 已启用，授权限于专用开发分支和标准免费 runner。
GitHub `workflow` scope 授权和首次真实 Mac 工程门槛已通过；继续提交/推送并验证独立 P1 纯核心。
真实权限/签名包探针仍待安排。Developer ID、验收 Mac 和 CLI/账号
尚未确认；不要为等待资源而扩张未经编译的 P1–P6 界面。
目前纯核心后续工作不需要用户操作。正式 P0 人工验收时由协调会话统一收集 Mac 的 OS/CPU、
安排本机 CLI 登录和签名身份（凭据不进入聊天），再按开发指南的显式入口集中验证首次权限/
复制/焦点/IME/多屏。没有签名条件时不得把开发制品当发行包，也不得要求绕过 Gatekeeper/TCC。
