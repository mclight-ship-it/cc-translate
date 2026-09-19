# macOS 原生客户端开发指南

**当前可下载检查点：原生界面、图片/OCR翻译、词典来源、复制隔离、字号/摘要/历史条数/输入上限、结果位置、双击复制间隔、截图全局快捷键及恢复默认设置；移植尚未完成。**
最新批次8f898aa已通过[完整App与同包三系统验收](MACOS_TODO.md#native-restore-defaults-checkpoint)。
下文保留更早失败与修复过程；完整Claude服务及后续移植项继续。
最新已核验包见[下载与操作](#native-translation-user-check)；
[模型目录](#native-model-catalog)已通过同包三系统验证，已包含在最新下载中。
以下保留原生词典及更早链路的前置证据。
源码`b86507f` / [run35147996073](https://github.com/mclight-ship-it/cc-translate/actions/runs/35147996073)
保留独立翻译/结果/设置/历史窗口、直接翻译和菜单栏，增加原生下载、离线查词及词典管理。
本地命中不请求模型、不自动补充AI；六种明确选择的AI动作仍追加到原结果后。
producer已通过真实URLSession下载67,948,544字节及随包安装，原生离屏绘制P95为37.747ms；
同一个App在macOS15.7.9/14.8.9/26.6.2各202process/536core/17Foundation通过，run实际watch exit0。
前置界面/动作包`4807f62`已验证；后续仍有P3–P6，见[当前实施清单](MACOS_TODO.md#native-product-ui)。
界面采用现有SwiftUI/AppKit与系统语义色，不改成HTML/WebView原型。

前置协议检查点已修复官方启动通知的`emittedAtMs`被native envelope误拒绝的问题：
源码`3ee680a98141badc8b7499eff6716c7223aa41d4` /
[run35103974280](https://github.com/mclight-ship-it/cc-translate/actions/runs/35103974280)。
官方0.146.0/0.154.0在Mac producer均实际通过版本读取及native prewarm，
严格仅initialize/initialized/hooks/list，无thread/turn/账号/模型调用。
正常Windows完整hook1573、同包三系统各190process/478core/13Foundation通过，
完整App已独立核验；这不是要求用户反复改安装，详见[协议检查点](MACOS_TODO.md#codex-protocol-checkpoint)。
当前[下载包及步骤](#native-translation-user-check)已切换到上述正式界面包，保留协议修复。
2026-09-16 本轮交接后用户确认翻译通过、目前测试可用，翻译主流程已有实际使用的正向反馈。
以此为可用基线继续开发；无需为继续正常使用重复版本/权限前置检查，扩展验证另行安排。

前置版本兼容修复已验证：源码`3efebbfabb7a6af16772d313ca3a5789a0988b64` /
[run34996120967](https://github.com/mclight-ship-it/cc-translate/actions/runs/34996120967)。
稳定版 Codex `>= 0.146.0` 可进入运行时协议/目录验证，不再使用精确版本白名单。
显式版本探针显示安全解析的数字版本和最低要求；无法识别、过旧和预发布不会混为同一结论。
正常Windows完整hook1565通过，同包三系统各188process/473core/13Foundation通过；
官方0.146.0与0.154.0只读`--version`也在producer用随包监督实际通过，没有登录或模型调用。
用户需使用[当前修复包](#native-translation-user-check)，不必为符合最低要求的稳定CLI降级。
旧`2b116f0`包及旧用户实测均不覆盖本修复；所有未来版本的完整兼容性仍不能仅凭版本号承诺。

前置阶段概述（2026-09-14；最新修复见上）：P0 真实 Mac 自动化及多项 P1 切片已通过，已收到首轮匿名用户正向实机报告；
catalog 真进程监督及缓存签名/history-kind 纯规则切片已通过 Windows/Mac 自动化；
显式平台路径/原子 JSON 基础也已通过；同一 Mac15/Xcode 16.4 制品现已在标准免费
macOS 14.8.9/26.6.2 arm64 CI 完成包内运行、进程、存储、网络与 Foundation 集成验证。
共享历史仓库和显式 Mac owner 已接入，并完成新包的 Windows/三系统自动化；
配置owner现已接入显式私有helper与Swift API，含保存/迁移可读性修复，在同包三系统通过。
历史现也接入同一业务连接及Swift分页/记录/清空API，含worker未启动确定失败的跨端修复，
同包三系统90进程/242核心/9Foundation通过。旧89/242/8绿灯未覆盖该review缺陷，不代作修复证据。
按最新连续授权，完整请求快照已接 Windows 派发/执行并通过同包三系统验证：
源码 `8797fc7` / [run34823367426](https://github.com/mclight-ship-it/cc-translate/actions/runs/34823367426)，
Windows正常完整hook1357；每系统90进程/277核心/9精确Foundation，新35个快照方法逐项执行。
Darwin native Codex 后端前置修复源码 `6d029d1` /
[run34836719504](https://github.com/mclight-ship-it/cc-translate/actions/runs/34836719504)，
正常Windows完整hook1494；每系统172进程/410核心/9精确Foundation，
该次新增14个进程/核心方法逐名各执行一次；当时尚无翻译helper/Swift API/UI。
此前独立审查发现idle回收责任、item终态及非法item类型三项生产缺陷；现已实证并修复，
正常联合300项、完整hook和同包三系统均通过；旧13b6543绿灯不代作这三项修复证据。
**前置翻译业务链已接通并通过真实合成进程验证**：源码
`2b116f0731803b52d87565d1e8e4602b6794c324` /
[run34847149053](https://github.com/mclight-ship-it/cc-translate/actions/runs/34847149053)，
正常Windows完整hook1542；同包三系统各185进程/458核心/13精确Foundation。
新增31 Swift unit、13 process、48 core均实际执行；Foundation使用真实包内helper/native合成CLI，
不是只mock provider。现有原生UI已增加显式启用、输入/选区/流式结果、设置、历史及复制入口。
**官方CLI/账号/真实模型及新UI真人操作仍未验证，不是完整产品验收**；
下一外部步骤集中在[新包操作交接](#native-translation-user-check)，不会代用户安装/登录或发送真实模型请求。
旧Windows WinError5拒绝来源仍未知。
完整首开/TCC 矩阵未验收。最低版本暂定 macOS 14，
macOS 26.6.2 的 CI 系统版本已有独立记录，但旧包用户自报 26.5.2 仍未独立核验，不等于完整兼容性结论，
Apple Silicon 优先；Intel 只有独立构建及实测通过后才承诺支持。
进度与证据以 [MACOS_TODO.md](MACOS_TODO.md) 为准。

## 1. 产品边界与开发方式

目标是保留 Windows 产品能力的原生 Mac App，不是 Tk 换皮、WebView 主界面或一次性全 Swift 重写。
Windows 原入口继续工作；不将 macOS 半成品接入 Windows 安装、更新或发布通道。
术语表、风格预设、系统词典及 Apple Translation 是独立后续方向，不捆绑此次移植。

开发者可在 Windows 编码和测试便携核心。原生编译、链接、资源、GUI、TCC 和签名必须在真实
macOS 环境验证。推荐经授权后使用云端 macOS CI，用户 Mac 只承担 CLI 登录、授权和两轮集中验收。
项目默认选择 **GitHub 免费站外分发**，不上 App Store，不要求付费 Apple Developer 会员。
用户安装测试不需要 Xcode、Python、Git 或开发者账号；开发机器/CI 才需要构建工具。
未识别/未公证应用可能需要用户亲自按 Apple 官方流程为本 App 选择“仍要打开”，不是保证双击即用。
Developer ID/公证是未选择的可选付费增强，不是免费路线的安装前置，也不标为已通过。
Codex/Claude CLI 与用户账号仍是外部前提；不是所有安装方式都需要 Node 或 Homebrew。

当前已获准提交并正常推送唯一开发分支 `agents/cc-translate-macos-native`，
使用项目公有仓库的标准免费 macOS runner 验证。真实 run/SHA 和结果写入验收清单；
工作流存在或提交成功本身不代表 CI 通过。不发布、不推送/合并正式分支、不部署覆盖 Windows 应用、
不购买额度或启用收费大机器。不配置签名私钥、不要求在聊天粘贴凭据、不绕过 Gatekeeper。
标准免费 arm64 runner 已实际验证；真实设备兼容性和首次权限仍待本机验证。
真实 CLI/账号另行验收，不属于本轮合成探针，也不要求用户先购买签名身份。

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
  history_owner.py           显式 Mac 历史 owner；稳定侧文件 flock、操作锁、close/fork 边界
  history_fixture.py         仅 CI 显式调用的临时历史写入/查询/清空/重开，不是业务按钮
  history.py                 显式业务连接的有界分页/记录/清空，固定历史仓库策略与revision
cc_classify.py               P1 共用本地分类/词典触发判断；仅依赖 re，无平台/数据路径副作用
cc_direction.py              P1 共用方向路由/方向提示词；UI 语言由调用方显式传入
cc_prompts.py                P1 既有文本提示词及独立 provider revision；无导入副作用
cc_result_rules.py           P1 缓存签名/history-kind；只接 caller-resolved 值，不读取 UI/配置
cc_storage.py                P1 显式 Mac 路径与单文件原子 JSON；不选默认 home，不是唯一 writer
cc_history.py                P1 共享历史仓库；Windows 原入口复用，单进程统一操作锁与严格默认读取
cc_config.py                 P1 无 I/O 的单一默认/Config/迁移计划；不提供配置 owner/磁盘迁移服务
cc_config_store.py           P1 显式配置仓库；严格 load/raw 迁移/独立 save 快照，共用操作锁
cc_macos/file_owner.py       P1 history/config 共用稳定侧文件锁、fork guard 和显式 close
cc_macos/config_owner.py     P1 显式 home + bundle ID 的 Mac 配置 owner；不创建数据目录
cc_macos/configuration.py    P1 显式配置/历史双owner连接生命周期、严格业务边界和固定错误码
cc_providers/base.py         冻结请求/结果/状态契约；纯导入不加载 CLI
cc_providers/registry.py     显式注册/获取/退出的纯 registry
cc_providers/darwin_rpc.py   有界同步stdio；自有进程组、取消/deadline/EOF/FD生命周期
cc_providers/codex_darwin.py 显式native Codex后端；复用app-server协议，无exec fallback
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
  Mac 开发包保留 `__init__.py` / `base.py` / `registry.py` 及 native config reader、
  Darwin 监督适配、原 instructions 资源及 catalog 实现。最新内部后端还携带原 Codex
  CLI/JSONL/app-server 模块以复用协议和类定义；Darwin facade 只走 native app-server，
  不执行旧 exec 路径、不 fallback，也不携带 Claude 后端。
  旧 storage fixture 仍以 `cli_simulated=true` 标明替代 CLI 输出、实际写入/重开临时缓存。
  新 `catalog_process_fixture` 则用包内 Python 真正运行合成 CLI 的 version/debug-models，
  经原 catalog 调用链验证冷缓存与重开；这不是运行用户官方 CLI，也不是放行真实用户 provider。
  新 native provider fixture 已通过真实合成 app-server 的初始化、无文本预热、
  流式/非流式、提交前后取消/超时/EOF与后代清理；用户官方 CLI/账号兼容、Claude Darwin执行仍待验证。
  每次抽取保留兼容导出并跑 Windows 回归，不能据合成进程通过宣称完整 Mac provider 可用。
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
该早期检查点的OCR专属文案和动态提示词组装仍由现有调用方负责；
后续[截图/OCR检查点](#native-capture-ocr)已把原字节排版提示及拼装helper共享给Windows与Mac。
后续快照与平台路径检查点见下，不把这条早期纯提示词提取宣称为全部P1完成。
已有 `ProviderRequest` / `ProviderResult` 等数据类和 `ProviderRegistry` 也能在隔离环境直接
导入；类字段、冻结语义、未知认证状态和 registry 退出错误传播均保持原样。
这只完成纯契约的初始化边界，不是完整请求快照/配置版本协议或可运行的 Mac provider。
包内 `__all__` 保留兼容 API 名称，但未提供的 CLI 后端访问会显式导入失败，不降级到其他后端。
既有 `CodexCliProvider` 构造可显式接收 `catalog_cache_dir` / `catalog_log_error`，直接传给
已有 manager，stream/warm 共用同一对象。未传时保留 Windows APPDATA/展开 HOME 的默认路径
及延迟 `cc_core.log_error`；显式传入不导入该模块。cache root/logger 不加入 fingerprint，
TTL、配置/二进制/native-cache 签名、冷缓存三次探针及重开后的 roundtrip 规则不变。
原存储诊断仅在临时目录创建合成 binary identity、配置、metadata 和缓存，继续报告 `cli_simulated=true`。
新增的包内 Darwin `catalog_process_fixture` 则启动包内 Python 构造的合成 CLI，真正调用原
catalog 冷缓存和重开路径，严格区分 `fixture=true` 与真实进程证据；两者都不运行用户 CLI/账号/模型。
catalog 与配置探针共用固定包内 C 自有组边界，catalog 每次 8 秒、stdout+stderr 共 8 MiB；
取消/EOF/早退和正常完成均先 TERM/KILL 再回收 leader，ECHILD 后不再 signal/wait。
不追杀主动逃离组的 wrapper；fatal 监督失败不降级或继续提交 turn。Windows 默认执行路径保持。
这不是完整平台配置/历史单写或可用的原生翻译 provider。最新代码的实际验证以 TODO 为准，
旧包的用户正向报告不迁移到新构建。
缓存签名拼接和历史类型优先级已抽到 `cc_result_rules.py`，Windows wrapper 实际复用。
route/本地词典对象、cfg 默认、i18n fallback 和 provider selection 留 UI 层；核心仅收显式值，
已转换字段不二次执行 `str` 或 model auto fallback。本地路线不查询 provider，
签名字节/字段顺序/旧版本与错误传播保持不变。
`_history_meta` 现在主线程创建只读兼容视图，旧历史字段不变，另包含共享 `RequestSnapshot`
和独立的取消/stream session 引用；仍复用已有 frozen `ProviderRequest`，不替换 provider 契约。
Mac 无需导入有 AppData/Tk 副作用的 `cc_core`，只随包验证纯模块；完成证据以 TODO 为准。

### 请求执行快照（2026-09-14，已验证共享依赖与Windows接线）

`cc_request.RequestSnapshot` 无用户 I/O/环境/platform 依赖，配置映射转只读映射，嵌套list/tuple转tuple，
保留未知字段/顺序与原值，不再次规范化配置；拒绝隐藏可变对象及循环，不回显 caller key。
既有 `ProviderRequest`/`ProviderSelection` 独立复制，image paths 变为 tuple。
快照持有执行输入、prompt、所选 profile/执行 model、cache 签名、方向/目标语言/分类/任务；
`with_timeout()` 只生成原契约的超时副本，不修改捕获值或旧 60/90 秒选择。
方向不适用的图片、词典/代码专用提示词和非翻译追加动作不伪造单一 target language。

Windows 主翻译（cold/stream/warm）、vision、词典补充及结果追加动作在启动 worker 前捕获，
实际 provider/CLI 消费该快照，不在执行中重取 prompt/model/stream 开关。
warm 候选除原 key 外必须匹配捕获的实际 prompt，防止相同 model/direction 却语言不同；
不匹配只在 query 未发送时回到原冷路径，不增加已提交请求重试。
`_record_history` 仍在实际写入时检查当前 job、当前 history 开关/上限。
vision 仍在原 UI 回调写入，结果追加仍不写历史；取消 event、UI session、窗口身份不在快照内。

调用方不得在捕获配置期间并发修改输入；这不是 Windows 全 App cfg 锁或恶意对象沙箱。
图片文件仍由现有 UUID 临时文件生命周期持有，冻结路径不等于冻结外部文件字节。
Mac 随包验证共享契约；本切片不增 helper operation、Swift 消息或 GUI，
下一步才将该依赖接到 Darwin provider 的真实执行链。默认诊断启动零业务 I/O 不变。
实际源码为 `8797fc7acbc9de9fad05d47f394589c3305da2ba`，不是文档提交的产物。
同包15.7.9/14.8.9/26.6.2全部关键steps通过；
新增反例先证明旧warm key会取错prompt，随后修复并通过，不以首次失败作成功。
完整675库存/65资源/37源码路径及三系统相同archive/tree已核验，
[精确计数、制品与限制](MACOS_TODO.md#request-snapshot-checkpoint) 以本检查点为准。

### Darwin native Codex 内部执行边界

`DarwinCodexProvider` 必须显式提供绝对 CLI/work/cache 路径、含绝对 HOME 的独立环境与日志接收器。
构造不启动进程；工作目录在显式 home 内，catalog 扫描在该 home 停止，
不从 ambient 环境补 CLI 配置。原 Windows 无新参数路径仍保持惰性 home、配置和日志语义。
当前仅 text/translation_summary，无图像能力；不支持的任务明确拒绝且未提交。
`complete` 也走 native app-server，不冒充 exec 兼容。稳定CLI最低版本0.146.0，
新版本仍须通过实际协议与目录检查；原安全override/catalog、
prompt/thread/turn/item身份严格检查；Mac拒绝全部已启用hook及工具/server request，不沿用Windows例外。

RPC使用同步selector和nonblocking管道，8MiB每operation累计预算包含空行和已消费行；
写时先排stdout以避免双向背压，每次有界检查取消/deadline。leader未reap时先清自有组再wait，
不按进程名或裸PID事后补杀；stderr不存原文。未知callback/编程错误仍传播，
已知I/O只输出固定码；selector异常也必须释放process owner，cleanup失败sticky且禁止再启动。
前台/warm/关闭串行，前台可中断预热；预热不发query/turn，也不证明已认证。
首次实际写turn字节即保守标记submitted，部分写入/丢响应不能假称未执行或自动重放。
同线程重入明确拒绝；取消/超时不跳过真实后代清理。冻结快照用于实际后端测试，不含取消/UI对象。
审查修复为原idle scheduler增加原子generation条件：操作锁忙时保留回收责任，
旧generation/关闭不重排；同模型warm快速返回后仍能回收，不重复获取非重入state lock。
native按operation隔离item终态，拒绝完成后重复/迟到事件，同时保留不同item及进程复用；
传入共享parser前明确校验item类型和agent text/phase，不扩大异常捕获。

修复源码 `6d029d1b7418be2c6d7ae47c1babab550ac441b3` 的同包三系统及
[完整制品/hash/旧反例/修复中失败](MACOS_TODO.md#darwin-native-checkpoint) 已记录；
完整681库存/71资源/43源码路径和6实际Mach-O核验完成，临时归档已清理。
原9项Foundation仍覆盖诊断/config/history，**此后端检查点未增加翻译IPC或UI**。
后续已用同一后端接显式翻译业务连接与Swift API，见下文；普通启动、hello及诊断/config-only不自动运行CLI。
合成测试与官方CLI安装/账号/真实模型、Finder/TCC/IME/多屏验收始终分开。
不因付费签名资格冻结安全工程开发；旧WinError5未知风险不变。

已完成的存储基础层用显式 home/应用身份分离 Application Support 与 Caches，
路径解析不创建/迁移目录。身份沿用已校验 Info.plist，由调用方提供，不读取用户业务配置。
共享原子 JSON primitive 由 Windows 兼容入口实际使用；Mac 合成临时目录诊断通过 CI 显式调用，
不增加 runtime JSON/设置 UI，不选择真实用户数据目录。原子替换不等于完整配置/历史唯一 writer，
Windows 默认目录、迁移、日志与 schema 保持；历史的 add/clear 锁边界现已由下述仓库统一。
共享 writer 显式保留 FD 所有权直至关闭，补齐 fdopen 失败的释放；旧 JSON 字节与失败清理策略不变。
路径primitive是词法解析而非符号链接权限检查；上述基础层验证只使用caller-owned临时目录。
当前UI业务连接只有在用户明确启用后才选择其home，后续owner仍执行自己的路径与生命周期检查。
共享历史仓库现由 Windows load/add/cache/clear 真实入口使用；原数组/字段顺序/时间/限额、缓存匹配与
OCR 排除不变，已有 `cc_result_rules` 元数据路径不变。add/clear/load/cache 共用一把可重入操作锁；
clear 等正在写回的 add 完成后再删除，而在 clear 之后获得锁的新 add 仍可记录，不改变取消/隐私策略。
Windows 既有损坏读取日志/空视图兼容和写入错误日志留在 wrapper，不将其用作 Mac 默认错误策略。
Mac 必须显式构造 `MacHistoryOwner`，在 caller-owned 目录对稳定 `.lock` 侧文件取得非阻塞 flock；
JSON replace/clear 不替换或删除侧文件，close 与操作互斥，close 后拒绝写入，fork 继承对象拒绝操作。
只在显式 Darwin 构造时导入 fcntl；损坏/读取错误直接传播，不当空历史覆盖。异常/崩溃接管已用真实
包内合成进程验证，但锁是协作式，不是抵御恶意目录替换的权限系统。单进程仓库本身不是跨进程锁。
原history fixture仍由CI单独显式调用；后续业务切片已通过`cc_macos.history`固定仓库策略，
在同一helper/FIFO及双owner生命周期接入分页/记录/清空，Foundation实际验证该业务链。
默认原生UI没有业务连接或历史按钮；不会仅因启动就选择用户数据目录。
无 I/O 配置规则已移到 `cc_config`，原始抽取时类/常量 AST 一致。本轮仅将 `_coerce` 循环提取为
单一 `coerce_config`；冻结旧方法恢复原类后仍核对原指纹，并继续真实 Windows 差分。
Windows 导出同一常量/默认 dict/Config 对象，`_coerce` 默认容错策略和 typed accessors 保留；
未知键与嵌套对象身份、字段顺序、缺省语言键和原转换异常范围不改。
`load_config` 仍在原 Windows 路径读取和记录错误，但实际调用共享 `plan_config_migration(raw, cfg)`。
计划只补原 raw 副本的 UI/Labs 标记和 streaming 字段，并按旧逻辑升级显式 cfg 的 streaming 开关；
不会把全部归一化字段写盘，最多原 save 入口一次，保存失败仍保留旧盘/返回已迁移内存。
新 `ConfigRepository` 将读取、严格规范化、raw 迁移计划和必要原子写入放在同一操作锁内，
只有打开时明确缺失返回默认且不写配置文件；损坏 JSON、非 dict、编码/权限/转换/迁移写失败原样抛出。
新服务用同一转换循环的 strict 模式，TypeError/ValueError 不回退后写盘；Windows 默认模式不变。
`load()` 返回新 Config 派生视图，不缓存内部可变 cfg；`save(dict)` 返回 None，写独立 JSON 快照，
不把 normalized Config 或迁移标记自动混入显式保存。快照采用 Python JSON 往返：
tuple/可序列化键按 JSON 规则转换，非有限数沿用既有 writer 语义；非法业务字段可原样保存，
但后续严格 load 会显式拒绝。调用方不得在创建快照过程中并发修改输入。

Mac 的 `MacConfigOwner(home, application_id)` 用显式绝对 home/已验证应用身份选择
Application Support 下的 `config.json`，调用方先创建目录并用 context/close 管理生命周期。
不读取默认 HOME、不扫描/迁移旧 Windows 文件，不在失败后转写 bundle 或仓库。
history/config 共用稳定侧文件所有权原语；config 的 `config.json.lock` 不随 replace 删除，
第二 owner 非阻塞拒绝，close 与整个操作互斥，关闭后拒绝操作，fork 子不 unlock 父。
获取 owner 可创建侧文件，但缺失配置的 load 不创建 JSON；协作式锁不是恶意篡改权限沙箱。
配置 owner 的已完成证据以 TODO 为准。后续业务切片已接同一 owner 到私有 helper 与
Swift `startConfiguration(runtime:home:)`、`loadConfiguration`、`saveConfiguration` API；
真实测试只选择临时 home + 所选 App 实际 Info.plist 身份。
历史主链 `9614eab` / run34808290474 虽绿色，但独立review确认保存/迁移预算缺陷，未最终接受。
初始化路径环补充 `4d769e7` 当时的正常Windows hook复现旧WinError5，1274项中2条关联断言失败。
随后实质可读性修复 `c459652` / run34809961745 已正常hook1280通过并推送，
同包15/14/26三系统实际76进程/218核心/5Foundation通过，含路径环及所有新反例。
历史失败不删除，不把这次正常成功当作Windows拒绝来源已解决。
精确源码/制品/hash与失败见[配置业务检查点](MACOS_TODO.md#configuration-ipc-checkpoint)。
没有设置 UI，不表示共享 Windows 默认对应的 Mac 功能已就绪。
真实用户路径选择/旧文件迁移服务、后台共享 cfg 与 UI 保存竞争仍未完成；
此配置检查点不覆盖后续请求快照，快照进度见上方独立记录；
历史业务helper独立切片已完成同包三系统自动化，证据见TODO；本owner不解决整个App的可变状态所有权。
Windows WinError 5 的已有复核和实际旧 writer 对照均失败，拒绝来源仍未知；
不以纯规则/旧三系统成功覆盖该阻断，不新增重试或弱化旧测试。最新实际验收见 TODO。
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
默认 P0 诊断模式没有业务配置/历史写操作，不能将探针当作最终翻译业务协议。

上述表格与四个并行任务属于默认诊断模式；显式业务连接不混入诊断能力：

- 调用方显式调用 `startConfiguration(runtime:home:)`；从所选 App 的 Info.plist 读取真实
  bundle ID，启动参数固定传入一次绝对 home/ID，不读取环境 fallback，不允许请求指定路径。
  初始模块导入/参数解析无用户文件 I/O，首次有效空 hello 才创建 Application Support，
  依次取得config/history两个owner；第二个失败必须关闭第一个，关闭历史失败仍尝试关闭配置。
  原生 App 启动/打开现有诊断面板仍不选择业务连接，不新增用户操作。
- 业务 ready 保持同四字段，但 `fixture=false`，capabilities 精确为
  `config_load`、`config_save`、`history_load`、`history_add`、`history_clear`。
  等待 ready 后才调用读写 API；普通模式仍 `fixture=true`，
  仅原两项能力。模式交叉、未知字段/ID/乱序/重复键均严格拒绝，不自动降级或重试。
- load 请求为 `{"operation":"config_load"}`，完成为 `{"config":{...}}`；
  可选布尔字段`defaults`缺省/false时保持原读取；true时仅返回共享`DEFAULT_CONFIG`的独立副本，
  不读取、迁移或写入用户配置，不读取历史/词典。它仍要求已显式打开的有效owner；
  不新增operation/capability，也不改变ready集合。预览默认值不等于执行恢复。
  save 请求仅 `{"operation":"config_save","config":{...}}`，完成为 `{"saved":true}`。
  config 必须是 JSON 对象，紧凑 UTF-8 编码不超过16KiB，根深度1、键/值也计下一层、最多10层；
  数值有限且绝对值不超过 `2^53-1`，布尔与数值严格区分。完整帧仍64KiB/16层。
  raw 和规范化后的可返回视图均先校验，但 save 只写 raw，不 dump normalized 视图。
  保存还预判真实raw迁移payload；原始和未来迁移的indent=2 UTF-8实际表示
  （按writer平台换行）必须不超过65535字节，为现有有界decoder追加的LF预留1字节。
  不扩大wire/decoder，不改原writer字节；即使compact合法，缩进扩张过大仍明确拒绝保存。
  严格磁盘 decoder、返回值检查和独立validate_migration(payload)都在owner操作锁内、迁移写之前；
  不允许只因normalized视图变小就把超限raw迁移写回。缺失读取不调用迁移validator、不新建配置。
- 固定错误为 `config_in_use`、`config_unavailable`、`invalid_config`、`config_io_failed`，
  加既有协议/调度错误；双owner资源关闭失败为`state_io_failed`。
  不向 stderr/报告输出路径、配置内容或异常原文。
  只有真正缺失返回默认且不创建 config.json，坏文件不当空配置覆盖。
- 单一 FIFO worker，最多4个排队/执行中任务。accepted 只是排队；started 后 load 也可能迁移写，
  不能撤销。排队取消可产生 cancelled；started 后 cancel 控制返回 `cancel_requested=false`，
  原操作仍完成/失败。每请求唯一终态，迟到响应和重复 ID 不导致重放。
- 唯一accepted后、started前的确定failed例外是`worker_start_failed`：seq1，
  必须accepted且尚未started，表示线程未启动、业务配置/历史操作未执行，不应报OutcomeUnknown。
  同code在seq0/已started/seq2、其他failed缺started，以及重复终态均拒绝；
  其他accepted业务failed仍要求started/seq2。Python原失败与双owner释放行为不改，不自动重放。
- EOF/正常 shutdown 停止新请求、取消尚未开始的任务，等待已开始操作后关闭 owner；
  shutdown 完成帧在释放所有权之后发送。配置正常 stop 不沿用诊断两秒 worker join/
  三秒终止期限；显式 forceStop、传输失败或超时仍可能导致结果未知。
  Swift 对未见终态的配置请求报告 `configurationOutcomeUnknown`，不声称取消已提交写入或自动重放。
  原子文件完整性不等于回滚/完整事务，协作侧文件锁不是恶意篡改沙箱。
- 配置检查点的Foundation后置集成精确执行五项
  （诊断、配置读保存重开、坏盘保护、竞争接管、保存/迁移可读性预算），包内进程测试另用真实 fsync 后的测试端 FIFO
  屏障验证 cancel/EOF/shutdown/丢 stdout，生产没有测试开关。
  全部五项及路径环/可读性反例已在c459652的新三系统运行通过，不继承旧绿色；证据以TODO为准。

历史业务沿用同一FIFO与双owner连接，不新增请求路径、Server或Swift直接写文件：

- `history_load`必含`operation/page_size/cursor`，page_size为1..100严格整数；
  P3源码扩展可选`query`（默认空字符串，沿用24000 UTF-8字节预算）与`kind`
  （默认all，可选all/text/dict/code/ocr）。不增加operation、配置前置或模型请求。
  先完整读取并验证历史，再在全库input/output/ts内搜索及按类型筛选，最后分页；total是匹配总数。
  复用Windows纯过滤函数：仅query合并空白并casefold，不改变历史空白/Unicode内容；
  旧kind缺失或未知时按is_code、is_dict、text依次回退。
  cursor为null或`{revision,offset}`，revision为64小写hex、offset为1..10000严格整数。
  完成恰含`entries/revision/total/next_cursor`，保留原新记录在前的数组顺序。
  返回最大可放前缀，完整envelope/ID/seq/metadata/UTF-8/LF都计入64KiB，不套配置16KiB预算。
  完整尾页去掉cursor可能反而更小，必须单独验证，不据中间前缀超限就丢掉可读尾页。
- revision绑定连接随机代次、成功add/clear次数和文件原字节hash；成功mutation、外部字节改变、
  新连接使旧cursor明确`history_cursor_expired`，配置save不影响历史cursor。不提供无限快照缓存。
  P3筛选revision同时绑定归一化query和kind，改条件必须从null cursor开始；只改page_size不使cursor过期。
  空query/all保留原revision算法、返回形状及默认请求字段。P3源码eaf0c15已另行通过同包三系统，
  见[全库搜索证据](MACOS_TODO.md#native-history-search)，不是沿用旧历史业务包。
  非尾页非空且cursor offset连续；尾页恰好结束于total，绝不把超限/坏文件当空历史。
- `history_add`恰含`operation/input/output/is_dict/is_code/kind/sig/limit`；
  input/output各最多24000 UTF-8字节，sig最多4096字节，kind为text/dict/code/ocr，
  flags严格bool，limit为1..10000严格整数。完成`{recorded:true,revision}`。
  原仓库创建timestamp及字段顺序、保留签名字节；实际未来数组和每条最坏分页envelope写前校验，
  原indent2/flush/fsync/replace不改，不接受会导致后续不可读的新记录。
- `history_clear`仅operation，完成`{cleared:true,revision}`。包括空库也推进代次；
  这是显式删除，可清坏文件，绝不开机自动清空；started add写完后才执行已排队clear，
  之后新add仍可写。尚未自动记录模型请求，也没有历史UI。
- 文件最多8MiB/10000条，entry相对最多13层、安全有限数且abs不超过`2^53-1`；
  legacy已知字段的原string/null/bool规则及未知字段保留。超限拒绝，不截断文件。
  单条既有记录无法放入合法页时`history_entry_too_large`且原盘不变。
  固定错误另含`history_in_use/history_unavailable/history_io_failed/invalid_history/history_too_large/
  invalid_history_record/invalid_history_cursor/history_cursor_expired`，不泄漏内容/路径。
- `MacHistoryOwner`公开构造仍只接受path；内部固定业务仓库策略复用该owner和原HistoryRepository，
  不开放可绕过严格读取的任意reader/writer注入。两个侧文件不随replace/clear删除；
  锁是协作式，不是防恶意目录篡改沙箱。started操作/失响应/强杀的未知结果不自动重放。

本轮新Swift调用链、精确Foundation集合与同包三系统执行结果见[历史业务检查点](MACOS_TODO.md#history-ipc-checkpoint)；
旧配置五项成功不冒充新历史链已验证，不扩大UI/provider/完整翻译承诺。
Swift提供`startBusiness`兼容别名以及`loadHistory(pageSize:cursor:)`、
`addHistory(input:output:isDict:isCode:kind:sig:limit:)`、`clearHistory`；
沿用唯一ID/严格事件API。历史未见终态的断连报告`historyOutcomeUnknown`，
混合队列还有未终态配置时保留`configurationOutcomeUnknown`优先，均不代表提交写入已回滚。
强制Foundation集合为原5项加历史生命周期分页/坏盘预算保护/双owner竞争3项，
再加实际Python worker失败frames跨端消费及重开1项，全部必须真实运行且无skip。
源码`4021270` / [run34817356816](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816)
现已实际达到新门槛：同包15/14/26每系统90进程、242核心、9精确Foundation，0 failures/errors/skips；
普通Swift64项里的9次初始无包skip不作为后置证据。Windows最终正常hook1306项通过。
旧dc0ba9c绿灯虽通过原8项，却漏掉worker未启动分支；独立review后精确修复，没有整体放松guard。
两轮更早真实Mac测试前置/库存失败和修复仍见TODO。
本地独立核对674库存/64资源/36源码路径及同包内容、模式和链接，新内层zip
SHA-256为`bb607badc1cb5de1c489d9c21431a9304f3d365e08d96375c64448bf7bc71741`。
该历史检查点当时未接翻译/UI，也不继承旧包Finder/TCC报告。

### 显式 native 翻译协议与生命周期（当前）

默认诊断连接保持原协议和provider惰性导入。`startTranslation(runtime:home:codexCommand:environment:)`
是独立、明确的选择；实际bundle ID仍由Info.plist取得，home/CLI绝对路径只在启动时绑定一次。
CLI环境是严格string→string JSON（最大32768 UTF-8字节），经私有
`CC_TRANSLATE_CODEX_ENV`传递，HOME必须等于显式home且必须有PATH；不放argv、不从环境补值，
也不将CLI环境直接变成helper的加载器环境。默认诊断模式不创建业务目录/读取配置/启动CLI。
启用时获取config/history双owner，provider构造不spawn；首次translate或明确结果操作才执行native CLI。

协议版本仍为1，4807f62已交付的ready为`fixture:false`、`backend:native_appserver`及七项能力：
原config/history五操作加translate/result_action，不把业务称synthetic。translate精确接收
`operation/text/app_language/origin/use_cache/record_history`；text非空白、UTF-8最多8192字节，
language只zh_CN/en_US，origin为text/selection/ocr，两个布尔不接受整数替代。
配置决定Codex profile/方向/summary/限额；payload不接受每请求path、env、model或服务端timeout覆盖。
Swift API的timeout只控制客户端等待，不改写执行快照的服务端预算。
原load的streaming强制迁移保持，所以磁盘false不代表已提供非流式设置开关。

`origin:ocr`仍是文字请求，不接收图片、路径或image任务。保留原始Unicode、换行和空白，
同时受8192 UTF-8字节及已提交配置的MAX_CHARS限制；不截断。OCR跳过本地词典查询、
历史缓存和自动摘要，即使调用方传use_cache:true也再次提交。普通/混合文字追加共享的
OCR排版提示，Windows真实调用同一helper，提示字节与抽取前一致；单词/纯代码仍使用
既有AI词典/代码解释提示，target_lang为null。所有OCR完成结果及历史kind都为ocr，
历史is_dict/is_code保留分类，记录仍服从最新历史开关。用户之后主动选择摘要动作不受此限制。
`dictionary_lookup`仍只允许text/selection；helper升级时已有的dictionary_status刷新不是OCR查词。

`result_action`精确接收`operation/action/text/app_language/target_language`，
action为concise/formal/summary/explain_code/as_text/retranslate；text非空白、原始UTF-8最多24000字节，
以容纳合法主结果，不沿用翻译输入的较小字符限额。仅retranslate要求支持的目标语言代码，
其余target_language必须null。动作复用共享prompt/RequestSnapshot和原provider执行链，
无cache查询或history读写，返回cached:false、kind:text、summarize:false、history:disabled。
target_lang仅as_text/retranslate具体化。Swift将其作为模型请求处理取消、总线预算和unknown优先级；
结果以请求ID/显示代际追加，不覆盖主结果，也不将追加文本反复作为下一次改写输入。
此动作链已在4807f62同包三系统验证；64d80a0只包含前一个界面切片，不代作动作证据。

共享摘要规则已原样抽取，Windows真实重导出/消费者及patch seams保持；
helper捕获完整不可变RequestSnapshot，复用分类、方向、prompt及原cache签名字节。
翻译worker可与原storage FIFO并行，native执行本身串行且不占状态操作锁；
完成时在锁内重读**已提交的当前**history开关/limit。显式record_history=false不写，
history关闭时也不查询cache；命中只读且不重复追加。执行输入冻结，不冻结取消/UI状态。
原子replace不等于单writer，协作flock也不是恶意目录的沙箱；输入快照期间调用者不能并发修改输入容器。

事件为accepted(seq0)→started(seq1)→delta→唯一终态；未启动worker的精确worker_start_failed仍为seq1确定失败。
delta含text/submitted:true；completed含text/submitted/cached/kind/target_lang/summarize/history/history_error。
cache命中submitted=false、history=unchanged；history=failed必须有固定storage错误码，其余必须null。
已产生delta后不允许终态submitted=false。最终文本可修正流式中间文本，不要求二者拼接相同。
单delta的compact JSON字符串最多4096字节，累计与最终文本各最多24000字节，计UTF-8及转义；
每请求实际完整响应envelope/序号/LF累计最多1MiB，单frame仍64KiB，超限不截断或写坏历史。

queued取消可以确定未执行；started翻译的取消终态必须等native实际清理。
开始不可逆历史提交后不撤销写入；保存失败仍交付翻译文本并显式标history=failed。
丢响应/超时/强停时pending翻译优先报告translationOutcomeUnknown，不假称回滚、不自动重放。
EOF/shutdown停止接新请求并drain；4807f62仅translation入口捕获SIGTERM，handler只置标志，
正常循环清理自有native组及双owner后退出143。Swift普通stop不自动强杀；
故障为关闭stdin→30秒→SIGTERM→30秒→SIGKILL，forceStop直接SIGTERM后留30秒。
这些界限不承诺SIGKILL、崩溃或脱离组后代的回收，不引入guardian或按进程名杀用户CLI。

初始translation接线历史包实测同包15.7.9/14.8.9/26.6.2、185process/458core/13Foundation；
原172/410/9全部保留。首轮无App前置上下文错误、正常Windows hook及完整制品审计见
[当前检查点](MACOS_TODO.md#translation-ipc-checkpoint)，不以初次13个可选skip替代后置执行。

### 无Codex本地词典：原生下载、首屏与管理

纯lookup/artifact/presentation模块不导入Windows默认路径、Tk或provider；Windows保留兼容facade，
原format-v8及自动AI补充行为不变。Mac的本地命中不调用CLI、不自动补充AI。
已有有效CLI时可复用原translation连接（构造不执行CLI）；没有CLI时使用configuration连接，
查词先于CLI设置提示。已知miss/disabled/unavailable/ineligible才沿原明确翻译意图继续；
取消、失败或unknown不是miss，不能据此提交模型。

两种业务连接各增加以下六项能力，因此configuration为11项，translation为13项；diagnostic不变：

| 操作 | 除operation外的请求字段 | completed |
|---|---|---|
| `dictionary_status` | 无 | state、enabled、size、sha256、data_version、download_url、entry_count |
| `dictionary_lookup` | text、app_language、origin、use_cache、record_history；复用翻译输入约束，但origin只允许text/selection | status、result；仅hit有结果，其余result=null |
| `dictionary_prepare_install` | 无 | ticket、path、url、size、sha256、data_version |
| `dictionary_install` | ticket | 校验/提交完成后state=ready、enabled=true的完整status |
| `dictionary_discard_install` | ticket | discarded=true |
| `dictionary_delete` | 无 | deleted布尔、enabled=false |

固定数据为dictionary-v1、67,948,544 bytes、SHA-256
`3695295d07268725e555471217351c74e4f9375b8152974760fe12b926e1830e`。
prepare只注册本连接的随机ticket及**尚不存在**的本App dictionary目录直属路径；
客户端不传自定义路径、URL或pin。URLSession完成有界下载后不覆盖地移动到该路径；
核心验证大小/hash/schema/版本、fsync并原子replace，之后才合并最新配置开启本地词典。
下载期间不占config/history FIFO。传输失败/取消走显式discard；已提交install的失败或正常取消
消费ticket并清理自己的staging。cleanup_failed保留清理责任，不假称回滚，关闭时继续尝试。
禁用保留数据库；删除必须显式确认。

查词沿用NFKC/trim/casefold、exact→form→alias及原置信度策略；原生纯文本完整保留分组义项、
来源提供的读音及来源/版本/许可，不显示或复制内部rich markers。缓存签名独立为native-plain-v1加语言，
先查实际词典再查缓存，缓存命中不重复历史，落历史前读取最新已提交开关。
历史关闭不读缓存；缓存/历史损坏时本地命中仍显示，并明确history=failed，不修复损坏历史或改调模型。
本地结果恒为submitted=false、kind=dict、target_lang=null、summarize=false。

词典使用独立串行worker，与config/history分属FIFO。accepted→started→唯一终态，无模型delta；
cancel确认不等于清理完成。queued取消若清理失败可在未started时以seq1返回dictionary_cleanup_failed。
pending模型仍优先unknown，其次dictionaryOutcomeUnknown，再config/history；不把本地失败说成已计费。
本轮显式configuration和translation入口均以只置标志的SIGTERM handler走正常取消/drain；
两种业务连接的故障宽限均为EOF30秒→TERM30秒→KILL，diagnostic仍为3秒/1秒。
SIGKILL/崩溃清理、完整真人GUI及性能目标不能由这些接口代码推定通过。

源码`b86507f`的正常Windows完整hook1687项通过（85.703s）；最终针对性132项/15.475s，
另以隔离复制Core路径实跑536项/12.126s，不让子进程回退checkout。
同一个producer App在15.7.9/14.8.9/26.6.2各202process/536core/17后置Foundation通过，零失败/skip。
新增两Foundation使用外置固定词库验证无CLI安装/查词/历史/失败保留/删除，
并记录八次明确标注非GUI的warm往返。另一个后置产品测试使用生产URLSession和实际随包helper，
真实下载67,948,544字节（CI网络622.187ms，不承诺用户网速），校验安装后进行两次warmup及十次绘制测量。
意图到同源SwiftUI/AppKit离屏绘制P95/最大37.747ms，实测达到此端点的150ms目标，来源OCR可见。
这不是物理键盘或打包GUI进程的完整首屏验收，也没有独立测量10ms查询+格式化。
21张合成数据原生截图包含浅/深词典管理、完整字面义项、长文滚动及AI词典Markdown回归；
历史仅对local-dictionary签名采用字面呈现，不把AI词典格式一并移除。
CI的Python固定输入获取与这次真实URLSession测试分别记录；词库不放入App制品。
完整失败经过、同包结果和独立App核验见[词典检查点](MACOS_TODO.md#native-dictionary-checkpoint)。

诊断最多 4 个并行任务，超限在该请求上返回 `failed/busy`。取消只作用于目标请求；
控制请求的完成不等于模型取消成功。每个业务请求恰好一个终态；完成与取消竞态由核心串行决定。
前端保留每 ID 序号验证，切换当前请求后忽略旧请求的 UI 结果；未知 ID/乱序为协议错误。
握手超时、异常退出或协议错误要可见，禁止 success-shaped fallback。原生持续排空两个输出管道，
有界读取，避免 pipe 堵塞；退出关闭 stdin，超时仅终止本 App 的 helper。
P0 初版 helper 不创建 CLI 子进程；当前 P1 显式包内诊断会创建纯合成 config CLI，
不运行用户的真实 CLI/账号。原生显式 `--version` 探针的 P1 监督切片用
`posix_spawn` 原子创建独立进程组，在正常退出、取消、超时和输出超限后清理同组后代；
TERM 宽限后升级 KILL。先用 `waitid(WNOWAIT)` 保留 leader，再发最后一个组信号、reap，
避免 PID/PGID 复用误伤。只对自己创建且尚未回收的组发信号，不按名称搜索/结束用户 CLI。
这不是完整 ProviderRuntime；主动 `setsid`/改进程组逃逸的 wrapper 不支持，也不跨组追杀。
无法确认子进程所有权时显式失败且不再发信号；系统无法完成清理时显示失败并保留回收责任，
不报告成功。Windows provider 默认行为保留；实际 native 配置/认证和模型请求仍待单独实现与验证。

Codex 配置探针复用既有 `read_native_config`，Darwin 分支保留原 argv/env/cwd 与安全覆盖，
只发送 `initialize` / `config/read`，不改模型/认证选择，不创建模型 turn。CPP 组信号原语
同时构建成项目自有 `Helpers/python/lib/libCCProcessSupport.dylib`，与 CPython 分开标识来源；
Python 只从包内固定位置加载且要求 ABI 1，不搜索宿主库或降级到裸 PID。
非阻塞 stdin/stdout 共用 8 秒 RPC 预算，最多接收 8 MiB；stderr 丢弃，错误为固定代码。
不在最后组信号之前调用 `Popen.poll/wait/communicate`；先 TERM、200ms 后 KILL，再有限等待回收，
关闭全部管道。丢失子进程所有权后不补杀/再 wait，清理错误不能伪装成功。
可选取消事件目前仅 Darwin 接入；其他平台若显式传入则明确拒绝，Windows 现有默认调用不变。
包内合成诊断传入 helper 工作项取消事件，确保 EOF/退出能取消正在等待配置的自有组。
宿主/Windows 诊断明确报告 `codex_config_fixture: not_run`；真正包内 Mac 必须报告四字段成功，
由原生协议和 smoke 双重严格校验。此结果不是用户 native 配置/账号兼容证据。
这里“只读”指 `config/read` RPC，不是承诺未测的官方 CLI 初始化没有磁盘/认证副作用；
当前诊断只运行合成 CLI，真实用户环境仍须单独验证。

协议结构性错误为连接级失败（保留 ID `protocol`），取消所有本连接任务并退出非零。
完整帧后的正常 stdin EOF 取消工作并退出；残缺帧 EOF 为错误。原生不得自动重发已提交的付费请求。
预热不发送文本、不创建付费 turn。

## 4. P0 原生探针和安全行为

普通启动只显示菜单栏图标，不弹窗、不申请权限、不联网、不探测用户选区。
用户通过显式菜单打开 P0 面板、运行合成 IPC、运行时自检或平台探针。
结果窗口与快速输入仍是开发界面，不是已完成产品UI。当前已有显式native启用、输入/AX翻译、
流式结果/复制、设置保存和历史分页/清空；只有用户另外开启选区翻译时，被动Cmd+C才可触发模型。
关闭窗口停止连接；关闭结果窗口会请求取消当前翻译。GUI/TCC/IME/多屏行为仍须真人验收。

### 权限、选区、快捷键和剪贴板

- 选区建模为 `present / absent / unknown`。AX attribute 不支持、权限拒绝、Secure Input、
  目标失焦/退出均不能解释成“确认没有”。unknown 不读旧剪贴板。
- AX 只读取选中的文本，不读取整个控件 value；探针从菜单切换焦点前固定目标 PID。
- 双击 Cmd+C 只观察用户事件，不吞正常复制，不写哨兵，不模拟复制，不撤销主动复制结果。
  探针可保守仅支持 AX；P2 再引入与本次目标/时间/changeCount 可证明关联的复制回退。
- 全局事件监听与 AX 授权分别报告；不以 AX trusted 推断所有权限已允许。
  按需请求辅助功能/输入监控/屏幕内容；通知和登录项是另外的状态，P0 不请求。
- Secure Input 下停用相关能力，不绕过。P3已实现的主动纯文本粘贴见[独立说明](#native-plain-text-paste)，不改变本节P0探针约束。
- 临时剪贴板事务只保守恢复；`changeCount` 不是原子 CAS。多格式、延迟数据、
  Universal Clipboard 和更新系统的 ask/allow/deny 单独验收。P0 不写剪贴板；
  主动纯文本粘贴是用户明确的去格式操作，不恢复旧内容或自动重放。
- 浮窗不激活并不等于无法接收键盘；快速输入主动激活、结果展示不抢焦点分别验证。

<a id="native-capture-ocr"></a>

### 区域截图、本地 OCR 与明确文字翻译

以下合同已由082aad6同App三系统自动化验证，完整源码/制品证据见
[截图检查点](MACOS_TODO.md#native-capture-checkpoint)；当前下载段已切换到经独立核验的截图包。
这不是用户账号模型或真人多屏/TCC/IME/VoiceOver签收。

菜单栏和主翻译窗口的截图入口使用真实原生选框，不再要求先打开诊断。
只有显式截图才申请屏幕录制权限；本地截图、裁剪、Vision和编辑不需要helper、CLI或账号。
选择区域后自动进行本地OCR，只有用户点击“翻译文字”（或在截图文字窗口使用Cmd+Return）
才进入上面的OCR文字请求。IME组合输入时不把Cmd+Return当成提交。

- macOS14使用ScreenCaptureKit的filter/configuration API，排除自身应用窗口，不采集光标或音频，
  不依赖15.2的区域API。所有显示器保留完成后才出现选框，后续裁剪、预览和OCR复用这些像素。
  多屏是逐一采样，不宣称严格同时曝光；旧诊断仍保留单屏4096像素上限与手动OCR确认。
- 显示器描述使用AppKit全局逻辑点、左下原点，记录ID/矩形/原始像素尺寸/旋转。
  支持负坐标、上下排列、混合缩放与反向选择；实际裁剪比例来自保留CGImage，不固定假设2倍。
  跨屏各自裁剪后使用统一比例合成，屏间空隙填白，边缘向外取整但不拉伸裁剪内容。
- 选区支持拖动或依次点击两角；方向键移动、Shift方向键缩放、Option细调为1点，
  F选择当前屏幕、Return确认、Esc/右键或取消按钮退出。最小有效区域10×10点。
  图像预算为保留32Mi像素/128MiB、合成16Mi像素/64MiB、最长边8192像素；
  过大屏幕组合在捕获前统一缩放，并为行对齐预留空间，不把常见双5K当成错误。
- 屏幕配置通知或ID/几何/缩放/旋转变化使事务失效，不自动重拍。权限弹窗返回时也核对代际，
  取消或新的显式截图不能被旧授权结果覆盖。无效选区会停止旧OCR并保留帧供用户重新选择。
- “在保留帧上重选”不重新截图。旧Vision尚在取消时，仅保留最新一次确认的本地OCR意图，
  退出后重新核对布局再执行，不并发堆积、不要求多点一次确认。
  重新截图若旧系统操作仍在退出，则明确提示稍候重试，不偷偷重拍。
- Vision使用准确识别，查询实际支持语言后选取英语/简体中文/繁体中文。
  OCR为空或失败仍可手动输入；文字超过8192 UTF-8字节保留完整内容供编辑，不静默截断。
  截图窗口最小620×600，窄窗口改为上下布局，浅色/深色保留可编辑文本和明确发送按钮。
- 取消、重选、关窗或退出会取消自己拥有的待提交/活动翻译并释放图像引用；
  不取消后来开始的其他翻译，不删除已经完成的结果。不可取消的系统工作可能稍后结束，
  其迟到结果不得恢复旧画面。仅使用本地OCR/“翻译文字”时，原始图像不写磁盘、不写剪贴板、不上传。

082aad6检查点本身不是视觉模型；后续源码已新增下面的明确图片发送，不将旧OCR包当作图片功能验证。
原生合成模型/图像/渲染与随包进程验证也不能代替真人TCC、VoiceOver、IME、多屏和Spaces验收。
OCR本地执行不意味着用户之后主动选择的AI翻译离线或免费。

<a id="native-image-translation"></a>

### 明确发送图片翻译（已包含在当前核验包中）

这是单独的“发送图片翻译”操作，不是把本地OCR文字改名为图片翻译。
当前推荐下载f9781b7保留bf89495已验图片链路并补齐Claude图片服务，已完成完整App同包三系统验收，见
[当前批次](MACOS_TODO.md#native-restore-defaults-checkpoint)，未声称完成全部移植。
以下保留早期工程包记录：
已提供的bfd9382工程验证包不受后续测试诊断替换；667d17b只增加私有剪贴板元数据诊断，
实际原生测试失败，未发布新App。其独立图片/随包阶段通过不是整体CI通过，
也不要求用户重新安装CLI、刷新目录或重做已通过的普通翻译。

1. 在主窗口或菜单栏选择截图，确认保留的区域。
2. 可以继续本地识别并编辑文字，再用“翻译文字”；也可以选择方向/模型后点击“发送图片翻译”。
   本地OCR为空或失败不妨碍明确发送图片，不要求先刷新模型目录，不增加精确CLI版本或模型白名单。
3. 图片操作只发送已选区域，以真实Codex `localImage` 输入完成一次请求；
   不发送整个显示器帧，不自动改发OCR文字，不自动重试或切换模型。
4. 取消会保留截图预览；图片翻译不取消仍在运行的本地OCR。
   图片结果可复制输出和执行输出相关操作；不会伪造原文或提供虚假的双语复制。

只有点击图片发送才编码选区并创建私有PNG；目录/文件权限分别为0700/0600。
沿用16Mi合成像素和8192最长边的图像预算，PNG最多80MiB；JSONL仍为64KiB帧，
只携带路径、字节数和摘要，不内联base64。helper验证后使用自己拥有的副本供provider读取。
两个文件所有者分别保留资源直到对应请求终态或连接排空，取消ACK本身不等于可以删除。
创建后的回滚失败、迟到创建和删除失败均保留清理句柄；界面可明确重试清理，
退出时等待排空，失败则让用户选择重试或在知悉可能残留后退出。
不承诺SIGKILL、崩溃或断电后的自动清理，不扫描或删除其他临时目录。

图片翻译不使用词典/翻译缓存或自动摘要。实时已提交的历史开关仍有效；
历史只保留翻译结果，原文为null，不存图片、路径或图片摘要。
没有使用真实账号/模型验证识别质量；现有原生图像、UI和随包合成测试不冒充这一项验收。

<a id="native-dictionary-sources"></a>

### 词典结果的来源与许可（已接线，原生验收进行中）

本地词典查询返回结构化来源时，结果状态栏显示“来源与许可”按钮；点击打开原生弹窗，
可选择、滚动阅读来源标识、版本和许可原文。内容按普通文字显示，不解释HTML/Markdown，
不打开来源中的任意网络链接，不发起模型请求或重新加载词库。
仅说明本地词条的来源，不为模型补充内容背书。

按钮保留同一原生实例；相同来源的追加输出、语言/主题变化不销毁已打开详情。
来源确实替换时关闭旧上下文，旧按下事件不能在释放时打开新来源；
关闭或Escape返回触发按钮，清空结果则退休对应控件。
模型结果、旧历史或缺少结构化来源的旧响应仍保留原有可读文字，不猜测或伪造新来源。
复制和历史里的原始归属文字不变，未修改共享Windows格式或历史schema。

来源来自既有共享`source_details`，随显式查询的`result.source_details`返回。
在历史提交前检查完整终态帧仍不超过既有64KiB；不截断许可，也不加模型/账号前置。
源码420928b及62a9219时序修正的测试、实际构建和仍未通过范围见
[来源按钮检查点](MACOS_TODO.md#native-dictionary-sources-checkpoint)，目前未替换推荐下载。

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
正式开源发布仍需明确应用自身许可，第三方 notices 不代替应用许可；
本轮只提供已授权的开发测试 artifact，不创建 Release 或重新授权第三方内容。
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
export CC_TRANSLATE_DICTIONARY_TEST_ASSET="$PWD/tools/macos/.staging/dictionary-fixture.sqlite3"
python3 -B tools/macos/dictionary_fixture.py \
  --download-to "$CC_TRANSLATE_DICTIONARY_TEST_ASSET" --allow-download
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

新增标准 `macos-14`/`macos-26` runtime jobs 依赖上述 producer，`fail-fast=false`。
它们只下载同一次 workflow 的精确 producer artifact ID；归档 SHA、源码 SHA、clean manifest、
资源库存以及文件字节/模式/相对链接摘要均与 producer receipt 比较。由同 SHA checkout 提供测试，
以 App 内 Python `-I -B` 运行原 core/process 清单和显式临时 storage fixture，没有宿主 Python fallback。
HTTPS/SQLite、helper 取消/EOF 与前后完整审计同样执行，不修改、重签或重建被测 App。
Foundation.Process harness 只复制原 support/C/集成测试，不包含 App target；
分别用预装 Xcode 16.2/26.6 编译，绝不把 harness 的编译身份混为产品的 Xcode 16.4。
每个 runtime 只上传四份小型 JSON，不重复上传 App；实际版本、计数和固定制品见本节末及 TODO。

不能使用开发机系统 Python 冒充随包核心。smoke 检查实际解释器是否在 bundle、版本/架构、
isolated 状态、SQLite 读写、真实 TLS、取消/EOF 及 bundle 不被写入。
Windows 是原生编译外部门槛，不通过大规模写未经编译 UI 来掩盖。

### P0 App 的显式验收入口（步骤模板；原包用户报告见下，不代表最新包实机已验）

1. 最新开发包Finder启动后仅应出现 `CC Dev` 菜单栏项目，不自动弹窗、申请权限或联网。
2. `Open input / diagnostics...` → `Bundled core` → `Start bundled helper`，
   再 `Run synthetic fixture`；结果必须标为 SYNTHETIC，不是翻译。用新输入和 Cancel/Stop
   验证迟到结果不污染当前显示，关闭窗口不退出菜单栏应用。
3. `SQLite / SSL / config (offline)` 与 `HTTPS probe (explicit network)` 分别执行；
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

这些是开发探针，不是完整产品 UI/本地化。完整性、首次 TCC、多屏、IME 和发行包的验收清单
仍全部保留，不能因为菜单或 API 代码存在而勾选实机通过。

开发 `.app` 与完整产品明确区分。免费路线的 P0 实机门槛是：从可信 GitHub 下载并校验，
经 Finder 正常打开（适用时由用户选择单 App 官方例外），再验证 helper、资源、SQLite/HTTPS 与首次 TCC。
2026-09-12 曾把 Developer ID + Hardened Runtime + 公证/stapling 作为唯一首测前置；
该方案已由 2026-09-13 免费分发决策取代。可选付费增强仍未实施，不是当前阻断或购买要求。
不要添加未经证明必要的宽泛 entitlement。未来每次更新是否保留 TCC 授权仍须实机验证。
最低 OS deployment target 的编译通过不等于 macOS 14 运行通过。

<a id="native-model-catalog"></a>

### 模型目录与选择

目录读取是可选的便利功能，不是翻译的新前置，也不要求重新安装或登录已经可用的Codex。
本轮实现及实际验证状态见[模型目录检查点](MACOS_TODO.md#native-model-catalog-checkpoint)；
下面步骤适用于下方已核验的新开发包。

1. 打开设置，在模型区域点击“刷新模型”。仅此操作读取当前所选Codex的模型列表，
   不发送翻译、不验证账号权限、不写Codex配置，也不会自动选择第一个模型。
2. 从模型选择器选中所需模型；设置、主翻译窗口和截图/OCR预览共用这些选项。
   在设置中选择会保存并显示回读结果；在主窗口/截图预览中选择后直接翻译即可，
   应用会沿用现有流程保存这次选择，无需先返回设置。手工模型ID仍可输入和应用；ID不被trim或重新规范化。
3. 可随时取消刷新。加载失败或返回空列表时，继续使用预设/手工ID，或者明确重试；
   普通翻译不因目录不可用而被禁止。只有用户的新操作才会在连接失效后重连。
4. 更换Codex路径会清除上一安装的目录，避免错用旧列表；再次点击刷新即可读取新安装。
   模型说明仅来自CLI元数据，不是账号可用性、费用或图片能力的证明。

本轮自动化只使用合成目录和合成翻译进程。可选实机检查聚焦刷新、取消、选择、重开保持，
无需重复已确认成功的普通翻译/账号安装步骤；不要求为了这项功能先完成完整权限矩阵。

<a id="native-plain-text-paste"></a>

### 主动纯文本粘贴

已包含在下方新的完整开发包中，见[同包三系统证据](MACOS_TODO.md#native-plain-paste-checkpoint)。
它是“去掉剪贴板文字格式并粘贴到正在编辑的应用”，不只是翻译输入框的Paste按钮。

- Settings → Plain-text paste / 纯文本粘贴中明确开启，快捷键为
  **Option+Shift+Command+V（⌥⇧⌘V）**。不开启就不注册全局快捷键，不监听/读取剪贴板。
  无需Codex、账号、模型请求或先运行Diagnostics；重开从已保存配置恢复明确开启的选择。
- 在其他应用的文本编辑位置按快捷键并松开按键，应用使用剪贴板中的文字去格式，
  再提交一次普通粘贴。需要时才提示辅助功能权限；开启开关不会弹权限请求。
  快捷键被占用会显示冲突，可释放其他占用后明确重试注册，不暗换组合键。
- 在CC Translate自己的编辑器中使用原生Paste and Match Style，不需要辅助功能权限，
  不向其他应用发键。全局开关关闭时，Edit菜单仍保留这一原生编辑命令。
  非激活浮窗持有键盘焦点时也不会把粘贴发给旧外部应用。
- 保留Unicode、制表符和换行。文字附带备用图片表示时只取文字；包含文件/文件promise的
  混合内容、图片-only、HTML/RTFD-only不转换，不为此加载图片或远程网页。
  支持纯文本及无附件RTF；不把格式转换变成AI翻译。
- 设置可显示保存/回读、注册、取消/停止和上次操作报告。关闭立即停止新动作；
  正在等待的外部数据返回后不会继续写入或发键。若格式已经移除，则如实显示部分效果，
  不恢复旧内容、自动重试或宣称目标编辑器已经插入成功。

前置模型目录包的实现使用MainActor上的AppKit changeCount及立即UTF-8写入；
可能等待外部提供者的数据读取和RTF解析使用后台队列中的C引用。
后续工程源码dbf5b33改为在原串行队列内、单次交互范围使用AppKit items/types/data，
不再依赖已实测跨不同私有资源返回旧ID的C枚举API。所有条目先做文件类型检查，
之后才请求精确文字类型或RTF数据；保持严格Unicode解码、取消、前后changeCount与一次性lease。
46项产品测试保留；C已知ID/字节/promise回调与读取后的AppKit条目顺序共同验证产品语义，
明确不再以C枚举器自身符合性作为测试目标。首次迁移实际发现UTF-16自动UTF-8别名将BOM
变为文字前缀，237f3c9尝试优先UTF-16。15/14通过，但26仍丢失UTF-8的literal U+FEFF。
原始日志核对后更正：26的case5实际取到了完整25字节UTF-8，并未选择UTF-16别名；
差异出现在Foundation UTF-8初始化。后续改用保留原字节的Swift解码/回验，
并按item自身广告顺序取表示；这套逻辑供主动粘贴和关联复制共用，实际原生结果见下。
接线源码c7745ec/run35284938518现已实际验证：三个系统46项主动粘贴和7项关联复制读取
均零断言失败，前缀和多编码回归通过。但15/26的真实AppKit promise产生后台线程警告，
26被既有检查拦截，只有14完成完整后置审计；不能把零断言失败等同于整个run通过。
该轮当时正在处理线程用法，保留取消/排空及主界面响应要求；其失败记录保留。
原始字节及新增literal-prefix/TSV矩阵继续保留，不以AppKit string便捷转换隐藏字节差异。
后续bf89495已采用下节的主线程只读worker并通过完整同包三系统验收；
这是具体源码和测试证据，不声称改变了Apple内部机制。
30秒读取期限后仍等待不能中断的调用drain；不承诺任意系统IPC绝不会阻塞。
检查不是跨进程原子CAS，也不替代真人外部编辑器、Universal Clipboard或系统访问策略验收。

<a id="native-custom-model-settings"></a>

### 原生自定义模型设置

此功能已包含在下方完整核验的新开发包中，证据见
[模型设置与OCR检查点](MACOS_TODO.md#native-model-settings-checkpoint)。
这一步是手动输入已有模型ID，不是动态目录、账号权限查询或完整模型管理。

- Settings的翻译区保留Fast/Default，并可输入自定义Codex模型ID，明确应用、重置草稿或
  重新读取已保存设置；主窗口和截图共用同一选择器。应用走原配置保存/回读协议，
  不运行模型、不要求先执行目录或版本检查。
- 输入草稿、当前选择和已读回值分离。只在对应保存请求的读回ID逐UTF-8字节一致后
  显示已应用；失败/不一致保留用户输入并显示实际状态，不悄悄替换模型或重放写入。
  保存期间、断连和迟到回调沿原请求生命周期处理；输入框使用原生field editor，
  不将翻译的Cmd+Return改绑成“应用模型”。
- ID保持大小写和Unicode原字节，不自动修剪或规范化；空值、空白/控制字符、
  超过已有256字节请求上限会给出输入提示。Fast/Default由预置选项选择。
  选择器的绑定、tag和列表identity也使用字节精确比较，避免Swift String的规范等价规则
  合并不同ID或丢失之前保存的选择。
  `gpt-5.4-mini`不是被禁用的ID：共享配置只迁移未标记的旧默认一次，
  写入`codex_model_default_migrated`；从已加载配置明确选择mini后，
  保存、读回、重开及请求快照不再将它改写成`auto-fast`。
- 最近确认的自定义ID沿现有本机偏好保存，不新增账号、目录或探测RPC。
  合成测试覆盖多字节/组合Unicode/256字节、读回不一致、编辑和断连；
  真实包后置测试另检查实际provider请求，不用准备fixture里的期望值自证模型。
- 同一源码启用Vision自动语言检测，任意截图仍不采用应用UI语言作为强制识别语言。
  原像素小字及英文/简繁混合回归调用生产LocalOCR；两consumer只复制同字节测试和输入图
  到library-only harness，不重建/重签被测App，也不把测试PNG加入产品资源。

原生离屏渲染与合成请求不等同于真人IME/VoiceOver/TCC、屏幕多显示器或账号模型验收。
本轮三系统自动化已通过；主动纯文本粘贴也已单独完成上方验证，完整模型目录等仍属后续范围。

<a id="native-clipboard-worker"></a>

### 剪贴板读取隔离（已包含在当前核验包中）

bf89495的Mac15/14/26均通过实际App worker11、主动粘贴46与关联复制读取7，
原生94方法逐项通过且无旧后台promise警告；详见[当前批次](MACOS_TODO.md#native-product-preferences-checkpoint)。
以下保留此前未完成整包验证的过程：

主动纯文本粘贴的同步AppKit读取现位于同一App可执行文件的独立只读worker中，
且在该进程物理主线程执行；普通应用启动不会进入这个模式。
宿主界面可继续处理取消，等待本次reader退出/reap和所有管道排空后才允许后续操作。
这不表示外部剪贴板提供方被终止，也不声称同步AppKit getter可被线程内抢占。
不增加普通粘贴长度限制，不保存剪贴板正文到临时文件，不自动恢复或重试。
当前0812d47已编译，producer实际11项worker/46粘贴/7fresh全部通过且旧后台promise警告消失；
但同轮字体交互失败使完整App未发布，14/26尚未执行。不能把producer通过当作完整包验收。
后续569bca6再次核验11 worker/46粘贴及全部37关联复制方法通过、无旧警告；
完整run仍被字体滚动和摘要控件测试失败拦截，未生成App，仍无14/26同包验收。

<a id="native-result-position"></a>

### 结果窗口位置（已包含在当前核验包中）

9b55942已实际通过原生11项位置测试、125PNG与完整同包三系统验收；当前8f898aa下载继续包含该功能。
以下保留此前失败和修复过程，不把旧失败当作目前仍被阻断。

Settings → General提供三种结果窗口位置：
- **记住上次位置（默认）**：保留拖动位置并在重开应用后恢复坐标，不覆盖当前窗口大小。
- **屏幕中央**：新结果或召回时，在鼠标所在屏幕的可用区域居中。
- **鼠标附近**：从指针右下方打开，靠边时换侧并收回屏幕可用区域。

显示器移除或分辨率变化后会限制到当前可见区域。位置偏好不主动打开结果窗口，
流式文字到来时也不会追着指针移动。原有非激活浮窗行为保持，不为定位申请新权限。
这是本Mac的界面设置，与模型、业务配置、历史记录和翻译请求无关。
同一请求的完成通知不重新定位；关闭后打开、开始新请求或明确召回才生效。
多屏/Spaces的真实桌面体验仍需后续实机验收，不以纯几何测试替代。
018b9de首轮实际通过新11方法中的10项和全部3张位置设置渲染，真实面板不跳动/移动保存已通过；
发现损坏负尺寸的CGRect标准化问题，已改为验证原始尺寸。同期修正旧摘要测试的动态按钮就绪等待，
全套原生方法仍需新源码重验，尚不切换推荐下载。
后续6e2a341实际通过位置11项和摘要15项；整轮仍被旧剪贴板fixture的发布可见性断言阻断，
该失败发生于生产reader调用之前。9b55942已通过原生755项（23构包前可选skip），
其中位置11项和关联复制原方法均通过，125PNG完整；随后完整App/三系统也通过，
实际watch exit0，完整包字节已独立核验。结果位置继续包含在当前8f898aa下载中。

<a id="native-copy-interval"></a>

### 双击复制间隔（已包含在当前核验包中）

Settings → 划词快捷键与权限新增“间隔（秒）”及“应用间隔”。
默认0.5秒，双击较慢时可输入0.75后明确应用；支持正数，不要求只能选某几个速度。
这是既有`double_press_window`配置，不需要安装/登录CLI或先跑诊断。
它不会自动打开划词快捷键，也不会为设置间隔申请权限或发送翻译。

保存并读回之后才更新“当前双击 ⌘C 间隔”；已经打开的监听不重注册，但会丢弃尚未完成的半对按键或复制。
对已提交的翻译、结果和历史没有影响。第二次按键后仍只有0.5秒的读取有效期。
2026-09-19的浏览器可用性修正将候选counter起点放在本次双击第一键，
以接住第二键通知送达前已经完成的复制；第一键只观察元数据，不读取正文。
第二键确认、相同进程和焦点、新revision及安全检查全部满足后才可读取。
已复制文本可用时无需再等待浏览器AX；否则保留AX及短暂等待新复制路径。
AX返回空值或暂不可用不再一概排除本次复制；普通文本附带的浏览器元数据不读取也不阻断，
文件/图片/敏感或自动生成标记仍拒绝。此修正与结果窗口尺寸修正的独立验证状态见
[当前可用性检查点](MACOS_TODO.md#native-result-copy-usability)，未验证前推荐包不变。
首轮真实Mac已通过复制与窗口缩放回归，但发现紧凑短结果被状态区占位挤出视口；
正在将状态区改为内容自然高度、长提示才滚动，并提升错误文字可读性。原断言及失败图片保留。
失败时保持当前有效间隔和输入，点击“重新读取已保存间隔”核对实际值，不自动再写。
重启时可使用上次确认值的本机缓存而不启动helper；打开业务设置后的实际配置读回仍优先。

可检查：输入0.75并应用、关闭/重开设置后值保留；未开启快捷键时不会偷偷监听；
已开启时较慢的双击可触发，但单次复制、旧剪贴板和超过新鲜度的复制不能触发回退。
后者真实外部应用、系统权限与物理键盘体验仍属于实机验证，不以合成测试冒充。
源码新增25项原生测试和2张渲染。ab83b48已真实编译并通过24项新增方法；
另一项在检查禁用按钮时被测试OCR定位阻断，实际按钮存在，正在修正只读状态断言的定位。
127张PNG完整且中英新图已查看；修正源码仍需重验，尚未宣称新功能完整打包通过。
后续2d68fb6实际通过间隔25项全部新增方法；整轮仍有旧输入上限动态reload定位和
旧剪贴板fixture发布可见性两项失败。正在分别改为异步界面就绪查找和仅测试准备阶段的
有界元数据发布等待；不修改生产读取或其新鲜度，不把单项通过当成整包通过。
上述修正的c758 / [run35330446905](https://github.com/mclight-ship-it/cc-translate/actions/runs/35330446905)
已实际编译46.94秒，780项/23构包前可选skip/0失败/635.846秒；原剪贴板7、
原输入上限26和间隔25方法均通过，127PNG完整。完整App与同包三系统已完成独立验收，
实际watch exit0、3jobs/42steps全通过，当时推荐下载升级为c7581a8；后续3ffb141继续保留该功能。
不需重新安装或登录CLI；此前ab/2d失败作为历史保留，不是当前包仍然失败。

<a id="native-capture-shortcut"></a>

### 截图全局快捷键（新源码待验，尚未包含在推荐下载中）

设置新增“截图快捷键”，显式开启后用`⌘⌥⇧X`从其他应用开始区域截图；
默认为关闭，此Mac记住选择。启用开关只注册键位，不需要CLI、登录、诊断，
也不会因此请求屏幕录制/辅助功能权限。真正开始截图时沿用原有的屏幕录制授权流程。
先按下再松开，进入既有截图及本地OCR预览；只有后续明确点击翻译才发送文字或图片。
当前仍在捕获/选择/识别时不会启动第二次截图；截图菜单与按钮保持可用。

被其他应用占用时显示冲突，在对方释放后明确点击重试；不改用其他快捷键。
关闭此开关不关闭已打开的截图，关闭设置窗口或停止helper也不会改变已启用的快捷键。
如系统未能释放键位，截图动作已停用，界面保留错误和可用的重试；
无法再次释放时退出应用回收系统资源。下一次启动仍按最后明确保存的开关选择恢复。

源码包含默认关闭/持久化、失败与退出、原生双语开关/重试、3张状态渲染，
以及真实Carbon lease和本进程合成事件路由的测试。4fa56e0已实际编译并通过17/18新增方法；
唯一新失败是中文重试按钮的测试OCR定位，英文重试及按钮实际存在均有证据。
另一个旧完整设置截图的中文等待语句可见但OCR未匹配。正在修正测试的原生角色定位与图片读取，
仍需新源码验证；尚无包含截图快捷键的完整绿色包。
1507454仍有这两个测试方法失败；底层控件AX角色实际为unknown，已改为按已识别checkbox的
identity排除后要求唯一动作按钮。中文图片失败另加阶段诊断，不以新增PNG齐全或Windows通过代替Mac通过。
f8a014b已实际通过全部18项新增方法，中英文重试均完成；798项原生测试仅余旧中文设置图片1项失败。
诊断确认是OCR将“结束”读成“纯束”，停止状态本身正确；测试改用绘制时双分辨率bitmap保留小字笔画，
不改变产品OCR、不改期望文案、不删除精确断言，仍等待新源码实际Mac结果。
18db461已通过该停止状态图片及18项新方法；另一幅中文图片因绘制路径被扩大修改而出现OCR差异。
现将双分辨率仅用于目标停止状态图和负对照，其余截图保持原绘制路径；仍未升级推荐包。
3ffb141已实际通过原生798项（23既定构包前skip、零失败）及131PNG检查，
新增18项和先前两个失败方法均通过；实际watch exit0、3jobs/42steps全部success。
完整App及同包15/14/26已独立核验，后续8f898aa下载也包含此快捷键；此批制品与hash见
[完整验收](MACOS_TODO.md#native-capture-shortcut-checkpoint)。
不将合成按键、screen/OCR当成实机外部应用或TCC验证，也不代表完整移植完成。

<a id="native-restore-defaults"></a>

### 恢复默认设置（已包含在当前核验包中）

设置底部提供“恢复默认设置…”：先读取内置共享默认值，再在设置中展开确认区，
与历史条数降低确认使用同一种原生交互。焦点先到取消，Escape可取消并返回恢复按钮。
确认内容使用实际默认值，明确显示历史保存开关、未来写入时的保留条数、词典和粘贴开关；
取消不保存配置，也不改变本机偏好。

确认后只合并Mac已支持的翻译/历史/输入上限/双击间隔/词典/粘贴设置，保留未知及Windows专属字段。
复用现有保存和规范化回读，核对全部目标值后再恢复系统外观/语言、100%文字大小、
记住位置的默认策略，并关闭划词与截图快捷键。纯文本粘贴沿用现有“明确关闭立即停用，
保存后回读确认”的行为；不会自动发送翻译或测试账号。
已有历史不在恢复时删除，词典文件不删除，CLI路径、账号、系统权限与窗口位置记忆保持。
未来新增历史按确认区所示条数修剪；截图快捷键不照搬Windows默认开启的OCR热键。
保存失败/连接中断不重放写入；回读不符不重置本机外观/划词/截图偏好。
快捷键释放失败保留可见状态和原有显式重试，不宣称所有资源都已释放。
操作期间其他窗口后来编辑的翻译方向、模型或数值草稿保持，不被旧保存回包覆盖。

当前修正已通过独立Mac复验、完整包与同包多系统验证；当前推荐包包含此功能。
91773dd已编译，13新方法中11通过；首轮发现正常停止遗漏恢复状态清理，以及测试按钮OCR定位失败。
正常停止现与transport failure一样退休待处理恢复请求；测试复用唯一原生按钮定位。
修正8f898aa / run35341468695已正常通过1850项hook、811原生测试（23既定构包前skip、零失败）、
全部13新方法及134PNG检查；英中真实原生预览/取消/确认/回读均通过。
完整run实际watch exit0、3jobs/42steps成功，完整App与同包15/14/26独立审计通过，
包括扩展后的21项Foundation与默认预览字节不变验证。详见[完整验收](MACOS_TODO.md#native-restore-defaults-checkpoint)。
917已实际watch exit1，
发布门禁阻止归档和App发布，未用其907portable/232process/669core/21Foundation通过替代原生失败。

### 登录项（已进入当前推荐包）

使用Apple的`SMAppService.mainApp`，不写LaunchAgent文件、不把Windows启动配置迁移为Mac许可。
设置底部显示系统实际状态，提供明确的“登录时启动”、刷新、打开系统登录项及移除待批准项。
仅enabled显示已开启；requiresApproval仍显示未开启，重复开启转到系统设置而非重复注册。
关闭等待异步系统回调并回读，失败保留系统状态与安全错误码，不自动重试。
普通启动不读取或注册登录项；打开设置和从系统设置返回可见窗口时才刷新。
恢复翻译默认设置不改变系统登录项。13项新增原生测试及3张图已通过77388c0自己的Mac验证，
测试使用注入的系统服务，不在CI真实注册，不把合成交互当实际重登录或系统批准验收。
同包三系统与完整App核验见[登录项检查点](MACOS_TODO.md#native-login-item-checkpoint)。
在Settings底部开启“登录时启动”；如果系统要求批准，点“打开登录项设置…”由你允许，
返回设置后会刷新实际状态。只有系统enabled才显示已开启；关闭可移除注册或待批准项。
不需要为了此功能改动CLI/账号或恢复默认。实际注销/重登录测试属于可选实机验收，
先保存其他应用中的工作；不能用普通退出再打开冒充系统登录启动。

### Mac版本基础（当前开发包已提供）

Mac营销版本由`macos/Resources/Info.plist`独立维护，不复用Windows版本。
当前包显示名为CC Translate、版本0.1.0、构建150；bundle identifier和现有数据位置保持不变。
新构包命令`python3 tools/macos/bundle.py build --development --build-number 150`
可明确指定构建号；GitHub CI自动传当前workflow的`github.run_number`，不要求用户提供。
同一run重试沿用构建号；本地不传参数时使用Info模板的开发构建号，不据此发布更新。
构建后的Info与source-manifest的application字段保持一致；验证命令不能修改构建号。
新包的关于界面直接显示这些实际值，不额外启动CLI、连接网络或读取账号。
保留当前开发App/zip文件名与非Release标记；已有Sparkle框架和原生入口，但未发布feed或签名更新，
未来正式更新渠道必须维护独立递增序列，不能将本地模板构建或重置的workflow序列当成升级包。
源码4dfb04a / run35374096842已完成独立完整App及同包三系统验证，实际版本/构建与清单一致；
producer与两个消费者的真实About断言通过。8项新增离线契约及bundle/runtime80项、
正常1933项hook、845原生方法和140PNG均已验证；[完整证据](MACOS_TODO.md#native-app-version-checkpoint)已保留。

### Sparkle框架（当前开发包已提供）

后续构包已引入固定Sparkle2.10.0依赖，完整复制官方framework、内部helper和第三方许可，
不修改厂商签名，不分发签名工具/私钥。主App仍为Apple Silicon候选；
framework原有双架构不意味着整个App支持Intel。
便携打包/运行时93项、正常1946项hook已通过；18d6d6d / run35379767359的真实框架加载方法、
846原生项（23构包前可选skip、零失败）及140PNG已通过，实际watch0及3jobs/42steps全部成功。
build147完整App独立审计、原厂商94项逐项比对和同包15/14/26的253process/744core/21后置Foundation通过，
仅有框架的该版本不启动更新会话；详情见[框架审计](MACOS_TODO.md#native-app-version-checkpoint)。
该版本没有更新feed或自动检查/安装入口，不需要用户改CLI、账号或购买开发者资格；
当时未提前替换版本146下载；后续041df14的原生入口及完整App验证已通过，现推荐build150。
继续升级签名与N→N+1数据保护，不把框架加载当作签名更新完成。

### 检查更新入口（当前开发包已提供）

菜单“检查更新…”和设置中的软件更新区域已包含在当前build150中。
有预配置发布渠道时，点击才启动Sparkle并显示标准更新流程；启动、打开设置和恢复默认均不自动检查。
开发包没有发布渠道时会打开说明窗口，提供“查看已验证下载”，由你明确点击后才打开浏览器。
不会显示虚假的“已是最新版”，也不要求设置CLI、账号或自行填更新地址/公钥。
默认不自动检查、下载或发送系统画像；新检查/浏览器错误有明确反馈，不自动重试。
当前开发包未发布更新渠道，说明窗口里的“检查更新”不可用是预期状态，
“查看已验证下载”仍可用；这不影响翻译，也不需要你填更新地址或密钥。
041df14 / run35387156483已实际watch0、3jobs/42steps成功：15项新增方法全部通过，
861原生项/23构包前可选skip/零失败、143PNG、完整App独立审计及同包15/14/26全部通过。
以下保留此前失败历史，已不是当前包仍失败：
9fa9661的Mac编译已通过，但15项新增Swift方法中两项发生按钮定位/渲染失败；
143张图完整不等于通过。已确认三个新图背景全透明，现补系统背景及明暗/不透明回归断言，
保留原交互和正文检查，等待修正源码自己的Mac验证；便携默认值及打包/运行时94项已通过。
11ffa24的真实Mac渲染已确认背景/正文/错误及明暗断言全部通过；只剩英文按钮的测试OCR定位失败。
现仅将定位改为按钮的操作词，不依赖末尾省略号点数，保留唯一原生控件、实际鼠标点击和全部行为断言，
并增加检查不能打开浏览器的断言；随后041df14已实际通过这些交互与完整打包验证。
签名升级、实际重启/取消和数据/权限保留仍需后续独立验证，不把此界面当作完整更新发布。

### Claude服务（当前开发包已提供）

f9781b7 / run35358487419已实际watch exit0、3jobs/42steps全成功；
832原生测试（23构包前可选skip、零失败）和137张PNG通过，
完整App独立审计及同包15/14/26各253process/744core/21后置Foundation通过。
设置中选择Codex或Claude，分别记住CLI路径和未应用模型草稿；服务保存回读确认后才切换连接，
不重放旧翻译。Claude可选Haiku/Sonnet/Opus或输入精确模型ID，不要求先读取模型列表。
现有安装和账号继续使用，不加版本锁定或预先账号探针；测试未调用真实Claude账号或模型。
完整下载见[当前开发包](#native-translation-user-check)；以下为已解决的开发过程记录。

后续Claude已实现独立的一次性Darwin传输、provider及JSONL解释器，支持文本与base64图片，
并复用已有进程组owner；不做版本锁定或预先账号诊断，也不使用会跳过OAuth的bare模式。
provider源码ceae3fb的455目标与1909正常hook通过；
run35348803751 producer已实际通过新9项Darwin provider用例与966portable/249process/728core，
完整同包消费者与App独立审计现已完成，实际watch exit0、全部3jobs/42steps成功。
传输前置6fc首run的811原生回归与134PNG通过，但后续词典真实下载返回invalidResponse；
失败原日志保留，原源码重跑35347450089已实际watch exit0，
完整App与同包三系统审计通过，没有把首轮跳过的process/core当通过。
后续已接通provider绑定的Python/Swift helper会话与业务快照、缓存、历史、图片，
402目标契约与1925正常hook通过；3e53aec的新5Swift连接及4Darwin业务链已实际通过，
run35352153748实际watch exit0、3jobs/42steps全成功，完整App同包三系统独立审计通过。
下一源码继续接入Claude设置入口、两套路径/模型草稿、保存读回后切换helper及两套模型恢复默认，
首UI源码da41955正常hook通过，但首Mac运行因两处新增测试漏传视图回调而编译失败；
已修复测试构造并补Claude错误身份提示，现有16项新原生产品/菜单/渲染测试。
当时UI仍待修正源码Mac验证，推荐包保持8f898aa；现已由上述f9781b7完成验证并替换。
后续250e8c7已通过编译但832原生测试有4个失败方法：服务切换stopped清理遗漏、
菜单测试宿主未观察模型及两个低分辨率OCR错字。已修相应生命周期/宿主，
复用高分辨率渲染并保留全部语义断言；仍须下一源码自己的Mac结果。
ff30204后续已实际通过16项新产品方法及菜单/断连修复，但832项中仍有1个旧摘要中文OCR失败；
继续复用已有分块UI文字识别并加独立负例，而不是放宽原文断言或把832项误报为全通过。
也不代表已使用真实Claude账号或模型验收。
进度与验证边界见[Claude服务检查点](MACOS_TODO.md#native-claude-provider-checkpoint)。

<a id="native-text-scale"></a>

### 原生文字大小（已包含在当前核验包中）

bf89495已实际通过18项相关原生方法、原生渲染和完整同包三系统验收。
以下保留此前字体/编辑/滚动失败与修复的过程，不把这些早期失败当作当前状态。

Settings的通用区域提供90%/100%/125%/150%文字大小；100%保持原有字号。
该设置独立保存为本机展示偏好，离线可用，不依赖翻译服务，也不触碰Windows业务字体配置。
它作用于翻译输入/结果/追加内容、历史预览和详情、截图识别文字；
按钮、菜单、设置说明、状态、来源许可和关于正文保持原样。
结果重排尽量保持同一阅读段落，字号更改不会重新翻译、清空输入或改写已保存历史。
当前已交付16项新增原生测试和3项截图用例；
不把代码断言或旧包的通过结果当成当前编辑/IME/滚动已验。
首轮23cfd5e因新增测试的macOS类型名/检查回调throws声明不正确而编译失败；
已保留日志并只修测试类型和错误传播。
修正的0812d47已实际执行：16项中14项通过、3张150%截图生成并核对，
但切换字号时主编辑器丢失未提交的组合文字，另一项原生选择器测试定位/动作失败。
该轮不能推荐，也不以截图代替输入法交互验收。
新修复采用主输入/OCR共用的稳定AppKit编辑器，保留组合文字并仅更新字号属性；
另补真实原生菜单项选择和OCR连续缩放/提交测试。
569bca6/run35296221920已证实原两项通过、字体18项中16通过；
但主输入/OCR另有两项滚动回归，正补齐文档布局计算并保留更强的阅读位置断言。
该run实际697项Swift（23前置skip）中6方法失败，App发布被拒绝，不把局部修复当成完整通过。

<a id="native-summary-preference"></a>

### 长文自动摘要开关（已包含在当前核验包中）

Settings的翻译区域提供“长文自动摘要”。它控制符合既有分类规则的400字符及以上自然语言长文：
先输出简短摘要，再给完整译文；关闭后直接给出译文。短文、纯代码、截图路径和手动“生成摘要”操作不变。
该偏好可在未选择Codex时经本地配置服务保存，只影响后续请求，不打断正在进行的翻译。
界面以保存后的回读值确认开关；若保存/读回失败，会显示尚未确认，并提供重新读取入口，
不会自动重放写入。bf89495的15项原生方法已全部通过并完成完整App验收。
以下保留早期失败记录：
569bca6实际11模型方法通过，另4原生交互/截图方法在NSButton定位处失败；
116/118PNG只证明现有图片生成，两个确认态摘要图缺失，失败态图对应断言也未通过。
原始失败已保留，正在修定位/交互路径；不绕过原生控件操作，不改变测试发布条件。

<a id="native-input-limit"></a>

### 输入长度与统一预检（已包含在当前核验包中）

bf89495已实际通过26项相关原生方法及完整App验收；下文后续列出的候选失败保留为历史。

Settings的输入长度区域可应用正整数上限，默认5000。它按原始Unicode码点计数，
不是屏幕上看到的字形数：组合标记、空格和换行分别计入。界面保留已保存值与未应用草稿，
应用只改这一项；保存后回读确认，失败时可重新读取，不自动重试写入。未配置CLI也可设置。
合法旧大值不钳位；旧零值或负值保持可见，需明确保存正值纠正。

输入框与OCR编辑器分别显示码点数和UTF-8字节数。主输入、选择、OCR及词典输入
还受既有8192字节协议预算约束；提高字符数不扩大字节预算。超限会提示调整文字或设置，
不截断、不归一化、不发业务请求，也不清除此前结果或把超限误报为词典故障。
保存不会取消或重放已提交请求，配置在helper实际捕获快照时确定；不把排队确认当成捕获完成。
图片翻译和结果操作沿用各自合同，不把文本字符上限错误套用到图片或追加动作上。
实现及26项新增原生方法/2张预期PNG已交付；10项真实后端测试与库存门槛联合237项通过。
新原生代码尚未由自己的Mac源码CI验证，不在当前推荐包内。
首轮76ae1f3的903项便携测试通过，但原生编译在跨模块读取内部预算常量处失败，未执行26项新方法。
修正仅公开既有只读输入预算供App共用，校验与数值不变；仍需修正后的Mac编译和完整交互验证。
bb1f851已实际编译，新增20项偏好/预检方法通过；6项新UI方法及其他9项方法失败，整run未通过。
其中真实OCR编辑暴露规范等价字符串导致不同原始输入未写回的问题，正改为逐UTF-8字节同步，
同时保留码点计数、IME与外部替换回归。设置交互验证改查实际原生控件，不把本地provider getter
冒充完整AX客户端；真人键盘/VoiceOver的验收边界不变。目前没有新的推荐安装包。
后续7f54e7e的正常hook已通过，但真实Mac测试编译发现fixture局部声明重复；正在修正后重验，
不能将Windows检查或App生产目标编译代替744项原生方法执行。
a4ac252已实际执行744项：输入20模型/预检与剪贴板94通过一次，但字体新增等价拼写回归仍超时，
其余设置控件定位与一项中文截图也未通过。正在补齐SwiftUI精确值更新及诊断，
保留编辑器身份、原始输入和全部断言；当前仍没有新包可推荐。
下一源码已整合实际AppKit控件动作/状态/焦点和可见caption定位，替换不成立的provider树假设；
13方法和相关7PNG不变，缺失或歧义仍会失败。该机制验证UI接线，不替代完整AX客户端/VoiceOver验收。
e314fa6已实际验证18字号/精确编辑和94剪贴板方法，新的控件机制也已走通主输入及双语限额保存。
仍有10方法失败，正在修正真实控件渲染就绪等待、公开布局几何与小caption识别，
并保留实际焦点/状态和原有业务断言；不宣称已有完整新包。
95f3078已走通全部4项输入限额原生交互，剩5方法未通过。继续修正SwiftUI控件的真实鼠标分派，
并保留失败截图及OCR诊断；不设置模型/控件state来代替用户操作，不将缺失断言改成skip。
76f01c1的应用事件候选未通过，继续使用既有原生控件tracking模式并保存独立诊断附件；
原始Capture图确认计数实际可见，OCR识别副本可放大，评审PNG和业务断言保持原样。
67f280c实际通过两处OCR和历史失败恢复；持久附件确认摘要尚未产生保存请求。
继续事件队列上下文与真实按钮就绪验证，修复历史草稿相同值重提交误取消确认的问题。
87b4be6历史模型回归通过；交互用例尚被测试事件身份检查阻断，继续按实际队列字段修正，
不把这一测试辅助层失败算成产品验收通过。
243fd22实测证明队列事件编号发生16位转换；空间/时间匹配后摘要及全部输入操作通过，
仅历史确认按钮操作待修，尚没有新的完整App验收结果。
27bc99f已执行英文确认保存/回读/重开，剩中文动态控件查找；改为让出MainActor的异步等待，
不修改业务状态或跳过中文断言，仍待完整原生验证。
5774386的异步等待已通过历史恢复方法，仅剩中文确认按钮的OCR定位失败；
实际按钮存在。测试候选改用唯一可见的原生破坏性按钮属性，并断言取消按钮不具有该角色，
仍执行真实点击及完整保存/回读/重开。后续bf89495已实际通过744项原生测试（23项构包前可选skip，
零失败），英中历史流程完整通过；后续完整App及同包14与26也已通过；当前下载已进一步升级为8f898aa。

<a id="native-history-limit"></a>

### 历史保留条数（已包含在当前核验包中）

Settings新增独立的历史保留条数区域，可明确保存1–10000条；降低时需确认，取消不写入。
保存不立刻删除历史。下次真正新增历史记录时，按当时配置保留最新N条（包含新记录）；
已经在运行的翻译也可能使用新条数。提高条数不会恢复先前删除的记录。
旧配置不被静默钳位，保存后以回读值确认；未知结果提供主动重新读取，不自动重写。

已有历史列表在真正记录新结果后保留显示内容并提示刷新，不自动分页或本地截断。
配置保存不改变同一helper中的历史游标；helper重开仍会使旧游标失效，需从首屏重新读取。
已交付21项原生回归/2张预期截图及7项真实Python存储契约测试，
后端与bundle库存联合236项通过；此增量不在569bca6，原生交互与同包验证尚未完成。
摘要和历史测试已共用真实AppKit可访问性树控件查找/press，避免NSButton-only假设；
保留所有交互和截图断言。编辑器布局修复同时待验，合并718原生方法/120PNG仍需新源码实际执行。
464a9a7已实际执行718项，历史18模型/列表方法及7新Python契约通过；
但7摘要/历史UI、2编辑器滚动及1既有来源清空方法失败，完整run退出1。
实际仅116/120PNG，无App或14/26验收，不能推荐。正补真实窗口呈现/布局与AX诊断，并等待清空的真实渲染完成；
后续CI会提前上传原生unit原日志，完整测试及发布门禁保持不变。
fc089e6的早期原生日志已证实来源清空通过，但其余9方法仍失败。
几何诊断确认AppKit文档frame可有负原点：旧锚点混用了clip与文档坐标，而不是“负数本身非法”。
下一修正按文档可见矩形记录阅读位置、转换后滚动；设置测试也兼容公开的非正式AppKit语义接口。
所有实际交互/段落/选择断言保留，仍待修正源码Mac验证，不提前推广安装包。
45edbfc的实际原生日志已证实18项字体方法全部通过，阅读锚点坐标修复有效；
但7项设置UI方法仍在语义控件定位处失败，116/120截图不等于完整通过，继续修复测试访问方式。

<a id="native-about-licenses"></a>

### 原生关于与第三方许可

此片已经包含在下方同包三系统核验的新开发包中，证据见
[关于/许可检查点](MACOS_TODO.md#native-about-checkpoint)。
这是原生界面、生命周期及真实包资源验证，不冒充真人VoiceOver/Finder验收。

- 应用菜单、菜单栏及Settings中的“关于 CC Translate 与第三方许可”进入同一个原生窗口。
  可以在未配置Codex、未连接helper时单独打开关于，不运行CLI、模型、网络或权限探针。
  构造不读取资源；明确打开后读取包内Info.plist、source-manifest及许可目录。
- 版本、构建号、包标识及来源直接来自随包元数据；缺失不猜测，损坏显示错误并可重读。
  清楚区分清单声明和实时验证，此界面不验证签名、公证、源码真实性或CLI安装状态。
  不擅自为应用自身指定许可；第三方许可只适用于对应组件。
- 文档目录包括THIRD_PARTY_NOTICES以及Python、certifi、词典等实际随包文本；
  Python运行时结构JSON不冒充许可正文。选择文档后才读取全文，保留UTF-8原文、
  CRLF、空白与Markdown字面符号，不走AI/Markdown渲染，不自动打开链接。
  缺失、编码错误、不可读及清单校验不符均显示可恢复状态，不展示替换或截断内容。
- 关于资源任务与翻译/截图分离；旧读取不能覆盖新选择或重开的窗口。
  关闭只清理关于状态，不取消翻译/截图；两个退出回调都取消关于读取，不增加退出等待。
  “复制应用信息”仅响应明确点击，并显示失败；不会自动读取/覆盖剪贴板。
- 默认760×660、最小660×520；中英与系统/浅/深色沿现有模式。元数据、长原文、
  缺失/损坏和窗口生命周期有原生测试，真实包读取另作后置测试，不用前置skip充当验证。
  原TabView的离屏导航黑块已改为原生横向单选控件；七张About图的独立导航区文字断言通过。
  consumer只拷贝同字节App资源读取器与同一个测试到library-only harness，
  不包含产品App目标，也不重建或重签被测试的App；原18个Foundation测试保持独立。

<a id="native-translation-user-check"></a>

### 当前正式原生界面开发包：下载与使用

这是新的SwiftUI/AppKit产品界面包，保留用户已测通的Codex翻译链路，不再要求先跑诊断。
当前补齐Claude完整后端与原生服务选择、两套CLI路径/模型草稿、切换与恢复默认；
已加入系统管理的“登录时启动”及批准/关闭/刷新入口，本包另有独立Mac版本0.1.0/构建150；
新增原生“检查更新”菜单/设置/独立窗口和Sparkle框架，无发布渠道时提供明确的手动下载入口；
保留明确图片发送、稳定词典来源按钮、关联复制回退、剪贴板主线程隔离，
以及字号、长文摘要、历史保留条数、输入上限、结果窗口位置、双击复制间隔、截图全局快捷键和恢复默认设置；保留明确刷新模型目录、设置/主窗口/截图共享选择、
主动纯文本粘贴、
原生设置开关/独占快捷键及本应用Paste and Match Style，
保留自定义模型设置、一次性模型迁移修复和中英混合OCR识别改进，
保留原生关于/第三方许可、区域截图、可编辑预览和明确文字翻译、
全库历史搜索、本地词典下载/开关/删除/来源许可与六种结果动作。
producer执行861个原生测试（838通过、23构包前可选skip、零失败）并保留143张PNG，
同一个App在15.7.9/14.8.9/26.6.2各通过253进程/744核心/21后置Foundation及独立About1；
三个系统的生产Vision4、主动粘贴46、关联复制读取7和实际App只读worker11也实际通过。
完整App已独立字节核验；不将这些测试冒充全局快捷键或真实目标编辑器的人工验收。
旧包的用户翻译正向反馈不是新GUI/TCC、所有CLI版本或账号模型的完整验收。

- 源码：`041df148a6b4a3c45820ea9ae22f8f6953d5cb0b`；版本0.1.0、构建150；
  [run35387156483](https://github.com/mclight-ship-it/cc-translate/actions/runs/35387156483)；
  [完整App下载](https://github.com/mclight-ship-it/cc-translate/actions/runs/35387156483/artifacts/10565210962)；
  [原生离屏截图](https://github.com/mclight-ship-it/cc-translate/actions/runs/35387156483/artifacts/10564982505)。
- 内层`CCTranslateMac-P0.zip`：20,660,910 bytes；
  SHA-256 `072b11ab5dfa4b33a7914ec1934557e87f40c3c2f5d0e6b5f14e17226805a477`。
  artifact保留到2026-09-25T20:04:55Z；过期时只取新的经核验固定run，不使用未知镜像。
- 下载在GitHub Actions页面的Artifacts，名字为`macos-arm64-p0-development-NOT-A-RELEASE`，
  不是另外两份runtime-evidence小报告；网页可能需要登录GitHub，不需要安装Git/gh。
- Apple Silicon、macOS14+候选；Intel未支持承诺。用户不需要Xcode/Python/Git/付费开发者账号。
  本包已有正式翻译、截图文字翻译、结果动作、设置、模型目录、历史、本地词典、纯文本粘贴及关于/许可界面；
  结果位置、双击间隔、截图全局热键开关、恢复默认、完整Claude服务、系统登录项及原生更新入口已包含；P4–P6仍在继续。
  打包脚本没有Developer ID签名/公证/完整bundle seal；不要把Mach-O链接器签名视作发行签名。

**正常使用：**保留已经可用的CLI、路径和账号，不要求重装、降级、重新登录或重跑权限探针。

**本轮可选快速查看：**关于窗口应显示0.1.0/build150；菜单“检查更新…”应显示未发布渠道的说明，
只有点击“查看已验证下载”才打开浏览器。切换中英/深浅色后更新窗口正文和按钮应清晰。
没有已发布feed，因此此包不执行真正的签名升级；无需你为此准备账号、密钥或额外CLI。

1. 从固定artifact取出内层zip；如需核对下载，可用系统`/usr/bin/shasum -a 256`比对上述内层值。
   先退出旧Mac测试App；如“应用程序”里有同名包，停止并自行妥善移开旧测试副本，不覆盖正在运行的包。
   解压内层并从Finder正常打开。仅适用的未识别/未公证提示，可由用户按Apple官方单App
   “系统设置 → 隐私与安全性 → 仍要打开”流程决定；恶意软件、损坏/修改、组织策略或没有该入口时停止。
   不清quarantine、关闭Gatekeeper/SIP、重签或运行包内二进制。
2. 菜单栏显示`CC`，点`Translate… / 翻译…`打开输入/结果双栏窗口。
   打开窗口会连接随包helper、读取设置，但不会调用模型；不需要Start、Enable或先保存一遍设置。
   如需AI翻译而CLI没有自动找到，在Settings选择Codex或Claude及对应的现有可执行文件，之后分别记住。
   没有CLI也可以下载/使用本地词典、打开设置与历史；版本检查不是本地查词前置。
3. 输入文字，选择方向/模式，点`Translate / 翻译`或Cmd+Return。
   已安装并开启词典时，单词本地命中直接显示释义、不调用CLI或模型。
   未命中的正常AI翻译才明确使用所选CLI账号，可能计费；测试时使用无敏感内容的短句。
   支持流式结果、取消、选择文字、复制、复制双语；重新翻译明确不使用缓存。
   结果未知或可能已提交时不自动重放；不要把unknown理解成未执行或未计费。
4. 结果下方`Actions / 结果操作`可精简、正式表达、摘要、解释代码、按文本翻译或指定语言翻译。
   动作是明确的额外模型请求；结果追加到主结果后，不使用翻译缓存、不写历史，取消/失败保留主结果。
   精简/正式/摘要始终取主结果，其他动作取原始请求输入，不取后来编辑的输入或已有追加文本。
   旁边单独的`Retranslate / 重新翻译`是绕过本地词典/缓存的明确模型翻译，替换主结果并遵循历史开关。
5. History可搜索全部已保存记录的原文、译文或日期，按文字/词典/代码/OCR筛选，
   显示匹配总数并对匹配结果分页、复制及复用。无需先“加载更多”才能找到较早记录。
   输入短暂合并后自动搜索，Return立即提交；清空仍需确认并删除全库，不限于当前匹配。
   Settings可保存中英语言、系统/浅/深色、
   默认方向/模式和历史开关，也可选择Codex或Claude并明确输入、应用各自的自定义模型ID；
   “重置草稿”不发送模型请求，保存后可“重新读取已保存设置”确认。
   不需要先加载模型目录；模型本身是否可用由所选CLI和账号决定。
   Codex可选点“刷新模型”读取列表；主窗口和截图预览共享选择，空/失败仍可手填ID。
   Claude可选Haiku/Sonnet/Opus或精确模型ID，不显示没有支持契约的“刷新模型”。
   切换服务须等当前操作完成，保存回读后生效；不会自动重新翻译旧内容。
   历史开关关掉并保存读回后，新翻译不再新增历史。
   通用区域可调字号；翻译区域可开关长文自动摘要、调整输入上限。
   历史保留条数需明确应用；降低时会确认，下次新增记录才按新上限保留，不立即删除。
   设置底部可“恢复默认设置…”；先展示确认内容，取消不改动，确认后保存并回读。
   不会清空历史、删除词典、改变CLI路径/账号/系统权限；不需要为了更新而执行恢复。
   普通关闭窗口不退出，菜单可召回结果。分页时历史变化会提示刷新，不自动重放请求。
6. `Screenshot / 截图`或菜单栏`Screenshot translation… / 截图翻译…`打开区域截图工作流。
   选区后自动本地识别，预览文字可编辑；只有点击`Translate text / 翻译文字`才发送文字。
   截图/本地识别本身不需要CLI或账号，不上传图片，也不要求先打开Diagnostics。
   可选在Settings开启截图快捷键，按下并松开⌘⌥⇧X进入相同流程；默认关闭，开启本身不申请截图权限。
   也可明确点击“发送图片翻译”发送当前选区图片；这是真正的模型图片请求，可能计费，
   图片可能包含敏感内容，发送前自行检查。本地OCR为空或失败不强迫改发文字。
   具体步骤见[明确发送图片翻译](#native-image-translation)。
7. 选区翻译、双Cmd+C为可选工作流，只有使用它们时才检查相关权限。
   Diagnostics是独立次级窗口，不要求为翻译重做AX/OCR/HTTPS探针。
   IME、VoiceOver、Spaces/多屏和TCC可集中补验，不阻止继续开发其他功能。
8. 应用菜单、菜单栏或Settings中的关于入口会打开同一个关于/第三方许可窗口。
   可以查看版本和构建来源、选择并滚动完整许可文本、明确复制应用信息；
   不需要Codex、helper连接或新的系统权限，不会发出模型请求。
9. 需要跨应用去格式粘贴时，在Settings开启纯文本粘贴，再在目标编辑位置按并松开⌥⇧⌘V。
   不使用该功能就保持关闭；无需为了这一更新修改CLI或账号，详细边界见上方说明。

**本轮设置与图片简短检查（可选，不需要改已有CLI路径或账号）：**

1. 调整字号，确认输入、结果、历史详情和OCR预览变化，重开后保持；不应重新发起翻译。
2. 开关长文自动摘要并确认保存读回；短文和手动摘要动作不因此改变。
3. 用无敏感内容调整输入上限，确认字符与UTF-8字节分别计数，超限不自动截断。
4. 历史条数先点取消确认没有改动，再明确降低并保存；不要为测试随意清空真实历史。
5. 用无敏感内容的截图分别尝试“翻译文字”和“发送图片翻译”，确认是两条明确选择的路径。
   模型请求仍使用已有账号，是否发送由用户决定；继续开发不以完成此人工检查为前置。

**模型目录简短检查（可选）：**

1. Settings点击“刷新模型”，检查显示的模型名称和ID；取消/空/失败均应能继续使用原模型或手工ID。
2. 在设置中选模型后正常退出重开，选择应保持；主窗口/截图预览可直接选择并用于下一次明确翻译。
3. 不需要重新安装、登录、降级Codex或重复普通翻译首测；目录不证明账号权限，也不自动发送模型请求。

**纯文本粘贴简短检查（可选）：**

1. 在TextEdit准备带粗体/颜色的合成文字，复制后切到另一个文本编辑位置。
   Settings开启纯文本粘贴，按并松开⌥⇧⌘V；应只插入一份文字，使用目标位置的样式，
   不出现翻译/模型请求。只有系统实际提示时才决定辅助功能授权，不需要重跑全部权限探针。
2. 关闭开关后全局动作应停止；在CC Translate输入框仍可从Edit菜单选择Paste and Match Style。
   正常退出重开，设置应保持已保存选择，不自动粘贴。
3. 可选再试文字中的中文/emoji/制表符/换行，或只复制图片/文件：不应重复插入，
   图片/文件剪贴板不应被该功能清空。跨设备延迟、权限拒绝和多应用差异可集中后续补验，
   不是继续开发的前置，也不要求重测已确认可用的普通翻译。

**关于/许可简短检查（可选，不是继续使用或开发的前置）：**

1. 从菜单打开关于，再从Settings打开，应复用同一个窗口。
   切换“关于 / 第三方许可”，检查当前中英/浅深色下两项文字可读。
2. 在第三方许可页选择一份长文档，滚动到末尾并选择文字；
   Markdown符号应保持字面内容，不自动打开其中的链接。
3. 按Esc或关闭窗口应只关闭关于，不清空原翻译输入/结果或取消截图。
   “复制应用信息”只在点击时写入剪贴板；不必重新测试已经确认可用的普通翻译。

**截图集中测试（无需改动已可用的Codex配置）：**

1. 先隐藏真实文档、聊天和通知，只在TextEdit放两行较大的合成文字：
   `CAPTURE A 12345`与`Please translate this sample.`。
   点主窗口`Screenshot / 截图`，或菜单栏`Screenshot translation… / 截图翻译…`。
   首次按系统提示自行决定屏幕录制授权；拒绝应有提示而不是偷偷继续。
   系统若要求重启应用，正常退出重开再试；不清quarantine或关闭系统保护。
2. 拖出文字区域，或点击两个角确定区域；应出现原截图和自动识别的可编辑文字。
   这个阶段不需要CLI/账号，不应自动翻译或新增历史。
   也可重新开始一次，在选框阶段按Esc或点取消，检查没有迟到预览/翻译恢复。
3. 保留预览，把TextEdit文字改为`CAPTURE B 67890`，再点`Reselect retained frame / 在保留帧上重选`。
   重新框选后预览/OCR仍应来自保留的A帧，不是新B文字；只有`Capture again / 重新截图`才取新帧。
   多屏用户可用无敏感内容的两个测试窗口检查跨屏选区；各屏顺序采样，不保证同时曝光。
4. 在识别文字框中改成`This text was edited before translation.`，选择方向，
   点`Translate text / 翻译文字`或在该窗口按Cmd+Return。
   这一步才使用已有Codex账号，可能计费；结果应对应编辑后的文字，不是原截图。
   先在中文输入法组词时试Cmd+Return，检查未误提交，再完成组词后明确翻译。
5. Settings保存历史开关为关时，不应新增记录；开启并保存后，新的截图文字翻译应出现在History的OCR筛选下。
   本流程不读翻译缓存、不自动摘要、不转本地词典；再次明确翻译会再次请求模型。
   OCR失败可编辑文字或明确重试本地识别；超限文字应完整保留供编辑，不能静默截断。
   最后检查620×600窄窗口、浅/深色、取消与关闭；原生截图测试不代替这些实机操作。

这些步骤是集中实机验收，不是普通翻译、本地查词或后续开发的前置门槛。
不需要提供真实截图、账号材料或原始CLI日志；反馈操作、现象、系统版本和匿名错误码即可。

**本地词典集中测试（无需改动已可用的Codex配置）：**

1. Settings → 离线词典 → 下载词典；约68MB，仅首次下载需要网络。完成后显示已启用，
   词典数据库存于App自己的用户数据目录，不写进应用包；不用Python、命令行或登录模型账号。
2. 回翻译窗口依次输入`hello`、`ran`、`中国`、`中國`；点翻译，检查完整释义、可用读音和来源，
   底部应显示本地词典/没有模型请求。`ran`覆盖词形，`中國`覆盖别名；不要期望每个词都有读音。
3. 复制结果及双语、从历史复用词典结果；本地释义不应显示内部`[[cc-*]]`标记，
   也不应因释义恰好含Markdown字符而丢掉原文。已开启历史时首个新结果记录，缓存命中不重复追加。
4. 断网后重复上述已安装词典查询仍应命中；此时不要使用AI动作或用未收录内容测试“离线AI”。
   本地结果不会自动补充AI，结果操作和重新翻译只有明确点击才请求模型。
5. 关闭“优先使用本地词典”仅禁用；重新启用无需下载。可选测试确认删除后显示未安装，
   不删除翻译历史；下载中取消应恢复可再次下载的状态，不留下半安装词典。

**全库历史集中测试（不需要新增模型请求）：**

1. 打开历史，搜索一条较早保存记录的原文、译文片段或日期；不必先加载较早页。
   如果已有记录不足一页，正常验证搜索/总数即可，不要求人为制造20次模型请求。
2. 切换类型，清除搜索，连续输入后按Return，确认显示最新条件的匹配总数，
   “加载更多”只追加该条件的记录；搜索无匹配时明确显示无匹配，不是读取错误。
3. 选中本地词典与AI历史各一条，检查释义字面内容/AI格式、复制及复用；
   搜索、翻页、复制和复用本身不会调用模型。
4. 搜索过程中关闭历史，再从菜单重开；旧搜索不应覆盖新条件。确认清空是可选破坏性测试，
   会永久删除所有历史，不只是当前筛选内容；不用为了验收删除真实记录。

**失败只回报脱敏摘要：**固定source/run、芯片/系统版本、内层hash MATCH/MISMATCH、
发生步骤、App版本探针显示的数字版本/固定分类、固定错误码、
是否显示submitted/unknown及是否能正常退出重开。
当前无自动诊断导出按钮；不要发送CLI原始日志、环境、账号/认证文件、真实配置/历史、
真实屏幕/剪贴板或个人路径。unknown不等于未计费/未写入，不能据此重放。
仅安装/登录、Finder/TCC及真实模型需要用户操作；已完成的工程链不以付费签名资格为前置。

### 首轮用户 Mac 验证交接（历史固定开发样本；正常打开后约 10–15 分钟）

以下固定样本已取得首轮正向用户报告（范围见下），不是后续源码的验收。
随后 catalog 进程监督、共享规则和路径/原子存储基础已分别完成自动化；
该样本阶段的app-server、完整配置/历史服务和UI尚未接入；后续结果见上方当前检查点。
缺少付费身份不阻断本路线，但首次打开是否成功必须由实机结果确认，不能用 CI 代替。

**固定来源与边界：**

- 只提供 Apple Silicon / arm64 测试包；CI 实际为 macOS 15.7.9、Xcode 16.4。
  macOS 14 仅 deployment target 候选；macOS 26 和 Intel 未验。设备自报不是兼容性证据，
  个人设备/身份信息不写入公开仓库。
- [已通过的 run 34706318638](https://github.com/mclight-ship-it/cc-translate/actions/runs/34706318638)；
  固定源码 SHA `eec92a5794dd9a78ccf91f6f594e0d189e44d4e1`。
- [下载开发 artifact](https://github.com/mclight-ship-it/cc-translate/actions/runs/34706318638/artifacts/10301738307)
  （GitHub 登录后下载，名称 `macos-arm64-p0-development-NOT-A-RELEASE`，
  2026-09-19 16:49 UTC 到期）。外层归档含 `CCTranslateMac-P0.zip`、
  `bundle-audit.json`、`helper-smoke.json`；不是 Release/安装器。
- **仅 fixture/诊断，不是完整翻译产品。** 不登录账号、不发送模型请求，不测试真实翻译能力。
- 构建脚本未对 `.app` 执行开发证书/Developer ID 签名，也未开启并验证 Hardened Runtime、
  公证或 stapling；本轮核验 6 个 Mach-O 均有工具链产生的嵌入式 ad-hoc 代码签名，
  代码页与下载内容一致，但没有完整 bundle 资源签名，不等于 Apple 身份验证。
  没有 Apple 开发者身份或公证保证；校验和匹配也不证明软件无恶意行为，用户仍须判断是否信任来源。
  当前 `.app` 未做完整 bundle ad-hoc 签名；单个二进制的 ad-hoc 不等于 Developer ID 或公证，
  也不是 Personal Team 的设备限期/七天重签模式。用户不需要自己签名。
  **已有正常启动/退出重开的用户报告，但缺少干净用户/quarantine 来源证据，不能宣称完整首开或兼容验收完成。**
  CI 的 XCTest/helper 成功不证明 Finder 能打开，也不证明首次权限可用。

**下载安装（不安装开发工具）：**

1. 仅使用上面的固定 GitHub artifact 链接，核对仓库、run 与源码 SHA；GitHub 可能要求登录免费账号。
   不使用转存站/镜像。解压外层 artifact，先保留内层 `CCTranslateMac-P0.zip`。
   若已过期或不可用，联系协调者重新提供经核验的制品与校验值，不改用未知来源。
2. 用 macOS 自带终端输入 `/usr/bin/shasum -a 256 `（末尾留空格），把内层 zip 拖入窗口，再回车。
   这是只读校验，不需安装 Python/Git/Xcode。比较输出 SHA-256 与下方已发布值；不一致或文件缺失就停止。
   内层 `CCTranslateMac-P0.zip` 大小 **18,351,930 字节**，SHA-256：
   `5443c28e048d93da1551c8627f3292528de239dc0a9f63d3bd5516a6d24c450c`。
   这是内层 App zip 的值，不是 GitHub 外层 artifact zip 的值；只需回报 MATCH/MISMATCH，不发个人路径。
3. 双击内层 zip 解压，把 `CCTranslateMac-P0.app` 移到“应用程序”；已有同名 App 时先停止，不覆盖。
   在 Finder 双击 App，先正常尝试打开，不直接运行包内二进制。
4. 仅当提示属于“无法验证开发者”或“Apple 无法验证是否不含恶意软件”，并且用户已核对来源且愿意承担
   未识别/未公证软件的风险，才由用户本人打开 **系统设置 → 隐私与安全性 → 仍要打开（Open Anyway）**，
   核对是本 App，再在再次出现的提示中确认“打开”。系统会为该 App 保存例外，不是全局关闭 Gatekeeper。
5. 如果明确提示**会损坏电脑/检测到恶意软件、App 损坏或被修改、组织策略禁止**，或没有适用的
   Open Anyway 入口，停止并报告提示类别；不要猜隐藏命令，也不要对这些阻断指导强开。

以上按 [Apple 官方说明](https://support.apple.com/en-us/102445)（2026-05-27 发布，2026-09-13 核对）。
例外只能由用户亲自决定，不由脚本或远程工具批准。绝不清除 quarantine、关闭 Gatekeeper/SIP、
执行 `tccutil reset`、让用户重签下载包或直跑包内程序。首次打开确认与辅助功能/输入监控/屏幕录制
TCC 是不同授权；先完成下面第 1 组，成功后再集中做权限组，不预先授予所有权限。

**正常打开后只做以下五组检查；全部使用新建 TextEdit 中的合成文字，不使用工作文档/真实截图：**

1. **静默与核心**：启动只出现 `CC P0`，不自动弹窗或请求权限。菜单
   `Open P0 input / probes...` → `Bundled core` → `Start bundled helper` →
   `Run synthetic fixture`；默认合成文字应有 SYNTHETIC 标记。先点 `SQLite / SSL / config (offline)`，
   再自愿点 `HTTPS probe (explicit network)`（仅访问固定公共站点，不发送用户内容）。
   两者同时验证包内纯合成 config 子进程及 catalog 临时存储，不调用用户真实 CLI/账号。
   此最小组失败就停止并回报固定错误码，不继续申请 TCC 或要求 CLI 登录。
   关闭面板仍保留菜单，重新打开不能显示上次残留结果。
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
   仅监督本次版本探针及仍在同组内的后代；不测试主动脱离进程组的自定义 wrapper。
   菜单 `Quit CC Translate P0` 后，在活动监视器确认
   本次 App/helper 退出，不按名称结束用户原有 CLI；无 CLI 时记 NOT RUN，不影响其他四组。

**失败信息怎么导出（当前没有自动诊断导出按钮）：**

在 TextEdit 用“格式 → 制作纯文本”，填写以下白名单模板并存为 `CCTranslate-P0-report.txt`，
只把这个文件交给协调者。OS/CPU 可用 `sw_vers -productVersion` 和 `uname -m` 获取；
显示器只填数量/缩放档，不填序列号。实际状态只填固定错误码/状态或简短的合成步骤结果。

```text
Build: eec92a5794dd9a78ccf91f6f594e0d189e44d4e1 / run 34706318638
Route: github-download / per-app-open-anyway / blocked-before-open
Archive SHA256: MATCH / MISMATCH
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

### 首轮匿名用户报告（2026-09-13；仅原始固定包）

全部归属源码 `eec92a5794dd9a78ccf91f6f594e0d189e44d4e1` / run `34706318638` /
artifact `10301738307`，不得转记到后续构建。用户自报 Apple Silicon / macOS 26 候选设备，
尚无独立系统版本佐证；runtime 报告为 arm64/darwin。不保存个人身份、主机路径或原始用户日志。

| 项目 | 用户实际确认 | 未确认边界 |
|---|---|---|
| 启动 | 初以为双击无响应，随后确认顶部 `CC P0`，符合静默启动 | 未观察到 Open Anyway；无干净用户/quarantine 来源证据，不推断所有下载均可直接打开 |
| 离线 runtime | 包内/isolated/禁 bytecode 为 true；Python 3.12.14，SQLite 3.53.1 读写通过，OpenSSL 3.5.8 / bundle CA / 证书验证通过 | 离线 HTTPS not_run 是预期；未单独回报 synthetic-stream |
| 合成存储与配置 | 词典只读/重开/来源、config fixture 方法/路由、catalog cache/reopen 均通过 | catalog 的 CLI 明确仍为 simulated；不证明真实 CLI/账号兼容 |
| AX/焦点 | 授予 Accessibility 后 PRESENT、合成选区正确；面板出现后直接输入仍留在 TextEdit | 授权前 UNKNOWN/拒绝路径未单独确认 |
| 主动 Cmd+C | 双复制显示合成选区、普通粘贴保留；Stop 后不再产生新 B 结果 | 输入监控拒绝及重启细节未报告 |
| 保留截图 | 主屏 A 帧预览后屏幕改 B，OCR 仍为 A；Cancel/clear 图文均清空 | 多屏/Spaces/拒绝路径未验 |
| HTTPS/退出重开 | 用户明确回报 HTTPS passed，Quit 后菜单消失，Finder 重开正常 | 未检查进程树，菜单消失不能证明全部后代回收 |
| CLI/下载校验 | 无 Codex/Claude CLI，版本/账号/模型 NOT RUN；不要求为本轮安装 | 用户未明确回报内层 hash MATCH；既有 CI/下载审计哈希不等于用户侧校验 |

这些是首轮正向实机探针报告，不是完整 P0/P1 或产品验收。免费分发与用户本人选择官方单 App
例外的路线不变；拒绝权限、干净首开、跨版本授权保留、完整平台/焦点矩阵与真实 provider 仍待验证。

### 后续 catalog 检查点制品（仅自动化，不继承上述用户报告）

源码 `a0c2df6fe7fa41d8e6c8034cfdc9303a6636453b`，
[绿色 run 34761449362](https://github.com/mclight-ship-it/cc-translate/actions/runs/34761449362) /
[固定 artifact 10319141756](https://github.com/mclight-ship-it/cc-translate/actions/runs/34761449362/artifacts/10319141756)，
到期 2026-09-20 14:03:44 UTC。内层 `CCTranslateMac-P0.zip`：18,356,322 字节，
SHA-256 `26803ef34f2b07bb55491a570991f615538321eefb21a1ad823f0c7a54a47881`。
这个 hash 仅对应新包；上面首测包及用户证据仍是原始 `eec92a5`，不能混用。

新包仍是 fixture/诊断，没有 Developer ID/公证或完整 bundle seal；不要求用户现在重新安装或安装 CLI。
以后如需测试此包，沿用上面的只读内层校验、Finder 与适用时用户亲自选择官方单 App 例外的流程，
但使用此处的新来源/校验值，并另报新 SHA；不能把旧包的权限/焦点报告当作新包已通过。
新增 `catalog_process_fixture` 在显式离线 runtime 探针中执行包内合成 CLI，不使用用户账号或模型。
真实执行数与资源证据见 TODO；此切片完成后暂停，完整 P0/P1 与 P2–P6 未完成。

### 后续共享规则检查点制品（仅自动化，不要求现在重装）

源码 `fa6a0b87a6d9caa6e6b863cd850060518e5a1d51`，
[绿色 run 34762885485](https://github.com/mclight-ship-it/cc-translate/actions/runs/34762885485) /
[固定 artifact 10318888821](https://github.com/mclight-ship-it/cc-translate/actions/runs/34762885485/artifacts/10318888821)，
到期 2026-09-20 14:33:14 UTC。内层 `CCTranslateMac-P0.zip`：18,357,050 字节，
SHA-256 `a2fec6d9205b44baf858f2d621cf0dbdf4ab9a655285458d26b087bca7474cb8`。
本包仅增加同源缓存签名/history-kind 纯规则，不改变诊断 IPC 或增加真实翻译。
Windows targeted 140/完整 hook 995、Mac 便携 182/包内核心 91（新增规则 9 项）全部通过，
既有 Swift/25 项真进程/后置包内集成与资源审计也通过，详见 TODO。
免费分发和签名状态不变，旧包实机报告不迁移；本轮到此停止新增功能。

### 后续存储基础检查点（仅自动化，不要求现在重装）

源码 `84ab360d61c56875276e73963527721e40c89426`，
[绿色 run 34764132000](https://github.com/mclight-ship-it/cc-translate/actions/runs/34764132000) /
[固定 artifact 10320055600](https://github.com/mclight-ship-it/cc-translate/actions/runs/34764132000/artifacts/10320055600)，
到期 2026-09-20 14:59:39 UTC。内层 `CCTranslateMac-P0.zip`：18,358,978 字节，
SHA-256 `c6266618ae36308d2d7f17252acfc9ad932f7036e0cf48aae0daa875a093ca61`。
Windows targeted 137/完整 hooks 1049、Mac 便携 198/包内核心 107（新增存储 16 项）通过，
实际 bundle 身份下的临时存储诊断、既有 25 项真进程/Swift 后置集成/不可变审计均通过。
原 runtime JSON、业务配置/history schema 和原生 UI 未变；不是完整唯一 writer、迁移或产品。
新包不继承旧包实机报告，免费分发/签名与人工门槛不变，完整证据见 TODO。

### 后续同制品跨系统检查点（仅自动化，不要求现在重装）

源码 `70fe79beee870c74ed1b4e078d98ac4fa89fce74`，
[绿色 run 34765811135](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135)，三个 jobs 的全部 steps success。
唯一 App [artifact 10321110850](https://github.com/mclight-ship-it/cc-translate/actions/runs/34765811135/artifacts/10321110850)，
到期 2026-09-20 15:33:30 UTC。内层 `CCTranslateMac-P0.zip`：18,358,978 字节，
SHA-256 `3a79f5fa2b82a2ec7b936309f2a593fe237f1a863a7d169c90391804b7bccc70`。
producer 实际 macOS 15.7.9/Xcode 16.4；同包在 macOS 14.8.9/26.6.2 原样运行，
各有 25 项真实 synthetic 进程、107 项核心、临时存储、HTTPS/SQLite/取消/EOF 和 1 项强制 Foundation 集成通过。
两个 runtime 的 harness 分别用 Xcode 16.2/26.6，产品不重建。
Windows 正常完整 hooks 1096 项、producer 便携 245 项及原 Swift 测试通过；初次集成 skip 与后置实跑分开记录。
独立核验 664 库存/54 资源 hash/26 Git blobs/6 arm64 Mach-O，三系统内容/模式/链接摘要一致。
本包没有新增业务能力；原始 `eec92a5` 用户正向报告仍独立。
不代表 Finder/干净首开/Gatekeeper/TCC、多屏/IME、用户官方 CLI/账号、Intel 或完整 P0/P1/P2–P6 已完成。

### 历史仓库检查点（历史自动化记录，不要求现在重装）

源码 `c78d8ee994a0d335a1e2c87b51e60c33949d4cc9`，
[绿色 run 34768088072](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072)，三 jobs 全部 steps success。
[唯一 App artifact 10321128934](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072/artifacts/10321128934)
到期 **2026-09-20T16:18:52Z**；内层 `CCTranslateMac-P0.zip` **18,363,013 字节**，
SHA-256 **f44bbfe8428e353c1c3aeefb5a8a8e3c0dab9bc0645ad0a668aeebd64cc1dfcf**。
Mac15.7.9/Xcode16.4 只构建一次，同包在 14.8.9/26.6.2 arm64 原样验证：
每系统 **44 进程测试（新增历史19）/128核心（新增历史21）/1强制 Foundation 集成**，均无 skip/错误。
Windows 生产改动完整 hooks **1165**、联合 targeted **199**；随后仅隔离测试基线修正的 targeted34/hook1 通过。
producer 便携 **299**、普通 XCTest **34 pass + 初次集成skip1**，后置集成真执行。
首次失败及修复完整保留在 TODO，不把 Python3.14 标准库的 fcntl 导入误判或下游未执行当绿色。
独立核验 **667库存/57资源hash/29同源Git blobs/6 arm64 Mach-O/19 runtime许可**；
三系统与下载包的内容/模式/链接摘要一致，临时 zip 已清理。
Windows add/clear 统一锁与 Mac owner 生命周期已完成；这不是配置/历史完整 helper 服务、新UI或全P1完成，
也不继承旧 `eec92a5` 用户报告或验证用户自报26.5.2。免费分发及人工门槛不变。

### 历史无 I/O 配置规则检查点（仅自动化）

源码 **7770b704f05890b60b734a3dc0652f674847c960**，
[run 34801568838](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838)
attempt1 三 jobs/全部 steps success。
[唯一 App artifact 10331488395](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838/artifacts/10331488395)
到期 **2026-09-21T03:11:31Z**；内层 `CCTranslateMac-P0.zip` **18,366,347 字节**，
SHA-256 **d92109ec4d51d90590294034474bdd775099b5cf59ad9b7caeae7b4ebd2a6fc9**。
仍仅 Mac15.7.9/Xcode16.4 构建一次，同包在14.8.9/26.6.2 arm64 不重建/重签运行；
两个 harness 用原选定16.2/26.6。每系统 **44进程 / 147核心 / 1强制Foundation**，
新增19项配置规则逐项实际通过；producer便携318、普通Swift34pass+初次集成skip1。
独立核验 **668库存/58资源hash/30同源Git blobs/6 arm64 Mach-O/19 runtime许可**；
三系统与归档内容/模式/链接摘要相同，既有合成存储/历史、HTTPS/SQLite、cancel/EOF不缩减。
Windows正常完整hook单次 **1194 / 67.382s OK**，但先前targeted **215 / 82个失败断言**
确有WinError5及保留日志的连带失败，不能宣称targeted全绿或写入稳定性已解决，详见TODO。
本次只共享Config/default/纯迁移计划并接原Windows入口，不是Mac配置存储/owner/线程安全或新UI；
不增加任何CLI/模型请求，用户无需现在重装。新包不继承旧包实机结论，正式平台/许可门槛不变。

### 历史配置 owner 服务检查点（仅自动化，当时尚未接业务helper）

源码 **0fd56c2d9f3630d03b078ad62e66e998fbc3419e**，
[run 34803920265](https://github.com/mclight-ship-it/cc-translate/actions/runs/34803920265)
attempt1 三 jobs/全部 steps success。
[唯一 App artifact 10332347322](https://github.com/mclight-ship-it/cc-translate/actions/runs/34803920265/artifacts/10332347322)
到期 **2026-09-21T03:52:53Z**；内层 `CCTranslateMac-P0.zip` **18,370,423 字节**，
SHA-256 **21a37524448e129928540d5b76a32e934fa4e472117a19aecdd087d69a910987**。
产品只由 Mac15.7.9/Xcode16.4 构建，同包在14.8.9/26.6.2 arm64原样运行；
两者仅用16.2/26.6构建测试 harness。每系统 **63进程/183核心/1强制Foundation**，全部0 failure/error/skip。
新增36配置仓库与19配置owner进程逐项通过，原19历史owner/19配置规则名称集合也全部通过。
producer便携369；普通Swift仍34pass+包未构建时skip1，后置真实集成另计，不能混报。
独立核验672库存/62资源hash/34包内源码映射/6 arm64 Mach-O/19 runtime许可，
三系统同一归档/内容/模式/链接，合成配置fixture和所有既有存储/HTTPS/SQLite/cancel/EOF门槛通过。
Windows联合337首次1项旧AST检查失败，冻结原方法后该项通过，原hash不改；
随后单次正常完整hook **1245 / 62.823s / OK**。旧WinError5拒绝来源仍未知，不能据此标解决。
这只完成可调用服务与显式owner，不接业务helper/设置UI，也不解决整个App共享cfg竞争；
旧配置文件迁移/请求快照/provider仍待办，用户旧包实测不绑定本源码。
完整计数、OS/Image/编译器、失败、hash与文档/源码身份分离见
[配置 owner 验收记录](MACOS_TODO.md#config-owner-checkpoint)。

### 历史配置业务与可读性检查点（仅自动化，不要求现在重装）

源码 **c4596526bd7429f76b701228bf35f031f737a3a5** /
[run34809961745](https://github.com/mclight-ship-it/cc-translate/actions/runs/34809961745)，三jobs全部steps success。
正常Windows联合301/32.124s、完整hook1280/69.220s通过；未删旧WinError5失败或改写未知根因。
两个review反例先在旧生产实现真实失败，修复后包内每系统76进程/218核心/5精确Foundation通过；
正常保存/读/重开、明确拒绝后旧盘/锁保持、真实raw迁移预算和关闭时序都有直接测试。
原生启动/诊断面板仍不选择用户配置，不新增设置UI或自动读取用户HOME业务数据。

[唯一App artifact10335050150](https://github.com/mclight-ship-it/cc-translate/actions/runs/34809961745/artifacts/10335050150)，
到期 **2026-09-21T05:34:52Z**，内层 `CCTranslateMac-P0.zip` **18,388,246字节**，
SHA-256 **d661cff7cfe5ea768a4e65a0fec6639b1a027da0ff316ee3e76d05f805316753**。
同一15/Xcode16.4产品在14.8.9/26.6.2原样运行，独立673库存/63资源hash/35源码路径/
6 arm64 Mach-O/19许可通过，前后字节/模式/相对链接不变；临时下载包清理。
完整实际OS/编译器/计数及源码与文档身份见[业务配置验收](MACOS_TODO.md#configuration-ipc-checkpoint)。
这不是完整P1、全App可变配置线程安全或用户实机结论，旧包用户报告不迁移；免费路线及零付费不变。

### 最新历史业务检查点（仅自动化，不要求现在重装）

源码**4021270362418c0876dfd7aa51c4c697694f3758** /
[run34817356816](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816)，
三jobs/33steps全部success，每系统90进程/242核心/9精确Foundation，正常完整Windows hook1306通过。
当前App为[artifact10336766780](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816/artifacts/10336766780)，
到期`2026-09-21T07:22:43Z`；内层18,398,253字节，
SHA-256 `bb607badc1cb5de1c489d9c21431a9304f3d365e08d96375c64448bf7bc71741`。
同包14/26无需重编译或重签；新包的历史/配置API测试只使用临时home，不代表Finder/TCC/GUI已验。
两轮Mac失败、worker跨端review缺陷与修复、实际系统/编译器、完整资源/源码/hash/不可变证据见
[历史业务验收](MACOS_TODO.md#history-ipc-checkpoint)。该历史检查点当时尚未接翻译链/UI；
当前后续结果见[翻译业务检查点](MACOS_TODO.md#translation-ipc-checkpoint)，旧用户包实测身份不变。

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
| P0 平台可行性 | 本文骨架、私有 IPC、权限/AX/热键、Finder CLI、随包 Python、截图/OCR、CI | 可信下载/校验及 Finder 首开（适用时单 App 官方例外）；真 Mac 触发→核心→展示；确定实测 OS/CPU |
| P1 共享核心抽取 | 路径、分类/提示词、缓存历史、provider 生命周期、自有进程组监督 | 无 Tk/Win32；Windows/macOS 规则一致；Windows 回归 |
| P2 原生主流程 | 结果、输入、流式取消、词典翻译解释摘要重译复制 | 所有主流程闭环，不伪装兼容 |
| P3 剩余功能 | OCR/截图、管理许可、历史设置诊断、粘贴主题语言 | 功能矩阵每项有测试或真实验收 |
| P4 分发生命周期 | 免费打包/完整性、登录项、Sparkle 更新、卸载；Developer ID/公证仅可选 | N→N+1，协议/数据/权限一致，重新授权行为实测 |
| P5 加固集中验收 | UI 时序、性能长稳、拒绝权限、多屏/休眠 | 未解决高优故障为零，缺环境不算通过 |
| P6 正式发布 | 安装文档截图、许可证、支持/局限、专属资产 | 用户另行确认后发布，不污染 Windows 通道 |

默认顺序 P0→P1→P2→P3→P4→P5→P6；打包、首次打开和权限可行性提前在 P0 检查。
P0/P1中已验证的依赖允许继续相应P2/P3实现；当前Codex翻译主链已收到用户成功反馈，
不再按最初P0单切片范围冻结正式界面。完整首次权限和发行验收仍单独记录，
代码完成、自动化通过、用户实测和正式发布是不同状态；P6仍需另行发布授权。

性能目标（均非实测）：分类 P95 ≤2ms；常驻 IPC ≤5ms；词典查询+格式化 ≤10ms；
触发到完整词典首屏 ≤150ms。固定样本/冷热分开，LLM 首字与完成交错比较；
记录空闲 CPU、内存、长稳与休眠，不用单次均值替代 P95。

## 9. 集中验收、风险与停止条件

第一轮平台探针：首次权限拒绝/允许、TextEdit/Safari/Chrome/VS Code/Terminal/PDF 选区、
Finder CLI、账号、输入法、跨应用焦点、多屏/Spaces/Secure Input。
第二轮完整候选：全部主流程、安装/登录项/休眠、深浅色双语长内容、浏览器新下载的免费分发包、
N→N+1 更新及权限/资料保留。目标是两轮，不为省次数隐藏故障或取消必要发行验收。

自动化优先覆盖来源按下期间 AI 更新、关闭时迟到结果、拖动时菜单、下载进度更新、
损坏/离线词典、provider EOF/超时/取消。预授权 runner 不代表真实首次 TCC 通过。
报告只包含合成输入和脱敏错误，不上传真实屏幕、认证、历史或机器私有路径。

| 阻断 | 处理 |
|---|---|
| 无可靠选区/权限 | unknown + 快速输入，不读取历史剪贴板冒充选区 |
| 下载包不能正常打开或运行 runtime/CLI | 停止扩展 UI，检查提示类别/完整性/打包边界，不指导强开恶意或损坏提示 |
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
