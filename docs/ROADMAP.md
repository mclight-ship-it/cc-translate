# CC Translate 路线图

本文件仅记录产品方向，不包含本机环境或内部工作记录。

## 正在开发：P2 原生产品界面及后续功能对齐

普通翻译链路已获用户实测通过；现已实现独立翻译窗口/结果浮窗、菜单栏、设置/历史、
中英与系统主题，输入后可直接翻译，诊断不作为使用前置步骤。**移植尚未完成**。
当前界面与六种结果动作源码`4807f62` /
[run35125864397](https://github.com/mclight-ship-it/cc-translate/actions/runs/35125864397)
已通过正常完整hook1606、真实Swift编译、36原模型/8动作模型/6原生渲染、16张截图检查，
同一个App在三系统各193process/504core/15Foundation通过，完整制品已独立字节核验。
动作保留原结果、追加区块，不读翻译缓存或写历史；取消/失败/未知结果不自动重放。
[下载与正常使用](MACOS_DEVELOPMENT.md#native-translation-user-check)不再沿用旧诊断操作步骤。
下一源码已开始本地词典纯核心拆分，再接无Codex的词典首屏/管理和P3剩余功能；可实现部分不等待完整权限矩阵，
也不把一个切片的绿色当成P2–P6全部完成。见[正在实施的清单](MACOS_TODO.md#native-product-ui)。

## 前置检查点：官方 app-server 通知信封兼容已验证

新版测试反馈在模型提交前发生协议错误。已用隔离的官方0.146.0、
仅initialize/initialized/hooks/list复现客户端漏收`emittedAtMs`的问题。
修复官方字段而非关闭严格校验。源码**3ee680a** /
[run35103974280](https://github.com/mclight-ship-it/cc-translate/actions/runs/35103974280)
已通过正常Windows完整hook1573、同包三系统各190process/478core/13Foundation，
完整App独立字节核验完成。官方0.146.0/0.154.0在producer真实native prewarm也通过，
不再仅以版本输出作为协议证据；严格没有thread/turn/账号/模型调用。
见[协议检查点](MACOS_TODO.md#codex-protocol-checkpoint)及
[新包与最小步骤](MACOS_DEVELOPMENT.md#native-translation-user-check)；
不要求用户重装或降级CLI。2026-09-16 本轮交接后用户确认翻译通过、目前测试可用，
主流程成为当前可用基线；后续优先保住主流程并减少使用障碍，扩展GUI/兼容性验证不作为使用门槛。

## 前置检查点：Codex 版本兼容与诊断修复已验证

将精确 `0.146.0` 白名单改为最低稳定版本门槛加实际协议验证，并在原生界面显示
安全解析的 CLI 版本，区分版本过旧、无法识别和协议不兼容。
源码 **`3efebbf`** /
[run34996120967](https://github.com/mclight-ship-it/cc-translate/actions/runs/34996120967)
已通过原独立reviewer、正常Windows完整hook1565及同包三系统各188process/473core/13Foundation。
显式版本探针显示数字版本/最低要求，版本过旧、无法识别、预发布与协议错误分别处理；
可能已提交的协议失败明确禁止重放，默认启动和原生命周期不变。
官方0.146.0/0.154.0二进制在producer实际执行`--version`通过，但没有登录或模型调用。
[当前App及操作步骤](MACOS_DEVELOPMENT.md#native-translation-user-check)已更新，
完整684库存/74资源/46Core源码路径/6Mach-O与同包不可变证据独立核验。
下方 `2b116f0` 是前置包证据，不覆盖此次变化。
用户账号/真实模型与新GUI仍由用户实测，不以降低版本或关闭检查代替修复。

## macOS 原生移植 — 显式 Codex 翻译链已验证，账号与实机待验

- [开发指南、架构、安全边界与 P0–P6](MACOS_DEVELOPMENT.md)
- [独立 TODO 与逐项验收证据](MACOS_TODO.md)

前置源码 **`2b116f0`** /
[run34847149053](https://github.com/mclight-ship-it/cc-translate/actions/runs/34847149053)
已把不可变请求、Darwin native Codex、私有helper/Swift流式API与现有原生显式入口接通。
输入/选区、复制、设置保存、当前history开关及分页/清空已有开发入口；
默认启动不读业务数据/运行CLI，不自动安装、登录或发送模型请求。
同一个App在15.7.9/14.8.9/26.6.2各通过 **185真实合成进程/458核心/13Foundation**，
原覆盖全部保留；31新增Swift unit也实际通过。正常Windows完整hook1542通过。
684库存/74资源/46源码路径/6Mach-O/许可和不可变摘要已独立核验，首轮前置测试错误及修复留证。
**这证明合成端到端工程链，不证明官方CLI/账号/模型可用，也不证明GUI/TCC/IME/多屏。**
下一外部验证是用户自己的兼容CLI/登录、明确同意的一次合成文本模型调用和集中Mac操作，
见[固定新包与最小步骤](MACOS_DEVELOPMENT.md#native-translation-user-check)；
不是购买签名或重装Windows的要求，Claude/完整vision与全部产品能力仍待实现。

SwiftUI/AppKit + 随包 Python 无界面核心；macOS 14+ 候选、Apple Silicon 优先。
现阶段只在隔离开发分支推进；不影响 Windows 发布，不代表已支持 macOS。
2026-09-13 选择 GitHub 免费站外分发，不要求付费 Apple Developer、不上 App Store；
Developer ID/公证仅为未选择的可选增强。可信下载的适用警告可由用户本人按 Apple 官方
单 App“仍要打开”流程确认，不全局关闭系统保护；恶意软件/损坏/组织策略阻断须停止。
原固定包已有匿名用户正向启动/helper/AX/复制/保留截图/HTTPS/退出重开报告；
无干净首开来源证据，权限拒绝矩阵、macOS 26 完整兼容性及 Intel 仍未验。
后续 catalog 真进程监督、缓存签名/history-kind 共享纯规则及跨平台回归已通过；
新包仅自动化，不能继承旧包用户报告。Windows 保留原签名字节、路由/类型优先级与缓存，
显式 Mac 路径与共享原子 JSON 基础也已完成真实 Windows/Mac 回归；
该基础层路径不创建/迁移、当时Mac只运行临时合成诊断，Windows默认与持久化入口保持。
同一个 macOS 15.7.9/Xcode 16.4 制品现已在标准免费 macOS 14.8.9/26.6.2 arm64 CI 运行；
包内核心/真实 synthetic 进程/临时存储/HTTPS/SQLite/取消/EOF 和 Foundation 集成全部通过，
产品未重建或重签，前后内容/模式/链接摘要相同，详细 run/SHA/制品见验收清单。
这不是 GUI/TCC、用户自报 26.5.2 或 Intel 的兼容保证，不能继承旧包用户报告。
共享历史仓库现已接入 Windows load/add/cache/clear，add 与 clear 共用操作锁，旧 schema/缓存/日志与当前历史开关策略不变。
Mac 显式 owner 使用稳定侧文件协作锁，真实包内竞争、replace/clear、关闭/崩溃接管、错误保护在三系统通过；
Mac 坏文件/读取错误不当空历史覆盖。该仓库检查点当时没有历史业务IPC或按钮，也未接触真实用户数据。
Windows 原子替换偶发 WinError 5 的二十轮复核及实际旧/新 writer 对照已复现，拒绝来源仍未知；
错误与根因阻断保留，不加生产重试，不将旧成功证据等同于当前稳定性已解决。
此前切片已把默认/Config 规范化与迁移计划移到无 I/O 共享模块，实际接 Windows Config/load_config，
保持原类型/未知字段/磁盘 payload，只随包合成验证，不新增用户文件/配置 UI。
源码 `7770b70` / [run 34801568838](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838)
同包三系统各147核心（新增19配置规则）/44进程/1强制Foundation通过；正常完整Windows hook1194通过。
该成功不覆盖此前targeted的WinError5失败，稳定性根因仍待诊断；固定制品/hash和所有失败见TODO。
现已完成可调用的 Mac 配置服务：显式路径严格仓库、独立 raw 保存快照，以及与历史共用的
稳定侧文件 owner；真实缺失/迁移/重开/竞争/退出/fork/close/故障保护在同包三系统通过。
源码 `0fd56c2` / [run 34803920265](https://github.com/mclight-ship-it/cc-translate/actions/runs/34803920265)，
每系统183核心（新增36）/63进程（新增19）/1强制Foundation；正常完整Windows hook1245通过。
Windows原转换仍兼容，新Mac服务用同一规则的严格模式；不把坏文件/转换失败当默认配置覆盖。
首次targeted1项旧AST检查失败已保留并修复，旧WinError5根因仍未解决；完整证据见
[配置 owner 检查点](MACOS_TODO.md#config-owner-checkpoint)。
该 owner 检查点当时仅可调用核心/临时合成 fixture。后续切片现已接入私有 helper 的配置
load/save 与 Swift 显式连接 API，源码 `9614eab` /
[run34808290474](https://github.com/mclight-ship-it/cc-translate/actions/runs/34808290474)
同包15/14/26各73进程/211核心/4强制Foundation通过；正常诊断仍零用户配置 I/O。
仅显式启动参数选择 home/实际 bundle ID，配置操作串行持有 owner，started 后取消不谎称回滚，
EOF/shutdown 等待本地操作后释放，响应丢失为结果未知且不重放；不是新的设置 UI。
Swift NaN编码的真实CI异常已修复并验证，失败历史保留。后续初始化路径环错误映射
`4d769e7` 的62项针对性通过，但正常完整Windows hook1274项中2条历史矩阵关联断言失败，
捕获真实WinError5单次replace拒绝；没有重跑凑绿或绕过hooks。当时补充源码和文档仅本地提交，
不把当时未运行的74/212门槛填成通过；这段阻断历史继续保留。
详见[业务链证据与阻断](MACOS_TODO.md#configuration-ipc-checkpoint)，用户无需现在操作。
随后独立review发现compact/缩进文件预算不一致、迁移只验证normalized视图两项完整性缺陷；
旧绿色不最终接受。三个真实反例先失败后修复，保存预判原盘与未来迁移payload，
迁移写前在同owner锁内校验raw payload；保留原缩进字节和wire限制，不加重试。
最终源码 **c459652** /
[run34809961745](https://github.com/mclight-ship-it/cc-translate/actions/runs/34809961745)
三jobs全部steps success：每系统 **76进程/218核心/5精确Foundation**，新增反例均真实执行。
Windows联合301项及这次实质修复后的正常完整hook1280项通过，未跳hooks；旧WinError5根因仍未知。
同包来源/673库存/63资源/35源码路径/许可/不可变已独立核验，最终三文档与源码身份分开。
历史业务helper/Swift完整窄链现已接通：在原显式连接内同时持有配置/历史owner，
支持严格有界revision分页、记录、清空；默认诊断仍零用户配置/历史I/O。
分页包含完整64KiB响应预算，写前验证后续可读性；坏盘不覆盖、queued取消和started写入明确区分，
退出/丢响应不重放，所有测试只用临时home。独立review补充发现worker启动失败的合法
accepted→failed(seq1)曾被Swift误判为OutcomeUnknown；已仅为精确worker_start_failed放行未started终态，
其他错误/序号/重复终态仍严格拒绝，Python原服务行为不改。
最终源码`4021270` / [run34817356816](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816)
三jobs/全部steps成功，每系统90进程/242核心/9精确Foundation真实通过，正常Windows hook1306通过。
新增真实五操作×缺失/旧文件矩阵、Python实际回包跨端消费及HelperConnection重开；
旧dc0ba9c的89/242/8绿灯未覆盖此分支，不算修复证据。
同一个Xcode16.4 App在15.7.9/14.8.9/26.6.2运行，674库存/64资源/36源码路径及不可变摘要独立核对。
两轮真实Mac失败分别是旧测试目录库存未同步双owner、测试将queued取消误作started完成；
均保留并修正精确测试前置，未改生产策略或重试凑绿。全部证据及固定制品/hash见
[历史业务检查点](MACOS_TODO.md#history-ipc-checkpoint)，不继承旧配置或旧用户包结果。
旧Windows WinError5已复现但拒绝来源仍未知，当前成功不表示该稳定性风险已解决。
完整请求快照依赖现已实接Windows主翻译、vision、词典补充、结果追加的派发和执行：
共享不可变配置/ProviderRequest与可变取消/UI分离，历史仍用当前开关/limit/job。
预热必须匹配捕获prompt，原key/cache字节与已提交不新增重试的边界保留。
源码`8797fc7` / [run34823367426](https://github.com/mclight-ship-it/cc-translate/actions/runs/34823367426)
三jobs/33steps成功：正常Windows hook1357通过；同包每系统90进程/277核心/9精确Foundation，
新增35快照方法在三系统各实际执行一次，675库存/65资源/37源码路径与不可变核验通过。
全部真实失败、[制品/hash与限制](MACOS_TODO.md#request-snapshot-checkpoint) 持久化，
源码与后续docs-only身份分离。此处完成的是共享快照依赖与真实Windows消费者，
该快照切片本身尚不是Mac模型执行或完整翻译闭环。
随后 Darwin native Codex 内部后端及三项审查修复已完成自动化：该后端修复源码`6d029d1` /
[run34836719504](https://github.com/mclight-ship-it/cc-translate/actions/runs/34836719504)，
正常Windows hook1494通过；同包15.7.9/14.8.9/26.6.2每系统172进程/410核心/9精确Foundation，
本次新增14方法逐名各执行一次，681库存/71资源/43源码路径及6实际Mach-O独立核验。
复用native app-server而非exec fallback，显式环境/catalog、自有组、流式/取消/timeout/EOF
已用真实合成CLI验证；[两次真实失败、修复和制品证据](MACOS_TODO.md#darwin-native-checkpoint)
均保留。**该后端检查点当时未接翻译helper/Swift API/UI，官方CLI账号/模型未运行**；
此前审查发现idle回收、item终态和非法类型三项生产缺陷，已保留旧反例并修复；
正常联合300项、新源码完整hook/172进程/410核心/9Foundation同包验证均已通过。
旧13b6543绿灯不代替这三项修复证据；后续业务链与原生显式交互已在本节顶部更新，
不将较早后端结果代作新链的通过证据。
用户旧配置迁移/全App配置线程安全仍未完成。
不会自动安装或登录，不宣称整个P0/P1/P2–P6完成；旧WinError5未知风险继续保留。

## 术语表 / 风格预设 — 待办，独立于移植

- 保持单次模型请求，本地匹配原文中实际出现的术语；无命中 prompt 不增长。
- 最长术语优先；英文大小写策略可配、中文精确匹配；不改原文，不匹配代码、URL、路径。
- 每次最多 10 条、约 500 字符；风格只追加短指令，超预算按稳定顺序截断。
- 缓存签名包含实际命中术语及风格，不因完整术语表改变而失效无关缓存。
- 首版固定译法/不翻译；默认、简洁、正式邮件、技术文档；简单本地配置，不做云同步。
- 固定样本 A/B；匹配 P95、模型首字和完成耗时不得明显退化。

## 其他保留待办

- 结果后解释术语 / 继续追问。
- 历史收藏 / Pin，与上限和清理规则一致。
- OCR 结构保留与重选区域。
- 有真实非 CLI 后端需求后再扩展 Provider 接口，不预先扩张抽象。
