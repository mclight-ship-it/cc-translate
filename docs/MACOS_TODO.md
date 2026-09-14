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
  - [x] 同一 Mac15/Xcode 16.4 arm64 制品在实际 macOS 14.8.9/26.6.2 完成包内自动化；
    [run 34765811135](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135)，完整证据见末尾。
    这不是整个 macOS 14+ GUI/权限兼容承诺，也不修改最低 deployment target。

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
  - [x] 路径/原子基础层：显式 Mac home/应用身份解析（不创建/迁移），共享 JSON 原子写入；
    源码 `84ab360` / [run 34764132000](https://github.com/mclight-ship-it/cc-translate/actions/runs/34764132000)
    通过，见[基础层证据](#存储基础可靠检查点2026-09-13)。
    Windows 原入口/默认路径/日志不变，Mac 仅临时合成诊断；不是唯一 writer 或迁移服务。
  - [x] 共享历史仓库与 Windows 兼容入口、add/clear 统一锁、Mac 显式跨进程 owner；
    源码 `c78d8ee` / [run 34768088072](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072) 三系统通过，
    详见末尾历史仓库证据。仅历史 I/O，不等于整个配置/历史服务或业务 helper 接线。
  - [ ] Windows 历史矩阵偶发原子替换拒绝访问的根因：2026-09-14 二十轮复核已复现，
    不是全部通过；新旧历史路径的单次 `os.replace` 均观察到 WinError 5。
    见[复核与阻断记录](#历史矩阵二十轮复核2026-09-14)，未用重试或削弱断言规避。
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

## 共享缓存签名与历史类型规则（2026-09-13，已完成）

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

## 历史检查点：显式平台路径与原子 JSON 基础（2026-09-13，已完成）

上一共享规则切片已验收关闭，不重做。当前仅完成下面的依赖基础层：

1. [x] 无副作用的共享 `cc_storage.py` 接收显式 home 与 application identifier，
   返回 Application Support/Caches 路径；不查环境默认、不创建/迁移、不回退资源或源码目录。
   Mac 应用身份由调用方取现有已校验 Info.plist 的 CFBundleIdentifier，不另建默认常量。
2. [x] 抽取 Windows 实际 `_atomic_write_json`，同目录唯一 temp、原 JSON 字节、
   flush/fsync/replace 与错误/patch seam 保持；配置/history schema、load/save/log wrapper 不变。
3. [x] 包内显式合成诊断使用临时 home，调用路径与真实 JSON 写入/重开/替换；
   同源模块/必需资源/hash/隔离导入、故障矩阵、Windows 消费者与真实 Mac CI 全部回归。
   诊断只由自动化显式调用，不增加业务 UI 或 runtime JSON 字段。
4. [x] 正常 hooks/开发分支推送、记录 source/docs SHA/run/artifact/hash，清理下载包后停止。

原子 replace 只保证单个文件完整可见，不保证跨请求/跨进程 read-modify-write 唯一所有权，
也不等于断电持久性协议。本轮不重构 `_HISTORY_LOCK`/`clear_history`（后者仍未共用锁）、
完整配置迁移/历史服务、请求快照或 provider 生命周期，不让签名费用成为新要求。

### 基础层接入与本地阶段记录

- `cc_storage.macos_user_paths` 只进行词法解析，显式绝对 home 与应用 ID，无 getenv/Path.home/
  resolve/mkdir；拒绝相对路径、父级跳转与 `.app` 内 home。它不是符号链接授权/完整路径所有权服务。
  Mac 自动化从实际 bundle Info.plist 取得 ID，在独立 TemporaryDirectory 中明确创建两种目录，
  用共享 primitive 各执行写入/重开/替换；失败直接传播，成功后由调用方清理整个合成 home。
- Windows `_atomic_write_json` 为同函数兼容导出，原 json/os/tempfile patch seam 不变；
  保留旧 JSON 参数/本目录唯一 temp/flush/fsync/replace/原异常与 best-effort temp cleanup。
  明确保留 raw descriptor 所有权（fdopen closefd=False，finally close），补上 fdopen 失败前的泄漏窗口；
  原 cleanup 失败不遮盖 primary error 的行为不变，不把残留 temp 的故障用例当已清理通过。
- `_resolve_data_dir`、`_user_data_path` 和 load_config/save_config/load_history/add_history/clear_history
  七个函数 AST 与 `5ecc987` 完全一致。没有改变 Windows 的默认目录、迁移或日志行为。
- 首轮便携/隔离 **15 tests，OK，0.401s**；补充路径/目录故障并联合既有
  配置/历史/原子写入、隔离与打包后 **99 tests，OK，6.987s**。
  新 Windows 接线测试、完整 hooks 和实际 Mac/包内入口证据随后记录，不以本地代替。
- 新增 Windows 接线 **38 tests** 全部通过；最终联合 **137 tests，OK，7.225s**，
  含 **16 项**便携存储/故障测试、现有配置/历史、隔离和包审计。
  回归冻结了 12 项既有 Windows AST，使用稳定序列化/hash，不依赖运行时 Git 历史或 ast.dump 版本格式。
  当时本地没有失败测试，后续完整 hooks/Mac 已按下表真实完成。

### 存储基础可靠检查点（2026-09-13）

- 实际源码 **84ab360d61c56875276e73963527721e40c89426**，正常 privacy/编译/pre-push
  完整 **1049 Windows tests，OK，57.418s**，无失败/skip；仍只有既有 Tk teardown stderr 警告。
- [run 34764132000](https://github.com/mclight-ship-it/cc-translate/actions/runs/34764132000)
  **success，job 2m37s**，标准免费 macos-15 arm64、Xcode 16.4，
  image `20260907.0337.1`。本切片 Windows 与真实 Mac CI 均无失败测试或失败重跑。

| 实际执行 | 结果 |
|---|---|
| Mac 便携/打包与 Darwin contracts | **198 tests，OK，6.611s** |
| 普通 XCTest | **35 总数：34 通过 + 1 初次集成 skip，0 failures，9.286s** |
| 包内 config/catalog 真进程 | **25 tests（9 + 16），OK，56.075s，0 skip** |
| 构建后 Foundation.Process 强制包内集成 | **1 test 真通过，0 skip，2.752s** |
| 包内 `-I -B` 同源核心 | **107 tests，OK，0.552s，0 skip**；新增存储 16 项逐项发现并通过 |
| 包内显式存储入口 | 从实际 Info.plist 取 ID，在 TemporaryDirectory 中执行 `probe_storage`，真实输出通过标记 |
| 构建、完整许可/资源/Mach-O、HTTPS/SQLite、取消/EOF、不可变和归档 | 所有 steps 通过 |

新增 Mac 实际执行包含：路径无隐式 HOME/目录创建、相对/父级/Bundle home 拒绝、
Unicode/空格/#/% 路径、JSON 原字节/重开/替换、先 flush/fsync/关闭 FD 再 replace、
fdopen/部分序列化/flush/fsync/replace 失败后的原文件保留与单操作清理、
旧 cleanup 失败仍保留 primary exception、相邻操作 temp 不动、缺目录无回退、
合成诊断拒绝覆盖已有数据目录或写 bundle。诊断不是 stub，也不把其返回值当成功证据。

- 新[artifact 10320055600](https://github.com/mclight-ship-it/cc-translate/actions/runs/34764132000/artifacts/10320055600)，
  `macos-arm64-p0-development-NOT-A-RELEASE`，已核验有效；到期 **2026-09-20T14:59:39Z**。
  内层 **CCTranslateMac-P0.zip，18,358,978 字节**，
  SHA-256 **c6266618ae36308d2d7f17252acfc9ad932f7036e0cf48aae0daa875a093ca61**。
  GitHub 外层 artifact SHA-256 为 `4135a1c6c8dbdaa1aa7e34e7203d122e4685cc4e50768e0aee3b149a44bbcd46`，
  不作为内层校验值。
- 独立下载核对 **664 库存项 / 54 资源 hash / 19 Git blobs / 6 arm64 Mach-O**；
  `cc_storage.py` 与 `cc_macos/storage_fixture.py` 均匹配该固定源码。
  source manifest SHA 正确且 clean，Info.plist 身份与原 lock 一致，无新身份默认；
  19 份 runtime 许可、605 文件 full-build 覆盖记录保留，ZIP CRC/路径/相对链接及 0755 模式通过。
- 现有 smoke 的 handshake、fixture、explicit_cancel、eof_cancel、bundle_unchanged、
  probe_files_cleaned 仍全为 true；包内 Python 3.12.14 / SQLite 3.53.1 / OpenSSL 3.5.8，
  HTTPS 证书验证通过。未增加 runtime JSON 字段，不把存储 CI 入口声称为新业务设置/诊断按钮。
- 下载 zip 已删除，合成目录无残留；会话只保留脱敏 JSON/验证记录。
  最后文档-only 正常 hooks/push，以 `[skip ci]` 避免重复未变源码 CI，交接分别给出源码与文档 SHA。

**基础层到此完成并暂停新增代码。** 本轮无需用户重装、登录或安装 CLI；新包仅自动化，
用户首轮实测证据仍只属于旧 `eec92a5`。未选择真实业务 home，不导入 Mac 的 `cc_core`，
Windows 的历史锁/清空、默认目录/迁移与日志行为保持原样。
完整唯一 writer、跨请求/跨进程读改写、配置/历史迁移适配、请求快照、provider 生命周期、
完整 P1/P2–P6 与剩余实机门槛未完成；这些可独立拆分的代码工作不因签名而被阻断，
但本次不继续开启。免费 GitHub/零预算/不 Release、不改 master 或 Windows 正式部署保持。

## 历史检查点：同制品 macOS 14/26 运行矩阵（2026-09-13，已完成）

1. [x] 完整保留 macos-15/Xcode 16.4 producer 的所有测试/构建/审计；归档后生成固定 SHA、
   本 run/attempt、zip hash 和内容/模式/链接摘要的 receipt，输出精确 artifact ID。
2. [x] needs producer 的标准 macos-14/26 arm64 jobs（fail-fast=false）只下载该 ID；
   先验证 zip hash，再 ditto 解压，不构建/重签被测 App，不安装 Python/依赖或更换账号。
3. [x] 复用相同包内 core/process suites、显式 storage、HTTPS/SQLite/cancel/EOF smoke 和完整审计；
   集成 harness 单独复制同 SHA 的现有 support/C/HelperIntegrationTests，不包含 App target，
   以预装 Xcode 16.2 / 26.6 编译；产品仍是 16.4 制品。harness 不适配时保留错误，不改 ABI/断言凑绿。
4. [x] 断言实际 sw_vers major/arm64/选定 Xcode，记录 producer 与 harness 编译器、测试计数和前后不可变；
   runtime 仅上传小型去敏报告，不重复上传 App。完成真实三 job 后记录证据、清理 zip、正常收尾。

官方 runner 表/镜像 README 只是选择依据，不是执行证据。当前不增加功能，不重做已验收纯核心；
跨系统自动化也不等于 Finder/干净用户/Gatekeeper/TCC、用户自报 26.5.2 或 Intel 已通过。

- 控制层已接入：producer 原 process/core 清单抽到同一 runner（下限 25/107，空/缺/skip/失败拒绝），
  保留全部原生/便携/网络/资源阶段。runtime 只使用精确输出 artifact ID、同 run/SHA/hash，
  报告目录拒绝放入 App；测试前后比较内容/模式/相对链接并复用完整审计。
- Windows 首轮联合 **115 tests，OK，20.651s**；补齐未知错误传播与不可写 App 报告负例后，
  最终针对性联合 **119 tests，OK，20.540s**。输出中的 `BLOCKED` 为“未显式准许 HTTPS”的预期负例，
  不是 CI 结果；此时三个系统尚待真实执行。
- 首次代码提交 `0bb40a9` 的两次正常 pre-push 都被完整 Windows suite 的原生崩溃阻止，未推送、
  未绕过。第二次 faulthandler 捕获 access violation；新增测试+provider 的 103 项及含原 GUI 的
  534 项定向复现都通过，不能据此称全套已好。随后完整 `PYTHONMALLOC=debug` 明确定位原
  `test_plain_paste` fake API：只返回 ctypes buffer 地址、不保留 buffer 对象，释放后被 memmove 写坏堆。
  已仅修测试 fixture 持有这两个分配，目标改为空缓冲并断言真实复制内容；生产剪贴板/Windows
  行为不改，没有屏蔽测试、跳过断言或归咎于既有 Tk teardown 警告。
- 修复后 debug allocator 下联合 **128 tests，OK，21.159s**。额外带
  `PYTHONFAULTHANDLER=1` 的完整诊断跑到 **1096 项**，但原 GUI branded launcher 退出码为 1；
  单项复现确认仅设置该诊断环境变量也失败，仅 `PYTHONMALLOC=debug` 时该单项通过。
  不改无关启动器、不把此诊断失败隐去。正常环境下剪贴板+启动器 **12 tests，OK，0.496s**，
  随后原始正常 pre-push（未注入这两个诊断变量）**1096 tests，OK，55.733s**，无失败/skip，
  privacy/编译均通过，成功推送；既有 Tk teardown stderr 警告仍在。

### 同包三系统可靠检查点

- 实际源码 **70fe79beee870c74ed1b4e078d98ac4fa89fce74**，含矩阵实现 `0bb40a9` 与上述测试 fixture
  生命周期修复。没有业务逻辑/协议/ABI、最低 OS 声明或 Windows 生产代码变化。
- [run 34765811135](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135) **success，attempt 1**；
  三个 jobs 的全部 steps 均 success。首次真实矩阵即通过，没有 Mac 失败重跑、harness 编译妥协或 runtime 测试跳过。
  以下是实际报告，不是 runner README 推测：

| job | OS / build，均 arm64 | image | 实际 Xcode / build | Swift / clang / SDK |
|---|---|---|---|---|
| [producer 103746446392](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135/job/103746446392)，2m51s | 15.7.9 / 24G830 | 20260907.0337.1 | 16.4 / 16F6 | 6.1.2 / 1700.0.13.5 / 15.5 |
| [runtime 103746853056](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135/job/103746853056)，1m51s | 14.8.9 / 23J631 | 20260831.0302.1 | harness 16.2 / 16C5032a | 6.0.3 / 1600.0.30.1 / 15.2 |
| [runtime 103746853097](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135/job/103746853097)，2m20s | 26.6.2 / 25G83 | 20260907.0351.1 | harness 26.6 / 17F113 | 6.3.3 / 2100.1.1.101 / 26.5 |

| 实际执行 | producer 15 | runtime 14 | runtime 26 |
|---|---|---|---|
| 便携/打包/Darwin contracts | **245 tests，8.371s**，含本轮控制层 47 项 | 不重复宿主测试 | 不重复宿主测试 |
| 原始普通 XCTest | **35 总数：34 pass + 1 初次集成 skip，0 failures，9.178s** | 只编译独立集成 harness | 只编译独立集成 harness |
| 包内 config/catalog synthetic 真进程 | **25 tests，55.494s** | **25 tests，55.540s** | **25 tests，55.257s** |
| 包内 `-I -B` 同源核心 | **107 tests，0.529s** | **107 tests，0.673s** | **107 tests，0.481s** |
| 强制 Foundation.Process → 包内 helper | **1 test，2.495s** | **1 test，2.439s** | **1 test，2.780s** |
| 显式临时 storage / HTTPS / SQLite / cancel / EOF / 不可变审计 | 全通过 | 全通过 | 全通过 |

除 producer 首次尚未构建 App 的集成 skip 外，以上包内/后置与两个 harness **均 0 skip/0 failure/0 error**；
日志确认原集成测试实际发现执行，不采用 Swift Testing 的附带“0 tests”行充数。
三个系统均真实输出 `Bundled storage fixture passed with bundle identity and temporary home`。
真进程覆盖仍含期限、输出限额、取消/helper EOF、正常/错误退出、TERM-KILL-reap、leader/ECHILD 所有权与兄弟存活，
没有运行用户 CLI/账号/配置/模型。产品 dylib/Python 都来自相同 App，不把独立 harness 构建当产品重建。

#### 固定制品与独立核验

- 唯一 App [artifact 10321110850](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135/artifacts/10321110850)，
  `macos-arm64-p0-development-NOT-A-RELEASE`，API 已核验有效，到期 **2026-09-20T15:33:30Z**。
  内层 **CCTranslateMac-P0.zip，18,358,978 字节**，
  SHA-256 **3a79f5fa2b82a2ec7b936309f2a593fe237f1a863a7d169c90391804b7bccc70**。
  API 外层 artifact digest `6077d0b7c4f1d29dfd839086799161d6dab028fb3a533ea11849dc2bd66b067a`
  与内层 hash 分开，不互相替代。
- 两个 runtime 只消费此 run 的精确 ID；其小型报告分别为
  [Mac14 artifact 10320387319](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135/artifacts/10320387319)
  （2,393 字节，到期 2026-09-20T15:35:31Z）与
  [Mac26 artifact 10320991222](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135/artifacts/10320991222)
  （2,393 字节，到期 2026-09-20T15:35:58Z），均有效，不含第二份 App。
- 独立下载核验 ZIP CRC、路径、App/Python 0755 和 python3 相对链接，
  **664 库存 / 54 资源 hash / 26 个 Core 同源 Git blobs / 6 arm64 Mach-O / 19 份 runtime 许可**。
  605 个保留文件的原 full-build 许可覆盖记录不变；source manifest 为该源码且 clean。
  本轮扩大逐字节 Git 对照到包内全部 26 个 Python/instructions 源文件，未增加包内业务模块。
- producer/14/26 及下载归档的文件内容/模式/链接摘要均为
  `6c41d32ac1c275c4203f847f74e47f8286faf4cc52a96ca6003bbd28a7a7f96e`；
  两 runtime 均最终 `stage=complete`、`bundle_unchanged=true`。
  两个独立 harness 的同源树摘要相同：
  `d72b49a127231ec4fd38b9906041b754555e57222c0d8acdda8d41e7eb84c08b`，原集成测试未改字节。
- 包内 Python 3.12.14 / SQLite 3.53.1 / OpenSSL 3.5.8、隔离/禁 bytecode、CA/HTTPS 证书验证全通过；
  handshake、fixture、explicit_cancel、eof_cancel、probe_files_cleaned 在三系统均为 true。
  没有用 Windows 执行 Mac 二进制。临时 zip 已清理，仅保留脱敏 JSON；最终证据 docs-only 正常提交/push，
  不为未变源码再次启动 CI。

**本次仅同制品跨系统自动化切片完成，到此停止新增功能。**
旧 `eec92a5` / `34706318638` 用户正向实机报告仍独立，用户自报 26.5.2 未独立核验；
当前新包没有 Finder/干净用户/Gatekeeper 官方例外/TCC/多屏/Spaces/IME 实测。
Intel、完整 P0/P1、请求快照/配置历史唯一 writer、exec/app-server/Claude 及 P2–P6 仍待办。
后续纯核心/synthetic 回归技术上可以独立推进，不伪称被付费签名阻断，但不属于本轮。
免费 GitHub 分发/零预算不变；不发布 Release、不改 master/Windows 部署、不要求用户现在重装或安装 CLI。

## 当前单一切片：共享历史仓库与明确写入所有权（2026-09-13，已完成）

已验收的跨系统矩阵关闭，不重做等价实现。本轮只完成历史 I/O：

1. [x] 共享显式路径仓库接入 Windows load/add/cache/clear，保留数组/字段顺序、时间/条数、
   OCR 缓存排除、配置路径/default、日志和原子写入 patch seam；不改 `_record_history` 当前开关/过期策略。
2. [x] add 的读改写与 clear 共用同一操作锁，read/cache 也在同一锁内；可控并发证明先开始的追加
   写回完成后清空才返回。此后新提交的追加仍可记录，不等于取消旧翻译或新的隐私策略。
3. [x] Mac 显式 owner 使用稳定的独立侧文件协作锁（不锁可被 replace 替换的 JSON inode）；
   close 与操作互斥，退出/崩溃释放，不删除/抢占活跃锁文件，不凭 PID 杀进程。
4. [x] 包内真实临时 home、竞争 owner/replace/退出/崩溃、故障/FD/temp 回归及 Windows 消费者/
   差分/并发测试；正常 hooks、同一制品 15/14/26 CI、审计和证据文档后停。

Windows 旧读取策略保留：缺文件/非数组返回空；损坏或读取异常记 `load_history` 日志后返回空，
旧 add 仍调用这一兼容读取入口。只在 Windows wrapper 保留既有宽异常日志边界，
不把它作为 Mac 的默认策略。Mac 写入前严格读盘，损坏 JSON/无效历史结构/权限错误直接失败，
不得当空数组覆盖；显式 clear 仍可删除损坏文件。共享原子 JSON primitive 不重造。
共享规则 `cc_result_rules` 继续由现有 metadata 调用链使用，不改变 kind 的上层选择规则。
后续完整配置/历史服务仍需唯一 helper 业务接线；本轮只显式合成入口，不增加 IPC/UI 按钮、
完整 RequestSnapshot、配置迁移或 provider/exec/app-server。

### 历史仓库实现与本地阶段记录

- `cc_history.HistoryRepository` 接显式路径，以同一 RLock 覆盖 load/add/cache/clear/close；
  复用 `cc_storage.atomic_write_json`，保留 JSON 字节/字段顺序/时间/上限和原缓存匹配。
  Windows wrapper 共用原 `_HISTORY_LOCK` 名称（改为可重入锁以保留 public load 注入），
  `_atomic_write_json`/load/log seam 保留，`_record_history` 和历史 UI 消费者未改策略。
- Mac `MacHistoryOwner` 只接受显式绝对路径，确认已存在父目录并消除目录别名；
  拒绝 `.app`/JSON symlink，不创建目录/迁移/回退。fcntl 只在显式 Darwin 构造时导入。
  `history.json.lock` 为示例稳定侧文件，O_NOFOLLOW/CLOEXEC/0600、regular-file 检查、非阻塞 flock；
  第二 owner 明确拒绝，JSON replace/clear 不影响侧文件，close 不删除它。
  close 与正在进行的操作互斥；fork 继承对象在进入继承的线程锁前拒绝，
  子进程只关闭自身 FD 副本，不 unlock 父 owner。close 错误可见且不重试状态不明的 FD。
  这是协作式所有权，不是防恶意篡改/目录替换的权限沙箱；调用方仍须保护其数据目录。
- 线性化：取得操作锁并完成追加写回后，等待的 clear 才删除；clear 返回后不能再被该已完成追加“复活”。
  若另一次 add 在 clear 之后取得锁则仍可记录，不取消旧翻译，也不冻结用户当前 history 开关。
- 首次 Windows 联合 **89 项 / 5.644s，2 failures**：差分矩阵一轮保留旧内容并出现旧日志文件，
  因当时断言未附日志、临时目录已清理，具体写入异常未保留，不能宣称已定位根因。
  已让字节差分失败附实际兼容日志，不改生产写入重试/异常策略、不删矩阵或放宽断言。
  同一矩阵单独 **1 项 / 2.313s 通过**；随后联合 **166 项 / 11.894s 通过**，
  再独立连续三轮完整矩阵 + owner contract **36 项 / 7.443s 通过**。上述均不是 Mac 证据。
- 新便携历史 **21 项**，Windows 差分/调用链 **15 项**（包含 224 组写入对照与 120 组缓存组合）；
  Mac owner 便携契约 **33 项**，包内 Darwin 真进程 **19 项当时待执行**，实际结果见下。
  构造清理用 finally 转移 FD 所有权；add/clear/close 并发测试等竞争者实际进入共享锁，
  不用 sleep 推测已开始。原配置/路径/日志 AST 不变，旧历史 AST 保留为冻结差分 oracle，而非删除断言。
  必需资源/hash/隔离导入、同包 process/core 清单已接入；下限 25/107 提高为 **44/128**，未扩 runtime JSON。
- 最终联合针对性 **199 tests，OK，11.937s**，无失败/skip；包含旧 Windows 历史消费者、
  故障/并发/差分、owner 契约、隔离导入与资源/runner 校验。后续正常 hooks 与真实三系统结果另记。
- 首次源码 `cbbf136fc914c1796063f15d74168b31af07e1a3` 正常 hooks **1165 tests，OK，62.496s**；
  [run 34767969885](https://github.com/mclight-ship-it/cc-translate/actions/runs/34767969885) 在 producer
  便携阶段 **299 tests / 5.277s，1 failure**，下游 runtime 未执行，不能算通过。
  实际日志定位 Python 3.14.7 的 stdlib `pathlib._os` 自身会 import fcntl，被新项目隔离 guard 误判，
  并非 owner 提前取锁。已仅在安装项目 import guard 前准备 pathlib 标准库基线；
  保留 fcntl 禁止项、直接项目 import 拦截和文件/网络 audit，不换工具链/宿主版本，不删断言或跳过。

### 历史仓库可靠检查点

- 最终源码 **c78d8ee994a0d335a1e2c87b51e60c33949d4cc9**；相对 `cbbf136` 只有隔离测试基线及失败记录，
  生产逻辑相同。修正后 Windows 针对性 **34 tests，OK，0.321s**、正常 affected-test hook
  **1 test，OK，0.181s**；上次生产改动的完整 **1165 tests，OK，62.496s** 保留为实际覆盖证据，
  不把未重复的全套冒充重新执行。正常 privacy/编译 hooks 均通过，没有 bypass。
- [run 34768088072](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072)
  **attempt 1，success**，三个 jobs 的全部 steps success；先前失败 run 34767969885 不改写为通过。

| 实际 job | 实际 OS/build，arm64 | image | Xcode/build；Swift；SDK |
|---|---|---|---|
| [producer 103752571771](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072/job/103752571771)，2m17s | 15.7.9 / 24G830 | 20260907.0337.1 | 16.4 / 16F6；6.1.2；15.5 |
| [runtime 103752884874](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072/job/103752884874)，1m37s | 14.8.9 / 23J631 | 20260831.0302.1 | harness 16.2 / 16C5032a；6.0.3；15.2 |
| [runtime 103752884833](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072/job/103752884833)，1m55s | 26.6.2 / 25G83 | 20260907.0351.1 | harness 26.6 / 17F113；6.3.3；26.5 |

| 实际执行 | producer 15 | runtime 14 | runtime 26 |
|---|---|---|---|
| 便携/打包/契约 | **299 tests，5.813s**，含新增历史21/owner契约33 | 不重复宿主套件 | 不重复宿主套件 |
| 原普通 XCTest | **35总数：34 pass + 初次集成skip1，0 failures，8.685s** | 独立原集成 harness | 独立原集成 harness |
| 包内 synthetic 真进程 | **44 tests，53.075s** | **44 tests，53.436s** | **44 tests，55.852s** |
| 包内同源核心 | **128 tests，0.509s** | **128 tests，0.485s** | **128 tests，0.467s** |
| 强制 Foundation.Process → 原 helper | **1 test，2.280s** | **1 test，2.428s** | **1 test，3.102s** |
| 显式历史/存储、HTTPS/SQLite、取消/EOF、完整审计与不可变 | 全通过 | 全通过 | 全通过 |

除 producer 构建前原有初次集成 skip 外，包内/后置/两个 runtime **0 skip/0 failure/0 error**。
三个系统各新增 **19 项历史 owner** 在日志逐项 `ok`，实际输出 `PASS: bundled history owner fixture`：
真实第二进程竞争拒绝、两次 JSON atomic replace 后锁仍有效、clear 后侧文件 inode 保留、
owner 正常退出/`os._exit` 崩溃被回收后接管、独立兄弟存活、fork 子副本拒绝且不 unlock 父锁；
目录别名、JSON/侧文件链接与 FIFO 拒绝、fstat/flock/竞争失败 FD 关闭、读取/坏数据/原子写失败不覆盖、
close 等待真实写入、close 后拒绝所有操作、状态不明的 close 不再重试。不是 mock flock 冒充真实竞争。
core 的新增 **21 项**同时验证实际 JSON 字节、旧字段/上限/时间/缓存、RLock 屏障、clear/close、
坏文件/权限/替换失败保护。Windows 新 **15 项**另走真实 wrapper/旧实现差分和 `_record_history` 当前策略。
Mac 历史 fixture 从实际 Info.plist 取得身份，在 caller-owned 临时 home 显式操作并重开；
它不是新业务 IPC/按钮。Foundation 仍测原 helper 通道，不能据此宣称完整历史 helper 服务已接线。

#### 历史制品与清理

- 唯一 App [artifact 10321128934](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072/artifacts/10321128934)，
  API 已核验有效，到期 **2026-09-20T16:18:52Z**。
  内层 **CCTranslateMac-P0.zip，18,363,013 字节**，
  SHA-256 **f44bbfe8428e353c1c3aeefb5a8a8e3c0dab9bc0645ad0a668aeebd64cc1dfcf**。
  API 外层 artifact digest `a890d9ee3e4593bb160c4cc993e286587166f387523e22e97ea1db82cf97af23` 单独记录。
- 两个小报告：
  [Mac14 artifact 10320338723](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072/artifacts/10320338723)
  （2,389 字节，到期 2026-09-20T16:20:39Z）、
  [Mac26 artifact 10321470139](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072/artifacts/10321470139)
  （2,391 字节，到期 2026-09-20T16:20:55Z），均有效、不含第二份 App。
- 独立核验 **667库存/57资源hash/29个同源Git blobs/6 arm64 Mach-O/19份runtime许可**。
  新 `cc_history.py`、`cc_macos/history_owner.py`、`cc_macos/history_fixture.py` 均匹配固定源码；
  manifest commit 正确且 clean，605 个保留 runtime 文件的原 full-build 许可覆盖记录不变。
  ZIP CRC/路径/0755/相对链接通过；同包只由 Mac15/Xcode16.4 组装，两个 runtime 不重建或重签。
- producer、两个 runtime 和下载归档的字节/模式/相对链接摘要均为
  `df82533aa6550e506d2f5bfc5db99e0b8315b15fceccbd19b001b0e4cd21518b`；
  runtime `stage=complete`、`bundle_unchanged=true`。
  三系统 helper smoke 的 handshake/fixture/explicit_cancel/eof_cancel/probe_files_cleaned 均 true，
  Python3.12.14/SQLite3.53.1/OpenSSL3.5.8、bundle CA 与 HTTPS 证书验证通过。
  没有在 Windows 冒充执行 Mac 二进制。下载 zip 与临时审计脚本已清理，只保留脱敏 JSON。
- 最终证据使用 docs-only 正常提交/push，不重跑未变源码；源码 SHA 与最终文档 HEAD 分别报告。

**历史检查点当时结论：历史 I/O 单一切片完成，停止新增代码。**
以下 2026-09-14 复核重新打开 Windows 偶发写入失败的根因待办，不撤销上述真实 CI 结果。
Windows 真入口已接共享仓库、Mac 显式 owner 已验证；
配置 writer/迁移、历史业务 helper 唯一入口、完整 RequestSnapshot/provider 与 P2–P6 仍未完成。
这些后续可独立项不因付费签名阻断，但不属于本次实施。旧 `eec92a5` 用户报告不迁移到本包，
用户自报26.5.2、干净首开/官方单App例外、TCC拒绝/重启/跨版本、多屏/IME、真实CLI/账号与Intel仍待验证。
免费 GitHub 分发/零预算不变；不 Release/master/Windows部署，不要求现在重装、安装CLI或登录。

### 历史矩阵二十轮复核（2026-09-14）

本次只复核原失败矩阵及精确消费者，不启动配置服务或其它实现。
测试时 HEAD 为 **dcf70d70ddcbefce9613de3361fa242d4b0948a6**，
其生产源码仍为 `c78d8ee994a0d335a1e2c87b51e60c33949d4cc9`。
使用 Windows Python **3.12.10 / AMD64** 的现有 unittest runner：

```text
python -B -m unittest -v tests.test_history tests.test_history_windows tests.test_storage_windows tests.test_full.TestHistoryIO tests.test_full.TestHistoryHelpers
```

- 正常环境独立进程 **20 轮，每轮完整 89 项，共 1,780 tests**，runner 累计 **72.606s**。
  无 `PYTHONFAULTHANDLER` / `PYTHONMALLOC` 注入，无测试 skip；第 6 轮失败后仍执行余下全部轮次，
  不是反复运行直到成功。20 份逐项 verbose 日志及每份 SHA-256、计数/退出码/UTC 时间保留在本地会话证据。
- **19 轮通过；第 6 轮 89 项 / 4.087s，2 failures，exit 1。**
  `TestWindowsHistoryRepository.test_add_bytes_match_legacy_matrix` 的
  `kind='invalid', flags=(False, False), sig=None, limit='4'` 字节断言及最终无日志断言失败。
  实际兼容日志已明确保留 `PermissionError: [WinError 5] Access is denied`：
  同目录唯一临时 JSON 到合成 `history.json` 的 `os.replace` 被拒绝。
  不能称“二十轮未复现”，也不能将此前未保留异常的原 89 项失败追溯判定为同一个根因。

为区分历史抽取与底层替换失败，额外只在会话外做两组有界诊断（不是替代验收）：

| 诊断组 | 完整执行 | 实际结果 |
|---|---|---|
| 原新入口矩阵，观察一次真实 replace 后原样抛错 | 20 次矩阵 / 8,960 次 replace / 46.004s | 2 次 WinError 5，3 个断言失败，0 error/skip |
| 冻结抽取前 load/add/cache/clear 函数绑定相同测试路径、现有共享原子 writer 与日志 seam | 20 次矩阵 / 8,960 次 replace / 48.957s | 3 次 WinError 5，4 个断言失败，0 error/skip |

- 旧历史对照中实际栈为冻结旧 `add_history` 到同一个共享 writer，参考文件自身也发生过替换失败；
  这不是旧版本全部环境/原子 writer 的独立回滚验证，也不能据此排除所有新旧代码问题。
  已知现象不只限于新增 `HistoryRepository` 路径。
- 失败时、原 writer 清理临时文件前，保留了仅合成的源/目标 JSON 副本、stat/文件属性、
  当前 Python 线程栈、最近替换的单调时序及 errno/winerror。诊断没有重试、删除目标或改断言，
  原错误仍由 Windows 既有 wrapper 记录；没有更改仓库测试/生产代码。
  观察到源和目标为普通可写文件、archive 属性（不是 read-only），单链接；
  当前 Python 仅 MainThread；失败后针对这两个文件申请 DELETE 访问并立即关闭句柄成功，
  **未执行删除，也不证明失败瞬间没有其它句柄或系统组件参与**。
- **根因仍未定位，稳定性复核未通过。** 已定位失败 syscall 和新旧路径、保留文件/线程时序，
  但没有失败瞬间持有者/拒绝来源证据，不能猜测杀毒软件、磁盘竞争或确定为环境问题。
  不为凑绿加入生产重试/延时、换测试目录、放宽断言、忽略日志或改变 Windows 兼容错误策略。
  在取得足够因果证据前不作推测性“修复”，这一项明确保留为阻断，交协调会话定下一诊断范围。
- 测试自身清理完成，工作树无残留 `.storage-test-*`；本地仅保留日志、JSON 汇总和必要合成失败证据，
  不保存真实用户历史/认证，不将主机路径或原始日志提交公开仓库。
  本次仅文档收尾，正常 privacy/docs-only hooks 提交/push；不重跑不变源码三系统 CI，
  不以旧绿色 [run 34768088072](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072)
  抹掉本次 Windows 失败，也不将旧 `eec92a5` 用户实测赋给新包。

### 下一配置持久化候选（只读调查，尚未开放实施）

1. **真实入口与线程边界。** `translator.pyw` 的 `load_config` / `save_config` /
   `TranslatorApp._save_config` 是实际磁盘链；加载有迁移写回，初始化检测语言可保存配置。
   `_run_startup_tasks` 在后台线程设置 `AUTOSTART_INITIALIZED` 后也保存同一 `self.cfg`；
   设置提交、词典删除、经 `root.after` 返回 UI 的下载完成回调也通过 `_save_config` 保存。
   当前没有配置操作锁，不能只锁 `save_config` 就声称修复共享可变 dict 或全部写入所有权。
2. **Windows 兼容边界。** `cc_core.CFG` / `DEFAULT_CONFIG` 是当前常量来源，不能让 Mac
   导入整个带 DATA_DIR 创建/旧文件迁移副作用的 `cc_core`。`_resolve_data_dir` /
   `_user_data_path`、公开函数与 writer/log patch seam 本轮均未改。
   诊断模块虽然只展示 config/history 路径，但 import 时同样调用 `_user_data_path`，
   不是可直接用于 Mac 的无副作用路径模块。
3. **迁移应先分离规则与持久化。** `Config` 合并默认/类型转换/保留未知键，并做旧 model/provider、
   `gpt-5.4-mini`、UI/Labs 的内存迁移；`load_config` 磁盘写回却仅给 raw 的副本补 UI/Labs 标记及
   streaming 开关，不直接 dump 整个归一化 Config。候选先共享显式输入的规范化与
   `changed + payload` 迁移计划，保持原字段顺序、未知字段、一次写回和已有显式 opt-out；
   语言检测、开机启动和设置副作用留上层，不把 Windows UI 默认解释为已实现 Mac 功能。
4. **Mac 严格读/owner 是下一依赖，不是当前成果。** 显式路径与调用方生命周期，缺失文件与
   损坏/权限错误分开；只在明确缺失时提供默认，错误不当空配置覆盖。
   read → migration plan → atomic write 应在同一 owner/操作锁内，跨进程协作锁必须是稳定侧文件。
   可评估从已验证的历史 owner 抽取窄公共原语，不能复制一套平行锁或假称原子 replace 等于单写服务。
   Windows 既有缺失/坏配置读取日志及默认、迁移写失败保留旧盘但返回已迁移内存值的兼容行为须另保留。
5. **建议下一授权按依赖拆。** 先纯配置规则/迁移计划并接 Windows 原 load，
   用 `TestConfigPersistence` / `TestConfigWrapper` / `TestAtomicWrites`、
   `test_storage_windows` 固定字节/未知键/错误矩阵验证；随后再单独批准显式配置 owner、
   启动后台任务与 UI 保存所有权和 Mac 合成入口。完整服务、RequestSnapshot/provider/UI 不在本次内，
   此建议未执行，也不因付费签名成为不可独立推进项。
