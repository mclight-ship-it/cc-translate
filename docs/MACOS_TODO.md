# macOS 实施与验收清单

设计和安全契约：[MACOS_DEVELOPMENT.md](MACOS_DEVELOPMENT.md)。
基线：`148f7a1`；仅独立开发分支。更新日期：2026-09-17。
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

<a id="native-product-ui"></a>

## 当前实施：P2 正式原生界面，不把诊断入口当成完成移植

2026-09-16 用户已确认普通翻译链路成功，同时明确指出产品界面尚未完成，要求继续按计划实施。
此前“正常使用”的交接过早：可用的是后端链路，不是全部Mac产品。当前进入P2，未完成P2–P6。

- [x] 独立原生翻译窗口：输入/结果、方向选择、一键翻译、流式取消、双语复制/重新翻译，
  默认不再进入诊断页，不要求手动Start/Enable/Save一串前置步骤。
- [x] 菜单栏入口、可选择/复制的稳定结果浮窗、结果召回、关闭窗口不退出。
- [x] 独立设置/历史界面：记住CLI选择、自动定位、中英语言/系统深浅色、历史分页追加/复用；
  诊断移到次级独立连接，不干扰已经跑通的翻译。
- [x] 原生模型状态测试及真实SwiftUI渲染截图、同包Mac构建验证，具体源码/制品见下；
  不把HTML示意图或旧后端绿色当成新界面证据；手工键盘/VoiceOver/TCC仍待扩展。
- [x] 六种结果操作追加闭环，保留原结果，不读缓存或写历史；验证见下。
- [x] 本地词典优先首屏/原生下载管理及全库历史搜索已验证；继续P3截图及全部功能矩阵，
  不因本界面切片结束而把整个移植标完成。

当前直接复用已验证的helper/provider，不改模型请求安全性或发布范围。
完整P0权限矩阵、Claude独立后端及旧Windows稳定性追踪仍分别保留，但不冻结独立可做的Codex产品界面。

<a id="native-image-translation-checkpoint"></a>

### 当前图片翻译进展：功能已接线，尚未完成同包三系统验收

源码90228cb已接通原生选区PNG、Swift附件所有权、helper私有副本和真实`localImage`请求；
后续修复取消准备期间的设置回读覆盖，并修正新历史截图测试遗漏语义背景的问题。
说明与操作见[明确发送图片](MACOS_DEVELOPMENT.md#native-image-translation)。

源码`bfd93827784f5533c385eb591d46eba62e9c1e0b` /
[run35255188300](https://github.com/mclight-ship-it/cc-translate/actions/runs/35255188300)
的producer已通过；**整run实际watch仍退出1**，两个消费者仅在旧私有剪贴板测试失败。
正常privacy/full hook1819/95.530s；portable876/14.777s；原生编译35.74s，
598方法中575通过、23项既定构包前可选skip，291.919s。
相对固定26645b9基线新增76、删除0；75项新增非集成方法各通过一次，
新增图片Foundation方法在构包后也实际通过一次，不能借用它的构包前skip。
已独立核验100张本run原生PNG：原84张全保留、新增16张，含窄窗、中英深浅色、取消、
清理失败和仅输出历史；诊断合成图不作为新的原生执行证据。

| 同一App执行系统 | process | core | 构包后Foundation |
|---|---|---|---|
| macOS15.7.9 / Xcode16.4 producer | 231/503.937s | 644/5.003s | 21/179.306s |
| macOS14.8.9 / Xcode16.2 | 231/503.552s | 644/5.842s | 21/191.148s |
| macOS26.6.2 / Xcode26.6 | 231/512.869s | 644/5.478s | 21/186.386s |

表中各阶段零fail/error/skip；每系统新增9项图片process、40项core和图片Foundation方法
已逐AST差分及原始日志核验各通过一次，旧20项Foundation也各通过一次。
两个消费者的About1/Vision4已通过，但**46项剪贴板阶段未通过**，不将表中局部通过写成整包验收成功。
首次完整进程运行的旧“image是不支持任务”假设已修正：
保留无进程/无RPC/无缓存写入断言，覆盖真正不支持的audio、预取消图片和相对路径图片拒绝。

[工程验证App10513607054](https://github.com/mclight-ship-it/cc-translate/actions/runs/35255188300/artifacts/10513607054)
已下载独立审计，**不是新的推荐版或Release**：内层19,351,108 bytes，
SHA-256 `d93ba68614fa3aa8be884de72e70ed671133eb65aed9436212ab6698924b3142`，
tree `7c19f25e27f0a75178266960a7449cb5b37608e18581b4a6505a2f995031f5d1`。
实际690库存/80资源/52源码路径（51唯一）/6个Mach-O；19份Python运行时许可完整，
许可目录总计27个文件（含其他许可、notice和元数据）。
633项固定runtime/许可与已验模型目录包一致，bridge排除已知构建元数据后也一致；
23文件独立Git重建harness SHA
`3e796078f3bc0a63dbdf05ee54455b4908a8b451b5923a045a3355ab413aa30e`
与两份消费者报告匹配。HTTPS证书/随包CA、SQLite、取消/EOF与smoke临时清理检查通过；
消费者最终整体验收仍保持`NOT PASSED`，没有调用真实账号或模型。

当前未勾选完成，也未更换推荐App：旧私有剪贴板fixture在C条目枚举后出现
`badPasteboardItemErr`，包括返回前一条目ID的多次实际诊断，仍在定位。
保留所有旧拒绝、原字节、changeCount、promise和零副作用断言；不靠放宽错误原因或重复运行碰绿灯。
CI现继续收集独立随包检查，但归档、seal、上传之前始终要求原生unit步骤的原始
`outcome == success`；失败、跳过、取消、空值或未知状态均拒绝，producer保持失败，consumer不能启动。
该归档前置已用实际shell验证，03be40a失败run也已证明unit失败时未生成App归档制品；
bfd9382的unit真实通过才生成上述工程包，不能因此忽略随后两个消费者的失败。
下一步仍是修复当前源码并完成整个同包消费者验收，不等待用户重新配置账号。

后续只增加测试诊断的源码`667d17b7b4a07ab23de89789c8930a209797bb8d` /
[run35263101241](https://github.com/mclight-ship-it/cc-translate/actions/runs/35263101241)
实际watch退出1：编译39.22s，598/23构包前skip/2fail（1unexpected）/299.782s，
仅一个旧私有剪贴板fixture构造失败。28个私有名称全不相同，**本次证据否定名称复用假说**：
新资源发布ID4，但reader枚举出属于前一不同资源的ID3；同一reader的名称正确，
查询ID4的flavor成功，查询返回的ID3失败；publisher也正确，第三个独立reference正确枚举ID4。
前后publisher的内存地址复用已记录，但不能据此宣称已证明Apple内部缓存实现或完成修复。
未改生产代码、未自动重试、未放宽断言；原46方法及23处创建/释放行为保持。
正常privacy/full hook1819/95.292s；portable876/18.984s；
随包231process/511.266s、644core/4.677s、21后置Foundation/178.680s均通过。
新增76Swift、9process、40core及全部21Foundation已逐项核验各实际通过一次。
归档前原始unit outcome门槛实际失败，**没有发布该源码App、没有执行消费者**；
仅保留UI artifact10516310562。不以独立阶段通过掩盖整体失败。
并行继续P2词典结果的来源/许可按钮，而非把“来源”误解为切回来源应用；
界面需在追加结果更新时保持按钮和已打开详情的identity，仍未勾选完成。

资源释放顺序候选`918f4979298cd78b91f3ad5e438f9019d8999fae` /
[run35268259169](https://github.com/mclight-ship-it/cc-translate/actions/runs/35268259169)
**实际watch仍退出1，不是修复成功**。fixture先退休本测试的C发布者，再请求全局释放；
新增HTML前驱场景复用原方法，仍检查真实ID/flavor/字节及零副作用，不做进程级引用保留。
producer编译46.32s，598/23构包前skip/0fail/354.106s；46项粘贴通过。
但两个独立消费者各4fail（2unexpected）：14的HTML与file发布、26的新增HTML前驱与
替换owner的promise仍返回前一资源ID。相同reader查询新ID的flavor成功、第三个reference
枚举正确；这证明释放顺序修正不足，不能把producer绿色当成根因已解。
正常hook1823/95.175s包含当时尚未提交的独立来源功能4项Python测试，不声称是隔离918源码hook。
同App三系统231process/644core/21后置Foundation均通过：
producer 525.630s/4.936s；14为515.928s/6.458s/193.703s；
26为501.217s/7.294s/193.906s。新增76Swift/9process/40core及全部21Foundation
逐项实际通过，100张原生PNG全部CRC核验并查看本源码窄窗/仅输出历史截图。
App10518163727独立审计：19,351,112 bytes，
SHA-256 `06102c1b8fe54bfc01b082be06e10d880bfd1d8956eceabc810b60086a27f6c2`，
tree `b87b0706154feedc3ef8cf947cec2984ad7fdaaa4e5e8aca485e37d91adecda0`，
690库存/80资源/52源码路径（51唯一）/6实际Mach-O；
23文件harness `1caf0549ea825de5573a216b36a801b662c297d072f2b2524f02247d58405049`
与两个消费者相符。报告保持`NOT PASSED/plain-text-paste-harness`，
未执行失败之后的最终消费者不可变门槛；不替换推荐包。

后续改用AppKit条目读取，源码`dbf5b33eae16865a3deadd8e2fd4a17407b9a152` /
[run35275212199](https://github.com/mclight-ship-it/cc-translate/actions/runs/35275212199)。
不再让产品依赖已观察到跨资源不一致的`PasteboardGetItemIdentifier`：
单次交互在原有串行队列取得AppKit items/types/data，所有条目先检查文件类型，
之后才请求文字或RTF；严格解码、changeCount、取消、一次性lease及不恢复/不重试保持。
测试明确替换的是C枚举API本身的符合性断言，不删除46个产品方法：
已知发布ID仍由C验证flavor/flags/精确字节，真实C promise回调仍核对opaque ID；
产品读取之后才检查AppKit条目数量、顺序及测试专用eager identity的精确字节，
避免预先读取AppKit为被测读取准备缓存。identity仅存在于私有测试资源，不写用户剪贴板。
这不是已证明Apple内部机制或已修复系统缓存。父已审阅两个文件、核验方法清单无增删，
正常privacy/完整hook1823/95.502s通过，推送时远端精确一致且工作树干净。
**更正先前状态：API步骤的success是continue-on-error转换后的conclusion，不是原始测试通过。**
本run实际watch退出1；编译43.68s，616项/23构包前skip/1fail/369.102s。
46项粘贴45通过，唯一失败为UTF-16无损解码：AppKit同时暴露自动转换的UTF-8别名，
原优先级选择了它，使UTF-16 BOM变成文字开头的U+FEFF。C已知ID原始字节、
AppKit原UTF-16字节均精确正确；本次没有C枚举器失败，但迁移仍未通过验收。
18项Sources、76项图片方法及21后置Foundation逐项通过；
portable880/14.270s、232process/517.993s、648core/6.562s、Foundation21/184.681s通过。
原始outcome门槛实际拒绝归档，没有App、消费者未运行。112张本源码PNG已核验并直接查看关键图。
仅三文档提交aa5e164也因现有push触发器自动运行CI35276127888，实际watch1，
再次出现同一UTF-16失败；不是新的修复源码，也不能声称文档推送没有触发云CI。

修正源码`237f3c9df85ae98f140f5da32359195348ae3110` /
[run35277832576](https://github.com/mclight-ship-it/cc-translate/actions/runs/35277832576)
优先读取并解码UTF-16本身，外部格式仍先于可能改变换行的native别名；
不对UTF-8结果任意删除U+FEFF。原4个编码矩阵场景和46个方法保持，新增4个
literal-prefix/TSV场景，并检查最终发布UTF-8的精确字节。
新增literal-prefix场景使用原始字节而非会消费前缀的AppKit string便捷转换作为判据，
同时仍检查纯文本类型；原4场景的所有断言保留。最终验证仍失败，见下方实际结果。
正常push首次被未改动的Windows历史矩阵测试挡住：1823/95.325s、仅日志存在断言失败；
本日志未记录具体拒绝原因，不能直接归因为WinError5。独立该方法1/3.022s通过后，
正常privacy/完整hook1823/88.733s通过并推送，未绕过hook，也不把重试称为Windows稳定性修复。
并行双Cmd+C切片的未提交文件与此源码分开，不宣称共享工作树干净。

237本轮实际watch退出1、attempt1：producer和同App macOS14完整通过，
macOS26仅粘贴阶段失败。producer编译49.99s，616项/23构包前skip/0fail/286.254s，
46粘贴/0fail/0.501s；18项Sources及76项图片方法逐项通过。
同App三系统232process/648core/21后置Foundation全部通过，新增图片9process/40core、
来源1process/4core及全部21Foundation逐项各通过一次：
15为521.951s/5.693s/186.573s，14为527.800s/5.313s/185.364s，
26为525.768s/6.624s/204.307s。
14的46粘贴/0fail/0.745s、最终`passed/complete`与不可变门槛已核验；
26的46项中两个方法共3fail：不可用C promise回调收到0xd、预期0x13；
新增UTF-8 literal-prefix场景丢失U+FEFF，读取和写入字节断言都失败。
**进一步核对原始日志后的归因更正：**本case5的AppKit types只有UTF-8和测试identity，
实际copy完整25字节，不是选择了UTF-16别名；丢失发生在Foundation的UTF-8 String初始化。
固定编码优先级也无法修复这一解码问题，不删除新增用例或放宽promise身份断言。
26报告保持`NOT PASSED/plain-text-paste-harness`；finally记录`bundle_unchanged=true`，
但失败之后的完整audit-after与最终成功门槛没有执行，不能混为一谈。
原Support实现者继续处理这两个文件；并行P2新复制读取也需复用一致的原始表示解码。

本源码完整工程App10522635034已独立审计，仍不作为推荐版：
19,376,266 bytes，SHA-256 `289b187a26174143ec5ed7488ab4572e464a9e00404691876ce4df3449140a3b`，
tree `c435ee3b436be66a8a987fbd9365e334a048f3caa1f098ffc44054cdb59ede14`，
690库存/80资源/52源码路径（51唯一）/6实际Mach-O/19 runtime许可；
23文件harness `92642eb8554caab4270268cea1f9cb87323993b9ad9ad16984865d88f5e06a5d`。
112张本源码PNG的完整清单、尺寸及CRC已核验，并直接查看来源按钮和仅输出历史。
14/26小报告artifact分别10521688368/10522437488，原始失败日志与独立审计均保留。

后续实现改为按每个item自己的广告顺序选纯文本表示，避免先取转换别名；
UTF-8用Swift解码再逐字节回验，保留literal U+FEFF并拒绝非法序列，不接受替换字符修复。
这份选择/解码逻辑已抽为共享`PasteboardTextRepresentation`，由主动粘贴及新复制回退共用。
7个延迟数据场景改用真实AppKit provider，严格检查回调对象身份、board名称、类型和次数；
保留取消排空/换owner/混合文件先拦截/原字节，独立C eager字节oracle仍在。
这是明确替换C promise桥接器的符合性oracle，不声称修好Apple内部机制。
原46方法及已有断言保留，编码矩阵8→11、非法UTF-8矩阵1→6，并增加相反表示顺序的双item场景；
新源码尚待实际Mac编译和执行。下次同App消费者也将运行7项新复制读取测试，不只在producer运行。

上述接线已提交为`c7745ec924d057c9a1cfaf8dae7772ebd8950d9d`，
[run35284938518](https://github.com/mclight-ship-it/cc-translate/actions/runs/35284938518)
实际watch退出1：producer和同App macOS14完整通过，macOS26被后台promise线程警告检查拦截。
**这次不是功能断言失败：**三个系统的46项纯文本粘贴及7项新复制读取都各自实际通过，
UTF-8前缀/多编码/真实provider回调身份没有再出现此前失败；但15和26各记录4条
`NSPasteboard: synchronous promise fulfillment requested from a background thread`。
14没有该警告、报告`passed/complete`；26报告`NOT PASSED/plain-text-paste-harness`，
finally的`bundle_unchanged=true`仍不代表完成后置审计。继续核实并修正AppKit线程用法，
不通过删除警告检查或让主界面等待阻塞provider来冒充解决。

producer编译53.03s，653项Swift/23构包前skip/0fail/347.912s；37项新P2与18项来源方法
逐一核对本次原始日志，start/pass均各一次。portable882/16.627s；
三系统process/core/Foundation为：

| 系统 | process | core | Foundation |
|---|---:|---:|---:|
| 15 | 232/521.880s | 648/5.461s | 21/189.444s |
| 14 | 232/495.082s | 648/5.204s | 21/176.658s |
| 26 | 232/534.176s | 648/5.065s | 21/193.642s |

46项粘贴耗时15/14/26为0.390/0.191/0.611s，7项新读取为0.054/0.055/0.068s，均零失败。
正常privacy/full hook1825/95.034s通过，没有借重试声称修复旧Windows间歇访问拒绝。
完整工程App artifact10524287554已独立核验：19,404,250 bytes，
archive `288ce7589c31ef8efdf81faffbf3d13c132b2a0d92f8c7881aaad6a5076f23e3`，
tree `4a4615a9b15d2cc2f0bf22615c5610fe2454c17aebe814f65d3aa28c7b1692d5`；
690库存/80资源/52源码路径（51唯一）/6实际Mach-O/19许可，27文件同源harness。
112张本源码PNG清单/尺寸/CRC通过，已直接查看中文来源弹窗及仅输出历史。
14/26小报告artifact10524398847/10525096426及原始日志保留，**未推广为推荐包**。

<a id="native-dictionary-sources-checkpoint"></a>

### 词典来源与许可按钮：18项新增原生测试通过，完整新包仍待验收

源码`420928b0865bab9207991f7069670fc2944aac26` /
[run35271286020](https://github.com/mclight-ship-it/cc-translate/actions/runs/35271286020)
接通共享`source_details`、有界helper响应、类型化来源和原生按钮/弹窗。
命中缓存仍取当前查询的结构化来源；复制文本、历史内容和缓存签名保持原样，
旧历史或模型结果不从文字猜测来源。说明见[来源与许可](MACOS_DEVELOPMENT.md#native-dictionary-sources)。
集成时发现一个新流式/取消测试被误嵌套于另一测试函数，已在首次CI前移至XCTest类成员，
并以输出发布expectation替代固定80ms等待。静态清单新增18Swift、4core、1process，删除0；
本源码预期616Swift/232process/648core/21Foundation/46粘贴与112张PNG，均需实际日志核验。
针对性110/7.400s通过；正常privacy、5文件compile及完整hook1823/95.083s通过，
推送时远端精确一致、工作树干净。

420首轮实际watch退出1：编译46.24s，616项/23构包前skip/6fail（2unexpected）/310.321s。
18项新增方法均实际发现，其中17通过；changed-source交互方法在第三次发布后
过早假设SwiftUI已更新原生弹窗，两个断言失败，另4fail来自旧C剪贴板fixture。
新流式/取消方法、真实mouseDown→异步更新→mouseUp、同来源的语言/主题/选择保留、
Escape/关闭后焦点返回均实际通过。112张PNG已逐张CRC/数量/尺寸核验，原100张保留、
新12张精确匹配；已直接查看本源码的中文暗色结果及英文浅色来源弹窗。
232process/532.947s、648core/5.079s和21后置Foundation/182.555s通过，
新增4core/1process及21Foundation逐项各通过一次。原始unit门槛拒绝归档，没有App/消费者。

仅测试时序修正`62a92194d495b4406f3c3778d6e67cde68a54a0d` /
[run35273880271](https://github.com/mclight-ship-it/cc-translate/actions/runs/35273880271)
改为等待真实NSTextStorage编辑通知含新来源，再检查关闭/重新打开；
保留所有旧断言并增加同一按钮/精确来源检查，不改生产代码。
正常privacy/完整hook1823/94.973s通过。实际编译49.63s，
**18项新增Sources方法逐项真正start/pass各一次**，原唯一来源失败方法0.646s通过；
全部616项/23构包前skip/2fail（1unexpected）/390.502s。
仅旧备用图片promise fixture仍发布19返回18，随后`-25132`，所以整run实际watch仍退出1。
portable880/16.089s、232process/483.295s、648core/4.867s及21后置Foundation/179.406s通过；
新增4core/1process和全部21Foundation逐项各通过一次。
API核验attempt1/source精确相符，原始unit门槛失败、没有App、消费者跳过；
UI artifact10520356031保留，不能将它说成安装包。
上方dbf5b33及237f3c9的实际迁移失败仍在修复；当前推荐下载不变，不把局部通过当完整移植完成。

<a id="native-associated-copy-checkpoint"></a>

### 双Cmd+C关联复制回退：共享解码已接通，待原生验收

现有明确开关和被动监听接通关联状态机；AX仍优先，仅unsupported时尝试同一前台
进程生命周期/焦点、双键时间与新changeCount关联的文字。不吞原Cmd+C、不模拟copy、
不写哨兵、不读取旧剪贴板、不恢复/重放。诊断启动明确AX-only，默认构造不监听或读业务状态；
输入/来源变化、停止、取消、新请求、关闭和退出使待处理结果失效。
时间及changeCount只是保守关联，不是写入者PID证明、跨进程原子CAS或Universal Clipboard验收。
第二键采样前已完成的复制宁可不采用；同步系统读取不能承诺可抢占。
原实现者交付10文件、37项新XCTest（15状态机/8监听/7私有剪贴板/7App），全部旧方法保留，
静态原生库存616→653。父完整阅读交付，原离线包/runtime回归66/11.133s通过；
接入7项同包consumer读取验证及2项Python证据解析回归后，68/12.394s通过。
Windows编辑器没有发现可执行native测试，不称为Mac编译通过。
这份实现不在237的App中；现在和主动纯文本粘贴共用表示选择/解码，
支持UTF-8/UTF-16/TSV/Mac Roman，不再限定仅UTF-8。最终UTF-8仍最多8192字节，
原始预算16386字节包含UTF-16的BOM，带/不带BOM均允许8192个ASCII字符。
7个原读取方法扩展原字节、编码、相反表示顺序、非法序列和边界回归，库存不再增加。
上述c7745ec已实际编译并执行37项新增方法；两个消费者的7项私有剪贴板读取均通过。
15状态机/8监听/7App方法的证据来自producer，不冒充三系统全部重复执行。
完整包仍受主动粘贴的线程警告问题阻挡；不是全局快捷键/TCC真人验收。

<a id="native-clipboard-worker-checkpoint"></a>

### 剪贴板主线程隔离读取：实现已合并，待本源码 Mac 验证

保留c7745ec的失败证据及后台promise警告检查，不再用宿主后台队列调用AppKit同步数据读取。
现复用同一App可执行文件的早期只读worker入口，在创建NSApplication/模型/helper前分流；
worker在自己的物理主线程枚举/读取/转换，宿主异步接收，取消或超时只清理本次owned进程组。
确认wait/reap、标准管道EOF及输入/错误输出排空后才释放操作；不宣称外部provider也已停止。
不新增shipping helper、普通粘贴字符限制、正文临时文件、自动重试或剪贴板恢复。

7个真实promise fixture改为独立测试producer，其主线程履约，阻塞时宿主仍可处理心跳和取消；
原46项粘贴方法保留，新增11项协议/实际App入口/大正文/超时与回收测试。
消费者复制同源码fixture和新测试，单独构建测试producer，不重编或重签产品App；
`CC_TRANSLATE_APP`指定审计后App，无产品路径fallback。11方法新增独立报告/零skip检查，
继续保留46粘贴、7fresh-copy、21Foundation及完整前后不可变审计。
父已完成交付审阅；本地bundle/runtime联合72项/11.973秒通过。
Windows无AppKit执行能力；新增worker、字体16项与3PNG仍待这份合并源码的Mac CI。
推荐包不变，不能把源码接线或c7745ec旧方法通过写成这份App已经通过。

首次源码`23cfd5e`的[run35292610415](https://github.com/mclight-ship-it/cc-translate/actions/runs/35292610415)
已实际退出1：886便携测试通过（20.218秒），新增字体测试有两处编译错误，原生测试未执行。
错误分别是把macOS编辑掩码写成UIKit式嵌套类型，以及渲染检查回调不允许抛出断言辅助错误。
现改为`NSTextStorageEditActions`，并让既有render辅助器传播throwing inspect；不删任何断言或方法，
不改变产品代码、不放宽发布门槛。必须用修正源码重新执行Mac CI，首轮原日志已保留。

<a id="native-text-scale-checkpoint"></a>

### P3 原生文字大小：接线已交付，待新源码原生验证

通用设置新增90%/100%/125%/150%文字大小，默认100%保留各处原有字号，
不改变最小窗口尺寸，也不放大菜单、按钮、状态和说明文字。
原生`nativeTextScale`偏好与语言/外观走相同的本地保存/重开路径；
无需helper、CLI或账号，不修改共享Windows配置`font_size`或其默认值12。
主输入/结果/浮窗/追加内容、历史预览与原文译文、截图OCR编辑均已接线。
结果仅字号变化时原位修改字体属性，并按阅读位置重新定位；不重写原文、不发送新请求。

交付6个文件、16项新XCTest（4偏好模型/9原生交互与渲染/3截图），父已完整阅读；
包含实际原生字体、选择/插入点/撤销/组合输入/滚动、流式追加以及离线控件的断言。
预期新增3张150% PNG：英文浅色翻译、中文深色历史、中文浅色OCR，检查可见文字和操作。
当前没有这些新PNG，也未实际编译这16项；不能引用c7745ec旧测试证明它们通过。
需随下一份完整源码运行Mac CI，仍不代替真实IME、VoiceOver或完整P3验收。

首轮源码`59c2ab6`/[run35116345396](https://github.com/mclight-ship-it/cc-translate/actions/runs/35116345396)
已实际编译全部原生界面和新XCTest（31.94秒），5项真实视图渲染通过；
36项模型测试中1项抓到准备期间的新模型选择被旧配置回读覆盖，其余35项通过。
完整Swift结果为154项、13项既定构包前可选skip、1failure，**首轮没有构建App，不算绿色交付**。
修复保留点击快照和后续编辑的区别，并扩展同一断言覆盖方向回到旧默认值的情况；
截图另存独立artifact，即使后续测试失败也可检查，不降低测试/构包条件。

修复源码`92bf5eb`/[run35117011234](https://github.com/mclight-ship-it/cc-translate/actions/runs/35117011234)
的Swift步骤已通过，原生截图artifact`10454599431`包含14张实际SwiftUI/AppKit合成数据渲染。
人工查看发现浅色未激活窗口的突出式Translate按钮白底白字；改为始终可读的系统按钮，
并用现有本地OCR验证截图底部确实可读出Translate，不只验证PNG不是空白。
主窗口同时改成均衡双栏，最小尺寸660×540也纳入截图；这项后续源码改动不能借用92bf5eb绿灯。

后续`7d5accd`/[run35118179544](https://github.com/mclight-ship-it/cc-translate/actions/runs/35118179544)
实际编译通过（34.02秒），36项模型全通过，但新增可读性断言真实失败，保留了失败截图。
检查确认仅换buttonStyle不足：SwiftUI的Return快捷键仍让按钮进入默认动作配色。
Cmd+Return改走标准AppKit菜单命令，按钮不再被SwiftUI隐式当作默认Return动作；
菜单只在输入窗口可提交且IME不在组词时启用，原可读性断言继续保留。

**正式界面切片已验证：**源码`64d80a0a93b8df9eb22299c5d1b52b9cd1a98f88` /
[run35118820999](https://github.com/mclight-ship-it/cc-translate/actions/runs/35118820999)，
实际watch退出0，API核对attempt1、3jobs/35steps全部success。
正常privacy/full hook1573/80.780s；producer portable676/10.603s；
全部Swift编译36.95s，154项中141通过、13项既定构包前可选skip，
其中新36项产品模型及5项原生渲染均通过。14张真实合成数据PNG中，
浅色/深色主界面及660×540最小尺寸已实际查看；浅色Translate可读性OCR断言通过。
这不是HTML示意图，也不冒充真人IME/键盘/VoiceOver、Finder/TCC或真实账号模型验证。

| 同一App的执行系统 | 真实合成进程 | 核心 | 构包后Foundation |
|---|---|---|---|
| 15.7.9 / Xcode16.4 producer | 190，零fail/error/skip | 478，storage fixture通过 | 13，零fail/skip |
| 14.8.9 / Xcode16.2 | 190，零fail/error/skip | 478，storage fixture通过 | 同13方法，零fail/skip |
| 26.6.2 / Xcode26.6 | 190，零fail/error/skip | 478，storage fixture通过 | 同13方法，零fail/skip |

[完整App artifact10456888448](https://github.com/mclight-ship-it/cc-translate/actions/runs/35118820999/artifacts/10456888448)
已独立读取内层archive并比对Git源码字节、资源、许可及Mach-O头：
18,699,652 bytes；SHA-256
`ad375c545ce0d192130877f82c90d70e5af8ea68061f4973103f343ecba055b5`；
tree `04f35ac58c05b058af5a02f81c99df2da7397b097d9981af2c30b8e7d8a5ed48`；
684库存/74资源/46Core路径（45唯一）/6实际arm64 Mach-O/19运行时许可/14 required覆盖。
605个保留Python普通文件与另加的自有process-support dylib分别计数，不混为上游运行时。
三系统archive/tree相同、包未修改，HTTPS证书/SQLite/取消/EOF/临时清理报告均通过。
官方0.146.0/0.154.0预热仍只调用initialize/initialized/hooks/list，无账号/模型调用。
[14张原生截图](https://github.com/mclight-ship-it/cc-translate/actions/runs/35118820999/artifacts/10456432367)
另存独立制品。没有新增独立reviewer签收；实现/测试代理不冒充独立审查。

上述64d80a0是前置界面包，**不包含结果动作**；下方4807f62已另行完成动作接线与验证。
词典优先首屏、完整动作、截图翻译、历史全库搜索及其余功能继续按P2/P3推进，
不在这个界面检查点停止开发。新包正常使用步骤见[开发指南](MACOS_DEVELOPMENT.md#native-translation-user-check)。

<a id="native-model-catalog-checkpoint"></a>

## 模型目录检查点：同包三系统已通过，继续图片翻译

已接通设置中的“刷新模型”、主翻译/截图共享模型选择，以及 helper 到 Darwin Codex
受监督的 `debug models` 读取。打开设置不查询目录；刷新不会发送模型请求、验证账号权限、
改写 Codex 配置或自动保存模型。空目录是正常结果；失败仍可用 Fast/Default 或手填 ID。
模型名称/说明允许缺失和上游新增字段；ID 保留原始字节，不做大小写或 Unicode 归一化。

本轮源码 `d637beabc61fff9fa1af093217feed9567361293` /
[run35229459129](https://github.com/mclight-ship-it/cc-translate/actions/runs/35229459129)
**实际watch退出0，attempt1的3jobs/40steps全部success**，现已替换推荐开发包。
正常 Windows privacy/full hook **1778 / 106.048s，OK**；原生源码库存为522方法
（较纯粘贴增加53、未删除旧方法）、20后置Foundation、222process/604core，
并已逐方法核对新增52个非集成方法、1个新增Foundation、8process/19core均实际执行通过。

| 实际环境 | 本轮结果 |
|---|---|
| macOS15.7.9 producer | 便携836 / 14.908s；Swift编译41.02s，522方法中500通过、22构包前可选skip，289.359s；222process / 443.183s、604core / 5.833s、20后置Foundation / 168.383s |
| macOS14.8.9同包consumer | 222process / 477.575s、604core / 5.538s、20Foundation / 174.134s |
| macOS26.6.2同包consumer | 222process / 458.227s、604core / 5.419s、20Foundation / 173.797s |
| 后置原生测试 | 两consumer各20Foundation、About1、Vision4、粘贴46全部逐方法一次通过、零skip；producer后置20Foundation/About1亦通过 |
| 私有剪贴板修复复核 | 三系统混合文件fixture前后C与AppKit计数均为2，不履约/不改changeCount断言通过；原多文本发布/换行边界断言通过；未修改生产粘贴实现 |
| 原生界面 | 84张真实PNG，旧71张全部保留、新增恰好13张；660最小主窗口名称/ID、Capture共享选择器、设置深色直接查看；合成状态不等于物理GUI验收 |
| 本地词典 | 产品URLSession下载67,948,544字节；同源模型到原生离屏绘制P95 67.543334ms，非物理键盘/打包GUI延迟 |

[完整App](https://github.com/mclight-ship-it/cc-translate/actions/runs/35229459129/artifacts/10502020234)、
[界面截图](https://github.com/mclight-ship-it/cc-translate/actions/runs/35229459129/artifacts/10500633203)、
[macOS14报告](https://github.com/mclight-ship-it/cc-translate/actions/runs/35229459129/artifacts/10501832665)、
[macOS26报告](https://github.com/mclight-ship-it/cc-translate/actions/runs/35229459129/artifacts/10502037302)
已独立下载核验，不混用失败run的包。内层ZIP **19,276,300 bytes**，
SHA-256 `1e5e3acff01ce084e96a2ff047b27598df5416ab7249a93d663439413fe3d73b`，
tree `07600d5f0543dd38bd58786dc36facfd60f82b55f72adfb49493871d4ac75a5b`。
实际688库存/78资源/50源码路径（49唯一）/6个Mach-O/19许可；633固定runtime与许可条目
和已验2f逐字节一致，bridge仅排除已知构建元数据后亦一致。
21文件consumer harness独立Git重建SHA
`fe2ba0dc37419b5249c6c708db0996d19b9e10f2fb103c7d8625294b429ec605`
与报告一致。HTTPS证书/随包CA、SQLite、取消/EOF、bundle不可变及临时清理均通过；
官方0.146.0/0.154.0仅版本/预热，未调用真实账号或模型。

首次 `572e1aa` / run35219286731 实际watch退出1：生产App已链接，但新测试的同步启动
回调被推断为throwing，无法赋给nonthrowing回调，尚未执行原生测试或构包。
修复仅把该测试断言换成明确的 `do/try/catch/XCTFail`，没有改生产代码、删断言或降低门槛。
第二次 `09917e0` / run35219785537 已实际编译（52.63s），521方法、22构包前可选skip，
35断言失败（232.954s），全部来自新增App目录/渲染测试。根因是协议扩展方法的默认参数
绕过了具体client实现；已改成与现有配置API一致的显式便利重载转发，并新增默认/显式超时
派发回归。失败日志与5张新状态PNG保留，不作为完整目录界面或App通过证据。
本次正常push曾再次被未改动的Windows历史矩阵WinError5阻断（1778 / 99.905s，2fail）；
同方法单独1 / 2.960s及完整正常重试均通过。没有更改Windows存储逻辑、跳过hook或声称修复权限问题。
第三次 `b1aa782` / run35220890958 编译49.67s，522方法、22构包前可选skip、2failure，
248.025s；30项目录App状态测试全部通过。实际PNG发现主窗口固定180宽选择器截断了模型ID，
另有原有中文纯文本粘贴结果字号过小的可读性失败。已改成弹性宽度并增加660×540最小窗口
的模型名称/ID断言，将原生粘贴结果改用与其他状态一致的callout字号；保留两项原断言。
当时预期84张PNG尚待执行；最终已下载本轮自己的84张，不借用失败轮截图作为修复证据。
第四次 run35221817416 的producer已通过：836便携、Swift522（500通过、22构包前可选skip）、
84张真实PNG、222process/604core/20后置Foundation，完整App也通过独立归档审计。
macOS14同包全部通过；macOS26的目录相关进程/核心/Foundation同样通过，但最后旧纯粘贴
私有C剪贴板测试出现2项失败（混合文件fixture仅1个item、多文本fixture发布时报OSStatus -25134）。
因此整run实际watch仍退出1，**不作为同包三系统绿色交付**。
两个粘贴生产模块、C支持与该测试文件相对原纯粘贴包均无源码差异；原始报告与日志完整保留。
仅做了一次同源码全流程复核run35224904618，实际watch仍退出1：producer通过，
但macOS14/26均复现旧混合文件fixture的AppKit计数1与预期2不符，不再无改动重试。
SDK定义的-25134是duplicatePasteboardFlavorErr，而非同步错误。候选修正bc9563f
将重复整数item ID替换为进程内保留对象的唯一opaque标识，保留原AppKit计数/不履约/
changeCount断言，新增前后C API计数诊断；未更改生产粘贴逻辑。
该候选run35228884435实际watch退出1，测试编译时发现ItemCount未作为Swift类型导入，
尚无候选运行结果。d637bea改为复用生产代码的计数类型推断，并将旧编码诊断的硬编码
item ID改为PasteboardGetItemIdentifier实际返回值；上述本轮三系统实测现已全部通过。
这验证了修正后fixture，不把一次成功宣称为所有系统长期稳定性保证。
整合另修复了目录清理失败后重用已失效provider导致“重试”持续失败的问题：
下一次用户明确刷新或翻译才关闭/drain旧helper并重建；不会自动重试目录或重放翻译。

使用步骤见[模型目录](MACOS_DEVELOPMENT.md#native-model-catalog)。
下一项继续真正图片provider/明确发送所选图片，剩余设置与P4–P6仍待实现；
不把OCR文字翻译等同于图片发送，不等待用户再说“继续”。

<a id="native-plain-paste-checkpoint"></a>

### P3 主动纯文本粘贴：同包三系统已通过，继续其余功能对齐

源码`2f371fa955876d70c84e2c53de16aad3b26e79c1` /
[run35210321489](https://github.com/mclight-ship-it/cc-translate/actions/runs/35210321489)
已实际watch exit0，attempt1的3jobs/40steps全部success。当前推荐下载更新为
[完整App10492197019](https://github.com/mclight-ship-it/cc-translate/actions/runs/35210321489/artifacts/10492197019)；
这不是整个P3或移植完成。操作见[纯文本粘贴](MACOS_DEVELOPMENT.md#native-plain-text-paste)。

- Settings复用`plain_text_paste_enabled`，明确开启后独占注册Option+Shift+Command+V；
  保存、回读、失败、注册冲突、取消及部分效果状态均已接入。重开只用曾明确开启的本机hint
  引导config-only读取，真实配置确认前不注册；默认关闭仍无剪贴板/权限/监控/CLI/网络I/O。
- 在其他应用中，等待触发键和修饰键释放，移除文字格式，再向捕获的目标提交一次粘贴；
  不使用不能吞键的被动监听，避免目标应用也执行同一快捷键。禁用立即停止接收新动作，
  旧保存/读取回调不能重新开启新关闭选择；退出等待未结束的数据读取。
- CC Translate自己的编辑器走原生`pasteAsPlainText:` responder chain，不申请AX或发外部键；
  非激活结果浮窗持有key window时也优先识别为本应用。全局功能关闭后仍保留Edit菜单的
  Paste and Match Style。IME composition和Cmd+Return有原生回归，不以此冒充真人输入法验收。
- AppKit的类型/条目元数据、变更计数和已准备好的UTF-8写入只在MainActor执行；
  可能等待promise的数据读取和RTF解析在专用队列使用新建C Pasteboard引用，不把缓存的
  NSPasteboard对象跨线程使用。读取前后和写入前后核对真实changeCount，保留一次性UUID/序号租约。
  changeCount检查不是跨进程原子CAS；不恢复原格式、不自动重试、不把键事件提交当成已插入。
- 保留Unicode/组合字符/制表符/换行；优先原始external UTF-16而非会改换行的兼容alias。
  全部条目的文件/文件promise元数据先检查；真实文件混合内容保持不变，文字附带备用图片
  则只读文字。HTML/RTFD-only、图片-only和RTF附件不进入外部资源转换。
  30秒默认读取期限不截成2秒；超时/取消只门控后续效果，不能中断的系统读取仍等待drain。
  不承诺任意系统daemon IPC绝不阻塞，也不把合成延迟当真实Universal Clipboard证据。

**实际失败及修复保留：**

| 源码/run | 发现与处理 |
|---|---|
| `2ceebb7` / 35196666915 | Carbon数量/大小参数实际Swift导入为Int，初版编译失败；修正ABI类型及MainActor最终清理后重跑，未执行的测试不计通过 |
| `be3163b` / 35197243289；`a8fca8e` / 35198418797 | 462项分别9/4失败：注册回调重入吞掉新配置请求、无效RTF被平台当空文字、平台纯文本alias与小字OCR。修复请求退休顺序、RTF头校验、保留字节一致性检查；状态/权限正文改callout字号，精确渲染断言不删除 |
| `09335b8` / 35202521292；`2f0f573` / 35202864582 | C适配器先因Swift不提供ItemCount别名而编译失败；改Int后469项17失败，揭示自有清空通知及UTF-16兼容转换问题 |
| `1f5846a` / 35203902558；`361cbe5` / 35205209500 | 469项分别9/16个Support失败断言；全部新UI已通过。私有数据诊断证实独立观察句柄延迟收到自己的清空，原始external UTF-16未损坏，是读取了会规范化换行的alias；没有加入错字/换行替换表 |
| `4df29f8` / 35206114383 | producer及macOS14通过，26的46项粘贴仍有3失败：文件混合元数据、读取期间换owner、另一适配器拒绝旧快照。整run实际exit1，不把15/14或该完整App当成最终交付 |
| `135b732` / 35209372816；最终`2f371fa` / 35210321489 | 改用MainActor AppKit元数据/真实changeCount/立即写入，后台只读可能等待的数据；前者三系统通过后，最终再用真实AppKit.writeObjects强化正向publisher覆盖，最终源码三系统重新通过 |

正常Windows完整hook最终1749/83.539s通过，targeted runtime23/0.999s通过。
本轮曾在未改动的history/config矩阵复现Windows访问拒绝：早期push有history失败；
push10的1749/88.051s出现26个config断言失败，日志含save_config WinError5。
同一个config方法独立1/2.766s通过，正常完整重试1749/82.196s通过。
没有改生产配置/历史或冻结oracle、跳hook、提权/关闭保护；旧访问拒绝问题仍未解决。

最终portable807/13.564s、Swift构建35.17s，469项（448通过/21构包前可选skip）零失败。
固定Git库存380→469，新增89个方法、零移除；46 Support及43 App方法逐项开始/通过一次。
扩展原19个Foundation之一验证默认关闭→保存开启→重开仍开启→保存关闭，未移除原始字节/未知字段检查。

| 同一个App/同源harness | process | core | 后置Foundation | About / OCR / 粘贴 |
|---|---:|---:|---:|---|
| macOS15.7.9 / Xcode16.4 | 214 / 398.672s | 585 / 4.517s | 19 / 171.151s | 1 / 4 / 46（粘贴0.173s） |
| macOS14.8.9 / Xcode16.2 | 214 / 375.142s | 585 / 4.469s | 19 / 154.254s | 1 / 4 / 46（粘贴0.123s） |
| macOS26.6.2 / Xcode26.6 | 214 / 398.085s | 585 / 5.702s | 19 / 164.241s | 1 / 4 / 46（粘贴0.179s） |

三系统逐方法核验，无后置skip，无背景NSPasteboard promise警告，含真实同进程AppKit publisher。
[71张原生PNG](https://github.com/mclight-ship-it/cc-translate/actions/runs/35210321489/artifacts/10491961172)
全部与已查看的字号修正布局逐字节相同，其中15张粘贴状态；不是物理键盘或打包GUI验收。
两consumer小报告为10493001001/10494355100，20文件library-only harness由固定Git原字节/模式重建为
`5d6bc07237078a64176f0bcbb1d4fb36911719a69b9a5029cbef2b42cdb663d1`，没有重建或重签被测App。

完整内层zip19,233,998bytes，SHA-256
`3ba7df479e738262b9656f915adc2277d0d89d772b97ea9a9b591c9780eebb1c`；
tree `d59cce54ad6b58e78d60775338db830af58d41b34965444353cb2016c0a9068c`。
CRC/路径/模式/链接、688库存、78资源hash、50 Core路径（49唯一）、6实际arm64 Mach-O及19运行时许可已独立核验。
632个固定运行时/许可普通文件和1条symlink匹配前置审计。自有重编译process-support dylib单独计数：
初版审计错误要求它与固定上游字节相同；实际差异限于UUID、ad-hoc签名及N_OSO对象时间戳，
其余字节与4df29f8对应产物一致，C源码/Package亦未改动；不冒充可重现发行签名。
证书/HTTPS/SQLite、取消/EOF、包不可变及临时清理逐字段通过；词典实际下载67,948,544bytes，
同源原生离屏绘制P95为42.818959ms，不是实际粘贴或物理键盘延迟。
官方CLI仍只做版本/native prewarm，无账号/模型调用。下一项继续完整模型目录/管理等P3，
真人外部编辑器、Universal Clipboard、TCC/IME/VoiceOver、多屏及P4–P6仍单列，不等待用户再说“继续”。

<a id="native-model-settings-checkpoint"></a>

### P3 自定义模型设置与混合语言 OCR：前置同包三系统检查点

该轮修正源码`b33515d7ece10a82eb2dacdc68f6d5440d172cc2` /
[run35185087510](https://github.com/mclight-ship-it/cc-translate/actions/runs/35185087510)。
实际watch exit0；API attempt1、3jobs/40steps全部success。完整App已独立核验，
该包保留为前置证据，当前推荐下载见上方纯文本粘贴检查点。56张该轮最终源码PNG全部保留，逐字节匹配已查看的首轮图；
新增8张模型设置状态及完整设置的中英/浅深布局均可读。这不是整个移植完成。

- 设置增加自定义Codex模型ID、应用、重置草稿、重新读取及保存/回读/错误状态；
  保留Fast/Default和已保存的自定义值，主窗口与截图共用选择器。
  草稿不直接改变请求，应用只保存和读取配置，不加载模型目录或探测账号。
- 修复共享配置每次把`gpt-5.4-mini`重新改成`auto-fast`的根因：
  旧默认迁移加一次性标记并持久化实际改写；之后明确选择的mini和其他ID
  原样通过保存、重新读取、重开和请求快照。不禁止mini，不增加CLI版本白名单。
- 新Foundation方法使用真实随包helper及合成provider，覆盖4个ID、文字/OCR来源、
  配置保存/重开及实际thread/start、turn/start的UTF-8字节；没有真实账号或模型调用。
  原18个Foundation保留，新总数19；About1和consumer OCR4独立执行。
- 生产Vision启用自动语言检测，保留原识别精度、关闭语言纠错及取消逻辑。
  4个生产API测试包括原660×120小字反例裁图、13px英文、简中/英文两种行顺序和繁中/英文。
  不放大/替换原像素、不注入预期词、不把应用界面语言强加给任意截图。
- 原始341项父整合测试有1个新增标记期望未同步，修正后341/8.350s通过。
  首次正常完整hook1744/84.702s被旧Windows冻结对照的110个子场景挡住；
  保留原AST散列，仅投影有意新增的迁移差异，并修复新查找错误假设raw必为映射的问题：
  复用已转换的迁移字典，保留旧Windows成对可迭代输入及错误/写盘合同。
  新增对应回归后联合352/11.738s及正常privacy/compile/完整hook1745/84.404s通过。
  没有绕过hook；原Tk teardown stderr保留，也不宣称旧Windows历史稳定性已修复。
- 前轮[run35182641842](https://github.com/mclight-ship-it/cc-translate/actions/runs/35182641842)
  实际watch1：portable803/11.810s、Swift构建28.41s、379/117.083s
  （21构包前可选skip、零失败）、214process/381.055s通过。
  4个生产OCR方法实际通过原小字反例及英文/简繁混合；后置Foundation19/157.646s，
  新模型方法的8组用例产生56个断言失败，未上传完整App，也未运行两个consumer。
  根因是合成CLI只接受旧目录里的模型并强求目录覆盖参数；真实产品本来就让目录外
  明确ID由原生Codex处理，目录不是使用门槛。修正仅在合成fixture中匹配该合同，
  保留已知模型的目录路径/原字节检查及mini的low effort要求，未放宽产品协议、
  删除模型/请求断言或编造模型目录。新增两项host回归，228/9.641s及正常完整
  hook1747/96.515s通过后推送`6652bd0`复验。
- `6652bd0`的producer已通过实际自定义模型方法（53.804s）、整个19Foundation/161.102s
  及585core/4.343s，确认上述fixture修复覆盖保存/重开到真实合成thread/turn。
  父再复核发现Picker仍使用Swift String的规范等价比较，可能丢失字节不同的已保存ID。
  `b33515d`把选择绑定、tag及列表identity都改为字节精确的Hashable值，并增加一个原生回归；
  不改变后端请求或新增使用条件。正常完整hook1747/98.709s通过；
  新字节identity方法已在最终原生运行中实际passed。
- 固定Git实点214process/585core/380Swift，bundle门槛同步；380包含原356、19模型设置、
  4生产OCR及1Foundation。21个构包前可选skip不代替后置执行。
  当前56PNG不包含checked-in的OCR输入图；原始失败日志保留。

最终`b33515d`的portable805/15.355s、Swift构建39.95s，
380/140.759s（359 passed、21构包前可选skip、0fail）。
固定Git原生清单356→380、+24/-0：23个新前置方法各一次started/passed，
新增模型Foundation及原18个Foundation在三系统后置均各一次started/passed，无后置skip。
15个新增core方法也在三个系统各一次ok；原始AST指纹不变，迁移的有意差异单独校验。

| 同一个App的系统 | 真实进程 | 核心 | 后置Foundation | About读取 | 同源生产Vision |
| --- | --- | --- | --- | --- | --- |
| 15.7.9 / Xcode16.4 | 214/405.160s | 585/5.021s | 19/169.277s | 1/0.014s | 4/4.392s |
| 14.8.9 / Xcode16.2 harness | 214/403.232s | 585/5.336s | 19/165.343s | 1/0.017s | 4/4.207s |
| 26.6.2 / Xcode26.6 harness | 214/391.597s | 585/5.032s | 19/164.702s | 1/0.032s | 4/3.533s |

Vision来自同源生产LocalOCR及原像素输入，不是打包GUI/TCC屏幕捕获实测。
两个consumer的19/1/4方法分别核对，没有用总数掩盖缺失、重复或skip。
完整App artifact10482227412，内层zip19,109,069bytes，
SHA-256 `32cefda7ac9c9d1a526873a3799bea411ed9b958a964277f90f6dc1a73bde53f`，
tree `e7678ec13e3db66bf5bf7b78f5cc57c886c6bdd9c29ca0d5ca13b2478d3b73a5`。
独立检查ZIP CRC/路径/模式/链接、688库存、78资源、50个Core来源路径（49唯一）、
6个实际arm64 Mach-O及最低系统、19份运行时许可/14项必需覆盖。
632个保留运行时/许可文件逐SHA与已核验关于包一致，另1个相同符号链接。
两个consumer的archive/tree/原16.4产品编译器均匹配；17文件library-only harness
从固定Git原字节及模式独立重建为
`92926c4697dfa99041de5c09368accc01158d81a8f5626353a7f456c17e84c10`，两端一致，
没有重建或重签被测App。UI artifact10482335822，14/26小报告10482731602/10481723601。
证书/HTTPS、SQLite、取消/EOF、不可变及临时清理字段逐项通过；
生产URLSession实取67,948,544bytes，同源模型到原生离屏绘制P95/最大57.188667ms，
不是物理键盘、打包GUI或OCR延迟。官方0.146/0.154只做版本/native prewarm，
没有账号、thread/turn或模型调用。

技术合同见[自定义模型设置](MACOS_DEVELOPMENT.md#native-custom-model-settings)。
该轮之后的原生纯文本粘贴现已在上方单独完成同包验证；对应Windows的主动去格式并立即粘贴行为，
不是只给翻译输入框增加Paste。完整动态模型目录、真正图片provider及其他P3–P6仍继续，
不等待用户再次“继续”。

<a id="native-about-checkpoint"></a>

### P3 关于与第三方许可：同包三系统已通过，继续完整设置

该检查点源码`d699185fe8d020b928bd4a8091447fb366304401` /
[run35176360537](https://github.com/mclight-ship-it/cc-translate/actions/runs/35176360537)。
实际watch exit0；API attempt1、3jobs/40steps全部success。该关于/许可包已完整核验；最新下载见上方模型设置检查点，
不是旧版黑导航截图所对应的App，也不代表整个移植结束。

- 原生About/许可视图、独立资源模型、菜单和设置入口已接通。构造无新增资源/业务I/O，
  不要求CLI/helper、账号或权限探针；正文完整字面显示，元数据不冒充签名/公证验证。
- 父直接复核交付并补齐第二个退出回调清理。
  真实包读取方法拆成独立测试，producer和两个consumer都必须后置运行；
  consumer的library-only harness使用原字节读取器/测试，未引入产品App构建目标。
  原18个Foundation和词典测量验证保留独立，XCTest执行校验共用已有逻辑。
- 新21个Swift方法：14资源/模型、3应用入口/生命周期、3渲染及1真实包读取；
  源码总356，构包前20个既定可选skip；新7张PNG、总48张已在前两轮实际生成。
  原214process/570core/18Foundation保持，另加每系统1个必须执行的包资源测试。
- 父宿主runtime targeted19/1.142s通过；正常privacy及完整hook1728/104.052s通过，
  保留既有Tk teardown stderr。编辑器没有报错不是Mac编译证据。
- 首版`8caec77`的[run35174381690](https://github.com/mclight-ship-it/cc-translate/actions/runs/35174381690)
  实际watch0：portable788/15.462s、Swift构建36.41s、356/106.734s、零失败；
  真实About包读取在15/14/26各一次passed。但实际PNG中的TabView导航为不可读黑块，
  因此没有把旧绿灯当作界面验收，也未下载即将替换的完整旧App。
- `83405c7`改为原生横向单选导航，并为全部7张About图加入独立顶条像素断言。
  [run35175862207](https://github.com/mclight-ship-it/cc-translate/actions/runs/35175862207)
  实际watch1：构建34.35s，356/107.699s，同一中文方法的两个OCR断言失败，未进入构包。
  实际中英/浅深PNG导航均可读，六张英文顶条通过；中文混合语言OCR误识别。
  当前`d699185`仅让测试按已知UI语言设置Vision，保留全部裁剪和文字断言；
  不改生产截图OCR、不放大图片、不注入预期词。正常完整hook1728/92.895s通过。
  两轮七张About PNG逐字节相同；当前中文断言通过，修正的是已知UI语言的测试识别配置。
- `83405c7`首次正常push的既有Windows历史矩阵明确遇到一次合成目录原子替换WinError5，
  导致同一方法的子场景及最终断言共2fail（1728/120.131s）。精确单方法1/3.503s、
  原样完整重试1728/93.333s均通过；未改Windows历史、权限或监控，也未解决其稳定性问题。
  日志中的日期是固定测试时钟，不是本次发生时间；不反推此前只有log.exists失败的根因。

最终`d699185`实测：portable788/17.271s；Swift构建46.01s；
356/124.716s（336 passed、20个构包前可选skip、0fail）。固定Git方法清单335→356、+21/-0：
20个新增前置原生方法各一次started/passed，新增真实包方法在三系统后置各一次started/passed。
原18个Foundation源码逐字节未变，每系统后置逐方法各一次started/passed，没有后置skip。
48张PNG实际保留，七张关于图均检查了独立顶条文字，且已实际查看中英/浅深/长正文/错误状态。

| 同一个App的系统 | 真实进程 | 核心 | 原Foundation | 独立About包读取 |
| --- | --- | --- | --- | --- |
| 15.7.9 / Xcode16.4 | 214/392.641s | 570/4.577s | 18/105.934s | 1/0.017s |
| 14.8.9 / Xcode16.2 harness | 214/377.144s | 570/3.900s | 18/103.942s | 1/0.014s |
| 26.6.2 / Xcode26.6 harness | 214/408.655s | 570/4.348s | 18/111.222s | 1/0.017s |

完整App artifact10479431166，内层zip19,073,485bytes，
SHA-256 `bda9a33e1e29a9b2740cc5729ba34b89978b69da7eb43dcdc50e6b7cfe2b86a5`，
tree `71421b23cdddf11e4fe0994f8e1facb7cd13faf07ef877f87ddfed09fcaf4ad0`。
独立核验ZIP CRC/路径/模式/链接、688项库存、78资源散列、50个Core源路径（49唯一，
包含非Python的Codex指令文本）、6个实际thin64 arm64 Mach-O及最低系统、
19份运行时许可/14项必需覆盖；632个保留运行时/许可文件逐SHA与此前独立核验截图包一致，
另有1个相同符号链接。两consumer的archive/tree/原16.4产品编译器均一致；
15文件library-only harness也从固定Git原字节和模式独立重建散列并匹配
`975b0863ec39824881a4a47190efa09b8618dc05c0b70ea5b4bcf59da72d7289`，没有产品App构建目标。
UI artifact10478831534；14/26小报告分别为10479311615/10479047017。

证书/HTTPS、SQLite、取消/EOF、不可变和临时清理逐字段通过。
生产URLSession实取67,948,544bytes；同源模型到原生离屏绘制P95/最大39.15725ms，
不是物理键盘、打包GUI或OCR延迟。官方0.146/0.154只做版本与native prewarm，
没有账号、thread/turn或模型调用。下一片继续完整设置/模型管理；小字号中英混合识别质量、
纯文本剪贴板及真正图片provider另有后续范围，不靠已知UI语言的像素测试宣称任意截图识别质量。

技术合同见[原生关于/许可](MACOS_DEVELOPMENT.md#native-about-licenses)。
完成这片仍不代表完整设置、真正图片provider或整个P3–P6完成。

<a id="native-capture-checkpoint"></a>

### P3 区域截图与本地 OCR：同包三系统已通过，继续剩余P3

当前源码`082aad6f26c6364acf7075e8e541d847b574bd25` /
[run35168220762](https://github.com/mclight-ship-it/cc-translate/actions/runs/35168220762)。
实际watch退出0，API核对attempt1、3jobs/39steps全部success；
[完整App下载](https://github.com/mclight-ship-it/cc-translate/actions/runs/35168220762/artifacts/10476491349)
已独立核验，当前下载段已切换到此截图包，不把截图PNG当成App制品。

- 原生主窗口/菜单栏截图入口、多屏保留帧、原生区域选框、同源裁剪、
  自动本地Vision、可编辑预览及明确“翻译文字”已接线。
  支持拖动、两次点击和键盘选区，窄窗口上下布局；取消/关闭/重选不自动发送。
- 捕获与OCR不需要CLI、helper或账号，不上传图片、不读取/改写剪贴板、不保存原始图像。
  文本翻译只在明确操作后使用既有Codex链路；真正图片provider仍未实现，不能一起勾完。
- OCR文字保留空白/Unicode/换行，禁用翻译缓存、本地查词与自动摘要；
  普通文字复用与Windows相同的OCR排版提示，单词/代码保留既有专属提示，
  完成结果与历史始终为ocr并保留分类标记，遵守最新保存开关。
- 父整合修复旧授权返回覆盖新操作、旧OCR迟到结果覆盖无效选区、
  首次菜单栏截图返回焦点，以及旧诊断截图4096上限被产品预算放大的关联问题。
  重选保留原帧；取消中的Vision只排队最新明确OCR意图；屏幕布局改变使旧事务失效。
- 方法盘点：新增48 Support、35 App、2协议/连接、1 Foundation，共86 Swift方法，
  总335；进程214、核心570、后置Foundation18。不是减少原覆盖后得到的数量。

**失败与修复保留：**

1. 初次正常push的1725项有3失败：两个共享提示抽取快照未同步，以及未修改的Windows历史矩阵
   末尾日志存在断言。快照保留原12项总hash，另从固定旧Git源码逐字节验证OCR常量；
   历史失败未取得具体日志原因，后续targeted与完整hooks通过，不宣称旧稳定性问题已修复。
2. `5662518` / [run35165336983](https://github.com/mclight-ship-it/cc-translate/actions/runs/35165336983)
   真正失败于Swift通知嵌套closure缺少显式self；补明确接收者，没有改行为或删测试。
3. `3bebcad` / [run35165672294](https://github.com/mclight-ship-it/cc-translate/actions/runs/35165672294)
   编译成功，335项中3方法的5断言失败：dictionary_lookup意外继承OCR origin；
   CGRect.width归一化负尺寸；OCR升级测试误将既有dictionary_status刷新当成lookup。
   分别保留查词text/selection范围、检查原始size.width/height、精确断言只有status刷新。
   本轮4个新渲染方法已通过、41张PNG已保留，但没有完整App。
4. `d6bc831` / [run35166343496](https://github.com/mclight-ship-it/cc-translate/actions/runs/35166343496)
   实际watch退出1：portable785/10.581s通过，Swift编译29.63s，
   335/96.251s中316通过、19构包前可选skip、零失败。
   包内进程214/392.843s在两个新增OCR方法的4个子场景失败：测试fixture重复使用save请求ID，
   被正确的连接规则拒绝；两consumer未执行，没有完整App上传。
   当前修复仅让该fixture每次保存使用新ID，另加宿主可执行的实际configure方法序列化回归；
   旧方法明确复现1个唯一ID而非2个的失败，修复后34/1.817s通过。没有放宽生产协议。
5. 正常完整hooks依次1725/85.123s、1725/84.685s、1725/84.368s，
   请求ID修复1726/90.621s、设置说明同步1726/87.025s通过；保留原Tk teardown stderr，没有跳过hooks。
   最终同App三系统、后置Foundation及独立完整制品审计结果见下。

设置页原本仍称截图翻译尚未实现、仅为诊断探针；082aad6同步两段中英文说明，
正确区分已实现的本地识别/文字翻译和未实现的直接图片请求。
前一fixture修复run35167671528实际watch退出0，日志另行保留，不代替该设置源码的新run。

**最终082aad6的执行与制品证据：**

- producer portable786/13.626s通过，Swift编译30.20s；
  335/83.033s中316通过、19既定构包前可选skip、零失败。
  固定Git源码盘点及实际日志逐项核对：新增85个前置Swift方法各通过一次，
  新第18个Foundation方法在三个系统后置各通过一次。
  新7个进程方法、新17个核心方法及1个原快照方法改名，在三系统相应suite均各通过一次，
  原17个Foundation方法也均保留并各通过一次，没有后置skip。

| 同一App执行系统 | 真实合成进程 | 包内核心 | 后置Foundation |
|---|---|---|---|
| 15.7.9 / Xcode16.4 producer | 214 / 392.200s | 570 / 3.612s | 18 / 105.568s |
| 14.8.9 / Xcode16.2 harness | 214 / 395.340s | 570 / 4.495s | 18 / 107.300s |
| 26.6.2 / Xcode26.6 harness | 214 / 382.925s | 570 / 4.284s | 18 / 103.745s |

- [41张真实原生渲染PNG](https://github.com/mclight-ship-it/cc-translate/actions/runs/35168220762/artifacts/10475427508)
  包含10张新截图状态；已实际查看当前窄浅色/宽深色预览，两行源图文字和编辑内容可读。
- 内层ZIP 18,957,188 bytes，SHA-256
  `a9c4d75d0dd10229c2f66f8ac8a5343af1f4990ff290b1c69966ce9ebfd26fe8`；
  tree `37528ae938bbd32e672207cbce65c217a670e9059d91d59d1c8031d40a0f175f`。
  独立核验ZIP CRC/路径/权限/链接、688库存、78资源、
  50个Core源码路径（49唯一）与固定Git原字节、6个实际arm64 Mach-O头/最低系统、
  19运行时许可/14 required覆盖；保留运行时及许可632文件与前一已独立核验包逐SHA相同。
- macOS14/26小报告分别为artifact10476008709/10476467369，archive/tree及producer编译器
  与独立审计一致；没有重建/重签产品App。HTTPS证书、SQLite、取消、EOF、临时清理、
  bundle不可变逐字段核对通过。
- 本地词典生产URLSession再实取67,948,544 bytes，当前意图到同源离屏绘制P95/最大41.833958ms；
  不是物理键盘、打包GUI或截图OCR的延迟指标。官方0.146.0/0.154.0仅版本与native预热通过，
  没有账号、thread/turn或真实模型调用。

已重新查看d6源码的窄浅色/深色实际截图：保留图片自身均完整显示两行，
没有确认先前疑似裁切；未因此保留推测性预览重构。截图和离屏OCR不等于真人GUI、
TCC、IME、VoiceOver、多屏/Spaces或真实账号模型签收。
技术合同见[区域截图/OCR](MACOS_DEVELOPMENT.md#native-capture-ocr)；
本切片结束后继续完整设置/关于许可等P3，不把整个移植标为完成。

<a id="native-history-search"></a>

### P3 全库历史搜索：已通过同包三系统，继续区域截图

当前源码`eaf0c15af071fa40f250d7052d517ebbd6f2ef3c` /
[run35157108608](https://github.com/mclight-ship-it/cc-translate/actions/runs/35157108608)
已接通完整历史快照搜索，不再只筛选已加载页。搜索原文、译文和时间戳，
类型固定为文字/词典/代码/OCR；先全库过滤再分页，显示实际匹配总数。
输入250ms合并，Return立即提交，类型立即切换；在途读串行，旧条件终态不覆盖新条件，
分页绑定条件与实际数据revision，过期时明确刷新而非自动重放。
关闭/退出/断连取消延迟意图；清空仍需明确确认，覆盖全库，不仅是当前筛选结果。
本地词典历史保留字面释义，AI历史保留Markdown；Windows实际复用同一纯过滤函数。

正常源码hook1705/88.187s与渲染修复hook1705/91.993s均通过；另独立targeted111/6.700s、
原Windows历史UI helper4/0.014s。首源码`4bc4a59`的
[run35156574433](https://github.com/mclight-ship-it/cc-translate/actions/runs/35156574433)
真实watch退出1：Swift25.36s编译成功，249项中18项既定构包前skip，
仅新历史详情渲染一方法4断言失败。实际PNG显示独立详情缺少生产父视图的背景，
浅色黑字落在透明像素上；修复只给独立渲染fixture补相同windowBackgroundColor，
不改生产、不删OCR/非空颜色断言，保留首次失败日志及31PNG。
新源码的31张原生图已生成，浅色字面释义、深色AI Markdown及搜索空态已实际查看。
**本轮实际watch退出0，API attempt1的3jobs/39steps全部success。**
producer portable768/16.815s；Swift编译43.53s，249项中231通过、18项既定构包前可选skip，
新增26历史模型、4原生渲染/窗口生命周期及2协议方法全部真实执行。
对Git前后AST差分与日志逐方法核对，每系统新增5process/17core各一次passed，
原17Foundation全部保留并扩展真实历史搜索链，构包后零skip/failure。

| 同一个App的执行系统 | process | core | 后置Foundation |
|---|---|---|---|
| 15.7.9 / Xcode16.4 producer | 207 /346.682s | 553 /4.178s | 17 /88.995s |
| 14.8.9 /独立harness Xcode16.2 | 207 /363.246s | 553 /5.194s | 17 /104.173s |
| 26.6.2 /独立harness Xcode26.6 | 207 /368.664s | 553 /5.301s | 17 /90.654s |

[完整App artifact10471459022](https://github.com/mclight-ship-it/cc-translate/actions/runs/35157108608/artifacts/10471459022)
已独立核验：内层18,829,861 bytes，
SHA-256 `9a42321555714e717d73dcf14a2cb23d3936cd625ce85c67392cc017109cfc23`，
tree `1451edf48d083318999b7524943ee746e47cedf3c38ce610bddbd4f8965a84b1`；
688库存/78资源/50source路径（49唯一）/6实际arm64 Mach-O/19运行时许可/14 required覆盖。
源码逐字节等于固定Git提交，而非并行中的截图开发工作树；605个上游runtime普通文件及许可
逐字节等于前置独立审计b86507f包。库存、模式、链接、资源和归档CRC均核验。
14/26报告archive/tree完全相同，HTTPS证书/SQLite/取消/EOF/不可变/临时清理逐字段通过。
独立审计脚本首次把coverage对象键数误当required数量，改读实际required数组后通过，
不修改产物、许可或产品规则。

词典原生下载/安装也在本源码真实重验67,948,544 bytes，URLSession1039.272ms；
2次warmup后的10次模型意图到同源只读原生视图/离屏绘制P95及最大55.88675ms，
满足该端点150ms目标；不是物理键盘、打包GUI进程或独立query+format10ms验收。
官方0.146.0/0.154.0仅预热、无账号/模型请求。
[31张原生截图](https://github.com/mclight-ship-it/cc-translate/actions/runs/35157108608/artifacts/10472180519)、
[macOS14小报告](https://github.com/mclight-ship-it/cc-translate/actions/runs/35157108608/artifacts/10472196184)及
[macOS26小报告](https://github.com/mclight-ship-it/cc-translate/actions/runs/35157108608/artifacts/10471794685)
均来自同run。完整App保留到2026-09-23T22:30:46Z。

区域截图Support、OCR文字后端和原生预览/明确发送界面已进入下一实现切片，
本历史包不含这些后续未提交改动，不能借此绿色签收截图功能。无需用户再次说“继续”。

<a id="native-result-actions"></a>

### 当前结果操作检查点：已通过同包三系统，继续本地词典

已接六种原生结果操作：精简、正式表达、摘要、解释代码、按普通文本翻译、指定语言翻译。
前三者使用不可变主结果，后三者使用原始请求输入；UI后续编辑或已有追加区块不改变输入来源。
结果追加到主结果后，完成文本只修正自己的区块；取消/失败保留主结果并标记未完成区块。
清空、复用历史或新翻译隔离迟到回调，下一条明确请求仍等待旧请求终态，不拿取消确认当终态。
动作不查询翻译cache、不读写history（含history开关开启或文件损坏），也不自动重试。
沿用共享prompt/RequestSnapshot/原native进程组及同一流式预算，未增加CLI版本使用门槛。

Python后端与Swift typed API、模型、菜单已配套；Windows针对性219项通过，
包含普通翻译/配置回归、fixture和打包清单。Mac193process/504core/15Foundation已实际通过，
保留原13Foundation并新增真实动作/取消闭环；新增Swift模型/协议/连接/渲染测试也已执行。
其中两张新原生结果图通过本地OCR检查正文与Actions文字，已实际查看，不以非空PNG代替可读性。

首轮源码`fa5c5e8` / [run35125217730](https://github.com/mclight-ship-it/cc-translate/actions/runs/35125217730)
真实编译全部Swift成功（43.32s）；36原产品模型、新8动作模型、6原生渲染（含动作可读性）及连接测试通过。
总173项、15项既定构包前可选skip、1failure，未构包：新增协议测试误以为通用JSON编码器必须拒绝
缺operation的对象；原契约是在register/send写管道前拒绝。修正为分别验证通用编码与请求注册，
保留所有非法动作字段/类型/字节预算的拒绝断言，以及缺operation注册失败、零pending请求的断言，
不为这个测试增加生产使用门槛。
Windows首个正常hook曾1606/79.209s失败两项未改动用例（topmost瞬时状态、history矩阵末尾日志存在）；
两项单独2/2.513s通过，不改断言后正常完整hook1606/74.922s通过并推送。
原失败日志保留；未获取此次history日志的具体原因，不擅称WinError5，也不宣称原稳定性问题已修复。

最终源码`4807f62a79c222b7fde75052d5e8cbd023b664cb` /
[run35125864397](https://github.com/mclight-ship-it/cc-translate/actions/runs/35125864397)
真实watch退出0，API核对attempt1、3jobs/35steps全部success。
正常privacy/full hook1606/75.010s；producer portable709/10.360s；
Swift编译24.90s，173项中158通过、15项构包前可选skip、零failure。
包含原36产品模型、新8动作模型、新6动作协议及2动作连接测试、6原生渲染与16张PNG。

| 同一App的执行系统 | 真实合成进程 | 核心 | 构包后Foundation |
|---|---|---|---|
| 15.7.9 / Xcode16.4 producer | 193，零fail/error/skip | 504，storage fixture通过 | 15，零fail/skip |
| 14.8.9 / Xcode16.2 | 193，零fail/error/skip | 504，storage fixture通过 | 同15方法，零fail/skip |
| 26.6.2 / Xcode26.6 | 193，零fail/error/skip | 504，storage fixture通过 | 同15方法，零fail/skip |

新Foundation逐个运行六种动作、每种明确请求两次，验证真实native合成CLI的prompt、非缓存提交，
即使历史文件故意损坏也不读取/覆写它；另验取消后的自有组及后代清理。
新进程测试实际覆盖动作queued取消、EOF与无重放、非法动作提交前确定失败。
[完整App artifact10459632452](https://github.com/mclight-ship-it/cc-translate/actions/runs/35125864397/artifacts/10459632452)
已独立读取archive逐字节核验；18,719,555 bytes，SHA-256
`5e371baeb464eea5de94712d80b0cf9a6034137f2546e173a1bdf3dc9b04bc14`；
tree `65ffc349934f7e6ed48ef94acd894f220d06626c010efc20b15db5a2583f475a`。
684库存/74资源/46Core路径（45唯一）/6实际arm64 Mach-O/19许可/14 required覆盖，
605个上游保留Python普通文件与自有process-support dylib分别核对。
三系统archive/tree一致、包未修改；HTTPS证书/SQLite/取消/EOF/临时清理均通过。
官方0.146.0/0.154.0只做版本与initialize/initialized/hooks/list预热，无账号/模型调用。
[16张真实原生截图](https://github.com/mclight-ship-it/cc-translate/actions/runs/35125864397/artifacts/10459243089)
另存制品；合成测试与截图不代替真人键盘/VoiceOver/IME/TCC或真实账号验证，没有新增独立reviewer签收。

<a id="native-local-dictionary"></a>

<a id="native-dictionary-checkpoint"></a>

### 本地词典首屏与管理：b86507f检查点

纯lookup/artifact/presentation及configuration-only六项操作已接线；Mac不需要Codex即可命中，
不自动补充AI。Windows保留原默认路径/facade/format-v8及补充行为。
URLSession使用session-bound ticket的初始不存在路径，核心验证固定pin、fsync/replace后启用；
原生界面及生产URLSession已经真实Mac编译与后置下载验证，不把“后端查到词”当作完整产品。
本地文本保留全部义项/读音/来源许可，独立native-plain-v1语言签名；缓存/历史错误显式显示，
不阻挡已确认的本地定义、不修复坏历史、不当成miss提交模型。

源码`b86507fd861e363b722fc11537045e0817dda251` /
[run35147996073](https://github.com/mclight-ship-it/cc-translate/actions/runs/35147996073)。
最终Windows针对性132项/15.475s、隔离复制Core实跑536项/12.126s、
正常privacy/compile/完整hook1687项/85.703s通过；早前跨Windows词典消费者联合390项也通过。
本run实际watch exit0，API attempt1、3jobs/39steps全部success；同包三系统各202process/536core/17后置Foundation，
无失败/skip，producer另实跑1个原生下载/绘制产品测试。
Swift编译39.08s，217个测试为199pass+18构包前可选skip；后置Foundation及产品测试均零skip。
新增9个Swift协议、21个词典模型、6个下载、3个连接、2个渲染、2个Foundation及1个产品测试已接线执行。
父整合补齐queued取消清理失败的seq1终态，与实际Python server回归配套，不放宽其他协议断言。
配置业务入口现在也合作处理SIGTERM；关闭仍先drain，不用取消确认或超时假称清理完成。
固定词库GitHub元数据已实核size/hash；开发机既有忽略数据只读复用，没有重新下载或安装。
CI外置Python测试数据获取与产品URLSession分别记录；后者实际下载67,948,544字节，CI传输622.187ms，
随后真实随包校验安装并启用，零CLI候选/模型提交。网络速度不作用户承诺，不计入warm显示耗时。
两次warmup后十次意图到同源原生视图离屏paint，P95/最大37.747ms，实测满足此端点150ms目标，
来源OCR可见。物理键盘/打包GUI进程/独立10ms查询格式化没有据此宣称通过。
失败/skip/缺失/重复/自相矛盾测量由独立日志解析拒绝，不用XCTest绿灯代替数值。
父修正本地字典literal呈现的来源判定，历史从local-dictionary签名恢复，AI词典仍保留原Markdown格式。

| 同一个App的实际系统 | process | core | 后置Foundation |
|---|---:|---:|---:|
| 15.7.9，producer Xcode16.4 | 202 / 384.170s | 536 / 5.819s | 17 / 106.623s |
| 14.8.9，独立harness Xcode16.2 | 202 / 333.930s | 536 / 3.390s | 17 / 83.074s |
| 26.6.2，独立harness Xcode26.6 | 202 / 344.436s | 536 / 3.836s | 17 / 84.638s |

父从该源码AST及完整日志逐名核验：新9process和32core在每系统各一次passed，17Foundation完整保留。
producer portable751项/16.212s；三系统八次warm Foundation往返分别2.294–5.403、1.446–1.812、
1.283–1.432ms，仅是往返，不冒充独立IPC、查询格式化或GUI分项。
[Mac14报告](https://github.com/mclight-ship-it/cc-translate/actions/runs/35147996073/artifacts/10467969481)、
[Mac26报告](https://github.com/mclight-ship-it/cc-translate/actions/runs/35147996073/artifacts/10468937061)
均独立读取并比对相同archive/tree/source、helper清理与不可变字段。

[完整App artifact10468966389](https://github.com/mclight-ship-it/cc-translate/actions/runs/35147996073/artifacts/10468966389)
内层18,810,874字节，SHA-256 `16e87b540606de1edd78d1f32ff019265cee69a6c72786de54161e8daf9cf4e8`，
tree `fce9adf9ee83a8ff612c82f9050e068ae99411286ddf6ce0580528735aa4c76e`。
父已独立核验完整ZIP的CRC/权限/软链接、688库存、78资源、50源码路径（49唯一Git源码）、
6个实际arm64 Mach-O及最低系统头、19运行时许可/14必需覆盖；
605个上游运行时普通文件及许可逐字节等于前置已独立审计4807f62，不重复下载上游归档。
词库本体和Windows facade未混入App。[21张原生截图](https://github.com/mclight-ship-it/cc-translate/actions/runs/35147996073/artifacts/10467946406)
保留浅/深色完整义项、词典管理、长文滚动及AI词典格式回归；合成截图不是真人GUI/TCC签收。
核验后仅删除本地临时完整App ZIP及两份审计脚本，保留原始日志、JSON证据与21张PNG。

本轮失败及修复保留，未降低断言或绕hook：
- 首次合成URL字面量触发privacy；改用URLComponents构造测试身份，不加扫描豁免。
- 35143650643没有runner执行：job env不支持runner.temp；改为step内RUNNER_TEMP/GITHUB_ENV并同步测试。
- 35144208996在Swift编译发现dictionary校验放错JSONValue作用域，移到ClientMessage，与其他请求一致。
- 35144794911编译成功，仅worker失败测试漏填新增词典参数；补真实必填字段，保留失败断言。
- 35145246205产品测试错误解析尚不存在的staging文件；改验已有父目录，保留范围和不覆盖检查。
- 35146057890原生绘制P95为42.781ms，但仅复制fixture且最终Core测试包导入失败，不当新包绿灯；
  已按既有独立模块约定修复导入，子进程改用实际Core路径；536项隔离验证后再加入生产URLSession实测。
- Windows正常hook曾再现旧history WinError5/日志断言；单测及正常完整重跑通过，不宣称已修复根因。

随后继续P3全库历史搜索等剩余功能；Python后端已恢复独立实施，不等待用户再发送“继续”。

<a id="codex-protocol-checkpoint"></a>

## 当前检查点：官方通知时间戳修复与真实预热已通过三系统

用户在版本兼容包报告`provider_protocol_error`且明确未提交模型。
隔离临时HOME的官方0.146.0在本地只执行initialize/initialized/hooks/list即可复现：
官方启动通知含顶层`emittedAtMs`，native envelope白名单漏了该字段，
因此把本来允许的状态通知误判为`invalid_appserver_message`。
不需要账号或模型即可证明此客户端缺陷；尚未取得用户所选CLI数字版本，
不声称已在用户Mac上确认唯一根因。

修复只接受官方定义的可选时间戳及其类型，保留未知字段、工具/hook、身份、
重复事件、取消和结果未知的严格校验。新增回归先在未修改生产上实际失败，
原2tests/4fail与真实二进制的无敏感值响应形状已留证。
官方0.146/0.154源码均定义`ServerNotificationEnvelope.emitted_at_ms: Option<i64>`：
只允许notification角色，缺失/null/有符号64位整数合法，不接受bool/小数/字符串/越界值，
不把该时间戳作为deadline或身份。response/request字段规则保持。
本地修复后已接收真实带时间戳的启动通知；其后Windows系统启用的hook仍被Mac策略拒绝，
这是预期保护，不以跳过该hook把Windows预检冒充Mac成功。
最终针对性206/15.106s通过，包含新增5个核心方法；同包最低门槛190process/478core/原13Foundation。
CI已把两份固定官方CLI检查从仅`--version`扩展到真实native prewarm，
并明确禁止发送thread/start或turn/start；旧version-only绿色不覆盖本次协议缺陷。

**源码 `3ee680a98141badc8b7499eff6716c7223aa41d4` /
[run35103974280](https://github.com/mclight-ship-it/cc-translate/actions/runs/35103974280)**：
真实watch exit0，API确认attempt1全部3jobs/34steps success。
正常privacy/8文件compile/完整hook **1573/90.758s OK**；
既有Tk teardown stderr及拒绝隐式HTTPS的负例输出保留，旧WinError5未宣称解决。
生产仅native envelope的8行变更；无Swift行为修改，无关闭协议保护或自动重放。

| 实际系统 | 包内process | 包内core | 后置Foundation |
|---|---:|---:|---:|
| 15.7.9 / Xcode16.4 producer | 190 / 338.948s | 478 / 3.391s | 13 / 56.495s |
| 14.8.9 / Xcode16.2 harness | 190 / 322.816s | 478 / 3.767s | 13 / 51.260s |
| 26.6.2 / Xcode26.6 harness | 190 / 332.598s | 478 / 5.280s | 13 / 53.724s |

以上零failure/error/skip；新增2process/5core方法三系统各passed一次，13Foundation逐名核对。
producer portable676/9.950s；普通Swift113=100pass+13无App可选skip，后置13真实执行另计。
**官方0.146.0和0.154.0的固定arm64二进制在producer均真实通过版本读取及native prewarm**：
使用本App随包Python/原进程监督/临时HOME，无账号；精确只发
initialize/initialized/hooks/list，turn_submitted=false，关闭后临时目录已清理。
这弥补了旧版只测版本号的缺口，但仍不是官方模型翻译或用户GUI验收。

新制品与独立字节核验：
- [App10449458171](https://github.com/mclight-ship-it/cc-translate/actions/runs/35103974280/artifacts/10449458171)，
  有效至2026-09-23T13:54:28Z；内层`CCTranslateMac-P0.zip` **18,504,081 bytes**。
- SHA-256：`f9022a0474c10476444696f3d89052be8b3596bd2a9cf0adc17db3d81cb5e10f`；
  tree：`27718d8cb9941dd6eef8386aca5826edb2fac2021cfeaf88dbb20572b9933414`。
- 14/26小报告10450196719/10449883887：使用同一15构建App，不重建或重签产品。
- 684库存/74资源/46Core源码路径（45唯一Git路径）逐字节对应source；
  6实际arm64 Mach-O的load commands/最低OS/rpath/依赖与producer报告匹配。
  runtime许可证：lock最低10项、实际coverage要求14项、包内19文件均验证，三种口径分开；
  606 vendor runtime文件/链接与前置包相同，原605 full-build覆盖声明保留。
- 三系统HTTPS证书/SQLite/storage/取消/EOF/不可变均通过。
  下载ZIP和3个临时验证脚本已精确删除，原始失败/成功日志、小JSON、官方预热及审计证据保留。

用户改用[新包最小步骤](MACOS_DEVELOPMENT.md#native-translation-user-check)测试一次合成翻译即可，
不需重装/降级/重新登录现有合适CLI。

**2026-09-16 用户实测反馈：翻译通过，目前测试可用。**
该反馈发生在本修复包交接之后，记录为本轮翻译主流程的正向用户验收，不再标为等待首次翻译成功。
未额外采集CLI版本、包hash或模型调用日志，不扩大为所有平台/账号/模型及完整权限矩阵通过。
当前主流程作为可用基线保留；其他兼容性和交互测试是后续质量工作，不是正常使用的前置门槛。

<a id="codex-version-checkpoint"></a>

## 前置检查点：Codex 最低版本与诊断修复已通过三系统

用户在前置开发包遇到 `provider_version_unsupported`，更换 CLI/路径后仍报告失败。
尚未收到该次实际版本输出，不能认定用户安装错误，也不能把下面的修复当作其真机已通过。

- 执行层、模型目录及目录缓存共用稳定版本 `>= 0.146.0` 的判断，不再拒绝所有更新版本。
  版本识别只取唯一的 `codex-cli` 标识行，避免把警告中的其他软件版本当成 CLI 版本；
  数字比较、UTF-8/长度、格式、重复行、build/prerelease 边界明确，实际协议检查保留。
- 过旧、无法识别、预发布及协议错误分别处理；CLI locator 的显式版本探针补充安全版本反馈，
  不暴露原始输出或私人路径，不在启动时运行 CLI。
- 新版本仍必须通过实际协议与目录往返检查；不增加 exec 回退、自动重试或模型调用。
  版本失败后的新显式请求可以重新检查，不把失败变为永久状态或自动重放。
- 版本输出过旧/不可识别时不运行模型；修正版本后须新显式请求才执行，不自动重放，
  也不把可恢复的版本错误永久锁死。协议错误若可能已经提交，界面明确提示结果未知、不得重放。

**源码 `3efebbfabb7a6af16772d313ca3a5789a0988b64` /
[run34996120967](https://github.com/mclight-ship-it/cc-translate/actions/runs/34996120967)**：
同一原 reviewer 的30文件增量审查可接受，无必须修复项；实际watch exit0，
API确认attempt1、3jobs/34steps全部success。没有重跑不变源码或绕hook。
Windows最终联合363/29.460s通过；唯一正常privacy/compile/full hook
**1565/78.897s OK**。原Tk teardown stderr、拒绝隐式HTTPS的负例输出仍保留，
旧WinError5来源未知，不把本次通过称为解决了该历史风险。

| 实际系统 | 包内process | 包内core | 后置Foundation |
|---|---:|---:|---:|
| 15.7.9 / Xcode16.4 producer | 188 / 347.467s | 473 / 3.844s | 13 / 54.297s |
| 14.8.9 / Xcode16.2 harness | 188 / 321.428s | 473 / 2.969s | 13 / 48.909s |
| 26.6.2 / Xcode26.6 harness | 188 / 329.863s | 473 / 3.201s | 13 / 52.956s |

各包内suite零failure/error/skip，全部新/重命名方法及13个Foundation逐名核对各passed一次。
初次普通Swift **113=100pass+13无App可选skip**，后置13真实执行另计；
producer portable668/10.093s。新增真实进程覆盖0.147.0/0.154.0/1.0.0合成CLI的
版本/目录/模型响应/历史全链，以及过旧、非法编码、重复版本、预发布等明确未提交失败。
**官方0.146.0与当前stable0.154.0原生二进制也各实际执行了`--version`**：
固定GitHub官方SHA下载，使用本App隔离Python与原自有组监督、临时home和最小环境，
两者识别正确且满足最低版本；原始输出不入报告，二进制不打进App、执行后已清理。
这仅验证真实版本输出，不证明官方账号、模型或所有新版协议均兼容。

完整App及报告：
- [artifact10407398182](https://github.com/mclight-ship-it/cc-translate/actions/runs/34996120967/artifacts/10407398182)，
  有效至2026-09-22T16:46:19Z；内层`CCTranslateMac-P0.zip` **18,503,830 bytes**。
- 内层SHA-256：`d15b1b32841f28f8645dd38c24a2d715f2aa551a536bca8ab593c6eca4f80372`；
  tree：`9ac2e5bd1868375a2cd268d36734c9bfe77b859fe24877718818ab21cb3fdf58`。
- 14/26小报告分别为10408281891/10407929214；同一15构建App，不重建或重签产品。
- 独立ZIP逐文件审计：684库存/74资源hash/46Core源码路径（45唯一Git路径）/
  6实际arm64 Mach-O及load commands均对应指定source和producer报告。
  包内19个runtime许可证文件（manifest要求14个）已纳入资源字节校验；
  606个vendor runtime文件/链接与前置已验收包一致，原605 full-build覆盖声明保留，两个口径分开。
- HTTPS证书/SQLite/storage/取消/EOF及同包不可变继续通过。完整ZIP和两个临时审计脚本已删除，
  小JSON、官方version-only报告、逐名发现结果、完整日志及source/docs分离检查点保留。

下一步使用[当前新包操作说明](MACOS_DEVELOPMENT.md#native-translation-user-check)。
无需为新版稳定CLI降级；先用App的显式版本探针确认同一选择的安全版本信息，再由用户主动测试翻译。
原2b116包及其绿色不覆盖本修复，旧实机报告不迁移到新包。

<a id="translation-ipc-checkpoint"></a>

## 前置检查点：显式 native 翻译业务已通过同包三系统，账号/实机未验

前置`6d029d1` / 文档`e577c5b`及run34836719504已获独立增量审查和三系统报告核验接受，
三项审查问题关闭，不重复该源码CI。按持续授权实际接下一业务链：

- [x] 原摘要/阈值共享抽取且Windows真实入口复用；分类/方向/prompt/cache字节保持，
  helper构造完整RequestSnapshot，不导入cc_core/Tk/Win32。
- [x] 显式连接固定home、实际Info.plist身份、CLI绝对路径与私有编码环境；
  普通诊断/config-only不启动CLI。Native环境不放argv，也不直接污染helper加载器环境。
- [x] 翻译worker与原storage FIFO并行，实际provider调用不占状态操作锁；
  执行快照冻结，完成时重新检查已提交的当前history开关/limit，取消/UI状态不冻结。
  已开始最终history提交后不假称可撤销；关闭等待受控执行/写入并释放双owner。
- [x] Python/Swift严格同协议：ID/seq/唯一终态、实际UTF8和转义字节、完整envelope累计预算、
  submitted/未知结果/不重放；原config/history五操作及默认启动保持。
- [x] 临时home中的真实Foundation→包内helper→合成native CLI→配置/历史。
- [x] 现有原生界面已提供明确启用、输入/选区触发、流式结果/复制、设置保存和历史分页/清空；
  App已编译，默认启动不执行用户CLI或模型。这一行不是GUI真人验收。
- [x] 针对性Windows/正常hook、同包15/14/26、精确新增发现/完整App来源/许可/清理。
- [ ] 官方Codex安装/兼容版本/账号/真实模型，以及新包Finder/TCC/IME/焦点/多屏集中验收。

当前已有可调用并经真实合成进程验证的翻译开发闭环，不把合成CLI当作官方账号/模型可用，
不把UI编译当Finder/TCC/IME/多屏验收。Claude Darwin、完整vision/词典呈现等仍未完成，
不宣称整个P0/P1/P2–P6完成。旧Windows WinError5来源未知继续保留。

本轮预提交及首轮失败记录（与最终Mac证据分开）：
- 摘要抽取前原27项通过，新增测试先行因模块尚不存在而失败；抽取后新15项加原摘要/元数据共43项通过。
  主将源码读取及隔离子进程指向实际导入模块，
  保证后续随包验证不回退checkout。新增八方向/代码/词典/长文快照回归后45项通过。
- 首次Windows联合115项有2失败：新fixture缺Labs显式optout marker，以及新eager import破坏
  原默认server不得加载provider的断言。修正fixture并改为仅显式translation bootstrap导入provider，
  原断言保留，随后115/17.129s通过。
- 扩展联合223/29.740s有1失败：Foundation冻结清单尚为旧9项；同步新13精确方法及拒绝skip、
  错误计数/重复/缺方法负例后，224/27.908s通过。输出中的HTTPS `BLOCKED`来自原拒绝隐式网络负例。
- 随后Windows真实Summary/Config/AtomicWrites/storage/history消费者及快照/provider/翻译协议联合
  556/26.423s通过。旧WinError5仍未定位，不以这一绿色宣称解决。
- Swift代理交付31项新协议/连接XCTest，Windows未执行；主发现Foundation新增extension误嵌套，
  已移至文件作用域并保留13方法/全部断言；后续真实编译/发现结果见下。
- 新增13个Mac process、48个core及4个Foundation；原172/410/9覆盖保留。
- 正常SIGTERM是可捕获退出：仅显式translation入口安装处理器，只置标志，不在signal handler取锁；
  原循环取消/drain、清理自有native组及双owner后非零退出。便携调度与后续三系统真实组消亡均通过。
  不承诺SIGKILL、崩溃或恶意脱离进程组的后代安全，也不声称强退回滚。
- 本轮测试遵守已有load强制streaming迁移：false磁盘配置会规范化/迁移为true；
  场景改名streaming-migration并检查真实迁移及delta，不误报为非流式业务执行。
- 首源码1562a79正常privacy/compile/full hook 1542/74.563s通过并推送，run34846653756
  实际失败于构建App之前的Swift测试：新增4个Foundation上下文先抛bundleMissing，
  没有复用原配置上下文的“未提供App则前置可选”逻辑。移除重复guard，统一复用原上下文；
  App后置仍要求CC_TRANSLATE_APP及13精确方法实际passed、零skip，没有放宽后置门槛。
  首轮原生代码已编译，31个新增Swift协议/合成连接测试实际通过；完整App/真实业务集成未运行。

**最终源码 `2b116f0731803b52d87565d1e8e4602b6794c324` /
[run34847149053](https://github.com/mclight-ship-it/cc-translate/actions/runs/34847149053)**：
实际watch exit0，API核对attempt1、三jobs全部steps success。正常第二次完整hook
**1542/74.504s OK**；第一次1542/74.563s与本次分开留存，均有原Tk teardown stderr警告，
无失败/skip，不代表旧WinError5根因已解决。最终runner门槛47/1.462s及前置上下文修复门槛
1/0.042s通过；没有重跑未变源码凑绿。

| 实际系统 | 包内process | 包内core | 后置Foundation |
|---|---:|---:|---:|
| 15.7.9 / Xcode16.4 producer | 185 / 345.212s | 458 / 5.316s | 13 / 60.220s |
| 14.8.9 / Xcode16.2 harness | 185 / 312.723s | 458 / 3.236s | 13 / 53.262s |
| 26.6.2 / Xcode26.6 harness | 185 / 327.219s | 458 / 3.741s | 13 / 54.798s |

所有包内suite零failure/error/skip。每系统新增13 process、48 core及全部13 Foundation均逐名
各发现且通过一次；producer新增31 Swift unit亦逐名通过一次。初次Swift99项含13项尚无App的
可选skip，与后置13实际执行明确分开；producer portable648/12.002s。
配置规范化/真实snapshot prompt、方向/代码/词典/summary、stream/cache/history/reopen、
当前optout、预算/坏盘/竞争、queued/started取消、EOF/shutdown/SIGTERM与丢stdout不重放均有真实合成证据。

完整App制品：
- [producer artifact10348832396](https://github.com/mclight-ship-it/cc-translate/actions/runs/34847149053/artifacts/10348832396)，
  内层`CCTranslateMac-P0.zip`为 **18,491,375 bytes**，
  SHA-256 `d778a34d7d0155120bd5683834b69b76a479813f3d5d8ad144d2b91732bef3f9`。
- 内容/模式/链接tree：`66509e0cf9fb982befbf508f488783f328e4818364e07f0e1e884de14aeb0d15`；
  14报告artifact10348922271、26报告artifact10348444972，均来自同run，不重建/重签产品。
- 主独立逐文件核验 **684库存、74资源hash、46源码路径（45唯一Git blob路径）、6真实Mach-O、
  19 runtime许可**；源字节直接对照指定Git提交，Mach-O头/最低系统/依赖/rpath与审计一致。
  排除本轮构建bridge后606条runtime文件/链接与上次已验收包相同，包含固定605原构建文件覆盖声明；
  不把这两个不同口径混作计数。HTTPS证书验证、SQLite、原诊断/取消/EOF及bundle不变/临时文件清理通过。
- 完整App临时下载及两份审计脚本已删除并核实不存在，保留小JSON/日志/逐名发现证据；
  源码与后续docs-only提交身份分开，不用文档提交替代源码CI。

下一真实外部验证见[新包最小操作交接](MACOS_DEVELOPMENT.md#native-translation-user-check)。
测试App有显式真实native调用能力，但目前仅使用合成CLI验证；不因免费路线而购买签名，
不在未获用户主动操作时安装/登录或调用其模型。

<a id="darwin-native-checkpoint"></a>

## 前置检查点：Darwin native 三项审查修复已通过同包验证（2026-09-14）

`e525c97` 文档检查点之后，独立审查发现三项确定生产缺陷；下述
`13b6543` / run34832960738 的绿色**不代表这些缺陷已修复**。
真正修复源码为 **`6d029d1b7418be2c6d7ae47c1babab550ac441b3`** /
[run34836719504](https://github.com/mclight-ship-it/cc-translate/actions/runs/34836719504)；
正常完整hook及同包三系统均通过。该前置检查点当时未接翻译IPC/UI；
后续业务链见上方当前检查点，不把两轮证据混为同一源码。

- [x] 未改生产前，三个反例实际得到 **3 tests / 0.032s / 13 failures / 4 errors**：
  空闲timer撞同模型warm版本探测后未重排；同item重复完成/终态后delta或start被接受；
  `item.type`为list/dict时TypeError逃出固定结果契约（complete/stream均覆盖）。
- [x] 空闲回收重用原scheduler，新增仅内部使用的原子generation条件；
  未取得facade操作锁时短间隔重排，旧generation/关闭/无进程不重排或误清新进程。
  Windows原无新参数调用语义不变；不阻塞timer抢操作锁，不重试模型请求。
- [x] 每次operation绑定清除item终态集合，同turn同item完成后拒绝重复完成、
  delta或start；不同item仍合法，复用进程的新operation不受旧集合污染。
  native边界明确校验item类型及agent text/phase，不靠宽泛catch吞TypeError。
- [x] 首版重排代码曾在持有非重入state lock时调用原scheduler，两个联合调用停滞后
  被终止，没有有效通过计数；随后仅在当前测试解释器内启用20秒有界栈诊断，
  实际定位到重复获取该锁及cleanup等待。未设置`PYTHONFAULTHANDLER`环境变量、
  未向GUI子进程注入诊断或安装/提权。现将generation条件移入原scheduler同一锁内，
  不嵌套取锁、不改为全局RLock；正常联合 **300 / 12.357s OK**。
- [x] 冻结后代理迟到加入的31行constructor cleanup回归已保留并纳入此次联合验证，
  不冒称包含在旧67项facade或旧403项core结果中。代理均已停止写入。
- [x] 新源码正常完整hook及同一App的15/14/26验证：保留全部旧覆盖，
  门槛提升到 **172 process / 410 core / 原9 Foundation**；
  新真实timer与版本探测屏障、原组/后代清理、item反例及不同item/reuse路径均实际执行通过。
- 原第二轮timeout阶段问题已在`13b6543`用冷预检未提交和真实预热后已提交两个回归修正；
  此次保留原3秒预算与提交/清理断言，不重复不变旧源码、不增加生产timeout或retry。
- 旧Windows `WinError5`来源未知继续保留；没有官方账号/真实模型或新的用户实机结论。

### 修复源码、完整hook与新同包制品

修复源码 **`6d029d1b7418be2c6d7ae47c1babab550ac441b3`** 已正常提交/推送；
一次正常privacy/逐文件编译/完整pre-push **1494 / 72.376s OK**，无失败或skip。
保留既有Tk teardown stderr与离线smoke拒绝负例，不混入旧1487计数；
此次没有复现WinError5，不表示旧未知拒绝来源已解决。

[run34836719504](https://github.com/mclight-ship-it/cc-translate/actions/runs/34836719504)
已实际watch到exit0并逐job/step核对：attempt1，3 jobs/33 steps全部success。
同一个15.7.9/Xcode16.4/Swift6.1.2/SDK15.5 arm64制品交给14/26，不重建或重签。

| 系统 / harness | 真实 process | 真实 bundled core | 后置 Foundation |
|---|---:|---:|---:|
| 15.7.9 / Xcode16.4 | 172 / 233.252s | 410 / 2.418s | 9 / 14.357s |
| 14.8.9 / Xcode16.2 | 172 / 252.654s | 410 / 2.622s | 9 / 13.855s |
| 26.6.2 / Xcode26.6 | 172 / 257.328s | 410 / 2.639s | 9 / 16.501s |

全部后置测试无failures/errors/skips。producer离线契约 **600 / 10.391s OK**；
打包前Swift共64项、其中9项尚无App按原约定skip，和打包后9项真实执行严格分开。
相对`13b6543`，新增7 process与7 core方法（含冻结后迟到的constructor回归）
按AST差分与实际日志逐名核对，每系统各执行一次；原9个Foundation方法也逐名各一次。
真实timer先撞正在进行的同模型版本预检，warm快速返回后不再发请求，
进程与同组后代已由idle回收并在显式shutdown之前验证消亡；不是靠最后清理凑绿。
item重复终态/迟到、非法type/phase、不同item和进程复用正向路径均由包内CLI实际执行。

- App：
  [artifact10343918198](https://github.com/mclight-ship-it/cc-translate/actions/runs/34836719504/artifacts/10343918198)；
  内层zip **18,427,383字节**，
  SHA-256 **`e48c1431a51f2a4df503cbe73d000198149e0555cb3749f75d8b8c8e092f8161`**，
  tree **`534a067a96eb03ec6f14a7980502d70ec32d9f2b93be234017aeed9d88f476c2`**。
- 同包runtime小报告：
  [14/10344359174](https://github.com/mclight-ship-it/cc-translate/actions/runs/34836719504/artifacts/10344359174) /
  [26/10344552441](https://github.com/mclight-ship-it/cc-translate/actions/runs/34836719504/artifacts/10344552441)。
- 独立核验完整zip的CRC、681库存/71资源/43源码路径（42唯一Git路径）、
  字节/模式/链接/tree、19份许可及metadata覆盖；实际解析6个Mach-O架构、minimum、
  依赖/rpath与报告逐项一致。固定producer的605保留runtime文件检查证据未冒称为本地重下载验证。
  具体临时App zip/审计脚本已删除，小JSON与真实日志保留。
- 后续仅三文档docs-only提交不替代该源码SHA、不重跑不变源码CI。
  本检查点不新增翻译helper/Swift调用、GUI或真实官方CLI账号结果；
  旧用户包`eec92a5`的实机结论不转嫁，用户当前无需重装、安装CLI或登录。

### 前一源码自动化检查点（保留，不作为上述审查修复证据）

源码 **`13b65433f9228175e6c49141ddbf7aa8d965d58a`** /
[run34832960738](https://github.com/mclight-ship-it/cc-translate/actions/runs/34832960738)
三个 jobs、33 个 steps 全部 success，attempt 1。**当前完成可调用 native 后端及真实合成
进程验证，翻译 helper/Swift API 与原生翻译 UI 尚未接入，官方 CLI/账号/模型未运行。**
下一连续切片是快照到该后端的显式翻译业务 IPC；不等待用户重新授权。
前置快照源码 `8797fc7` / 文档 `b90cd7e`
已获独立增量审查接受，不重复该源码 CI，也不等待用户再次授权。

- [x] 在已有 app-server 协议上接有界 Darwin stdio/自有组监督，覆盖提交前后取消、
  timeout/EOF/早退/关闭和同组后代清理；不以 `exec` 代称 native app-server。
- [x] 显式环境/工作目录/catalog 及生命周期，复用 native config/hook/工具边界；
  首个 Mac provider 只承诺已有安全 text 能力，未实现能力显式拒绝。
- [ ] 快照→provider→显式 helper 业务→Swift 调用链，默认诊断启动仍零用户业务 I/O；
  真实临时 home/synthetic CLI 端到端回归，不只留未调用的传输类。
- [x] 本内部后端检查点的正常 hooks、免费同包三系统、精确新增测试/来源/资源/清理；
  后续翻译 IPC 仍需独立源码、真实 Foundation 新方法及同包验证。
- 真人边界仍独立：原P0 Finder/CLI只有候选发现与版本探针；没有官方账号/真实模型验证。
  新P2 UI/完整首次TCC/IME/多屏尚未验收，旧 `eec92a5` 探针结果不迁移到新包。
  这些不阻止安全实现和合成回归；实际需要安装/登录/首次权限时集中给最少人工步骤。

以下为开发时记录，保留当时未执行/失败状态，不代替末尾第三轮真实成功证据：

- 显式 native facade 与有界传输/进程测试正在实现；新增模块尚未取得 Mac CI 结果，
  helper/Swift 翻译业务接口也尚未接线，不把后端原型称完整翻译。
- 复用 app-server 协议时发现紧耦合清理锁问题：冻结 `b90cd7e` 的实际
  `warm_up` / `stream` 方法，注入合成 cleanup 异常后，二者均传播原异常但遗留
  stream 锁；新 `finally` 回归确认异常仍传播且锁释放。没有执行真实 CLI。
- 旧共享回归首次 108 / 0.988s 通过；加显式 catalog home 用例后
  119 / 1.675s 出现 1 error（合成 catalog fixture 尚不接受新可选参数），
  补透传后 119 / 1.461s 通过；加清理锁回归后 120 / 1.555s 通过。
  这些不是完整 hook 或 Mac 新执行链证据，也不解决旧 `WinError5`。
- 再加显式环境不读 ambient/不展开路径的回归后 121 / 1.865s 通过。
  随后两个清理反例实证旧有界 stdout 探针、native config 探针在
  selector 关闭异常时跳过 process owner 关闭：分别 1 test / 1 failure /
  0.022s，以及 1 test / 2 subtest failures / 0.005s。以共享固定错误映射和
  嵌套 `finally` 修复，未改 C 组监督或 reap 顺序；合并 123 / 1.500s 通过。
  已加对应两项真实 Mac 合成组故障回归，尚未执行；不能拿 Windows mock 代验。
- 显式 home 增量曾将 Windows 旧 catalog 的 `Path.home()` 解析提前：
  新回归 1 / 0.015s / 1 failure 实际捕获此差异，已恢复无新参数时的原惰性求值与
  祖先扫描语义；仅新 Mac 显式 home 分支在该边界停止扫描。合并 124 / 1.479s 通过。
- 当前 native 后端库存：56 个有界 RPC portable、67 个 facade portable；
  36 个 RPC 真进程、35 个 native provider 真进程，加旧探针两项真实清理故障回归。
  同包 runner 保留全部旧覆盖，下限由 process 90/core 277 提升为 163/403；
  9 个 Foundation 方法不变，本内部源码检查点不声称已增加翻译 IPC。
- 主会话最终联合首次 366 / 24.464s / 30 failures / 1 error：
  打包测试的冻结 provider 文件清单尚未加入五个新源码，导致合成包缺资源并连带触发
  旧审计负例；补齐原清单及其逐文件不可变断言后，366 / 27.824s 全部通过。
  其中 smoke 的 `--allow-https` 拒绝提示是既有离线负例，不是实际联网验证。
  尚需正常完整 hook 和真实同包三系统执行，仍不调用官方 CLI/账号/模型。

首个真实 native 后端源码/CI（未验收）：

- `9ef1bb338b9a081abd83ee8116a6ce087ec99ef2` 正常提交并推送；
  privacy、编译、完整 hook **1487 / 75.891s OK**，有既有 Tk teardown stderr，
  无失败或 skip；不代表旧 `WinError5` 已解决。
- [run 34831141381](https://github.com/mclight-ship-it/cc-translate/actions/runs/34831141381)
  的 producer portable、Swift、完整 bundle 构建/审计已通过；
  包内 process **163 / 240.111s / 10 failures**。后置 Foundation/core/14/26
  没有通过，不能引用上轮绿色替代。
- 九项失败位于 FIFO 生命周期证明：已读完活跃标记后才向 kqueue 注册，
  其 readiness 代理断言没有收到事件。改以有界 nonblocking read 的实际 EOF
  为消亡条件，仍拒绝 EAGAIN 超时、不删数据/组/FD/时限断言；
  新增活跃 writer 必须 EAGAIN、关闭后必须 EOF 的正反回归，并记录 late-selector
  观测。需由下一真实 Mac run 确认，不能靠 Windows 证明 FIFO 行为。
- 另一项是参数证据索引错误：receipt 的 args 不含 executable，旧 `[5:][1::2]`
  错取 `-c`；现断言精确四元素前缀、偶数键值组与全部 `-c`，再从正确偏移核对
  每项实际安全 override 与 catalog 字节。生产参数没有改动。
  新 process 发现下限为 **164**，core 保持 **403**，所有原断言/方法保留。

第二轮真实执行：

- `fa085782068cb4440568279bc7248677c9e3e917`，正常针对性
  **76 / 10.599s OK**、完整 hook **1487 / 71.823s OK**。
  [run 34832091646](https://github.com/mclight-ship-it/cc-translate/actions/runs/34832091646)
  包内 **164 / 215.714s / 1 failure**；此前十项失败已不再出现。
  新 FIFO 正反回归实际通过，记录 `read EOF verified; late selector ready = False`，
  确认第一轮 readiness 不是可靠 EOF 代理，未修改生产组监督。
- 剩余失败是测试把整个冷启动的 3 秒 deadline 当作必然已提交 turn：
  本次正确返回 `timeout` 且 `turn_submitted=False`。现在保留同一个 3 秒 deadline、
  原时间上下界及已提交/后代清理断言，先通过真实无 turn 的 prewarm 完成控制预检，
  再计量前台调用；另加真实冷 version 超时必须不提交的独立反例。
  没有改生产 timeout、增加重试、伪造 submitted 或把旧失败改绿。
  新 process 下限 **165**，core **403**；仍须下一真实三系统执行。

### 第三轮真实源码与独立完整 App 核验

- 源码 **`13b65433f9228175e6c49141ddbf7aa8d965d58a`**，生产 native 后端仍同
  `9ef1bb3`；后两次提交修正测试证明方式并增加真实冷预检超时反例，没有增加生产重试。
- 最终 Windows 相关联合 **143 / 10.006s OK**；正常 privacy/编译/完整 pre-push
  **1487 / 69.755s OK**，无失败/skip，保留既有 Tk teardown stderr。
  前两次正常 hook 的 1487 / 75.891s 和 1487 / 71.823s 不混作此次计数。
- [run34832960738](https://github.com/mclight-ship-it/cc-translate/actions/runs/34832960738)
  已实际等待并核对 API 全部成功；前两轮失败不改写为通过。

| 系统 / harness | 真实 process | 真实 bundled core | 后置 Foundation |
|---|---:|---:|---:|
| 15.7.9 / Xcode16.4 / Swift6.1.2 / SDK15.5 | 165 / 208.235s | 403 / 2.740s | 9 / 20.595s |
| 14.8.9 / Xcode16.2 / Swift6.0.3 | 165 / 196.592s | 403 / 2.452s | 9 / 16.305s |
| 26.6.2 / Xcode26.6 / Swift6.3.3 | 165 / 227.985s | 403 / 2.609s | 9 / 14.952s |

三系统后置测试没有 failures/errors/skips。producer 离线契约 **593 / 9.383s OK**；
打包前 Swift 共64项，其中9项因尚无 App 按原约定 skip，不能拿来替代打包后实际执行的9项。
按 `b90cd7e` 与此次源码的测试 AST 差分逐名核对真实日志：
新增 **75 process + 126 core = 201 方法**在每个系统各执行一次并通过。
其中 RPC 真进程37、native facade真进程36、两项旧探针 cleanup 故障回归全部实际执行；
core 新增 RPC56、facade67、catalog3。活 writer/EAGAIN 与关闭/EOF 正反、冷 timeout 未提交、
prewarm 后 turn timeout 已提交均有真实进程证据，三系统 late-selector 观测均为 False。
9个原有 Foundation 方法保持精确集合，尚无翻译 IPC Foundation 方法。

- 完整 App：
  [artifact10343201974](https://github.com/mclight-ship-it/cc-translate/actions/runs/34832960738/artifacts/10343201974)。
  仅开发测试，不是 Release。内层 `CCTranslateMac-P0.zip` **18,426,899 字节**，
  SHA-256 **`c3dc949f980c3dcd17b84818fe11e8d9a29b7a02b4bc89b619e0f84e42c27c78`**；
  App tree **`3ea09e79056c6a6e5c7bc908524d0976a2e27a197331865dffdcfcaf4620a262`**。
- 同一 producer archive 供14/26运行，不重建/重签；小报告分别
  [10343047142](https://github.com/mclight-ship-it/cc-translate/actions/runs/34832960738/artifacts/10343047142) /
  [10343815837](https://github.com/mclight-ship-it/cc-translate/actions/runs/34832960738/artifacts/10343815837)。
  实际 producer 为15.7.9 build24G830、arm64、Xcode16.4 build16F6。
- 独立读取完整 zip：CRC、**681库存/71资源/43源码路径（42唯一Git路径）**的字节、
  模式/链接/tree、19份runtime许可及metadata覆盖均核对；实际解析6个Mach-O的架构、
  最低系统/依赖/rpath，不仅相信审计报告。605个保留runtime文件匹配完整build的证据
  来自固定 producer 构建检查，本轮未重复下载原始完整运行时归档。
- 会话临时审计器首次把许可覆盖复算子集与含额外605计数字段的报告整对象比较而失败；
  按真实 schema 分别保留子集相等及605精确断言后通过，不改产品制品或降低产品审计。
  已删除具体临时 App zip/审计脚本，仅保留小JSON与真实日志；未运行 Mac 二进制于 Windows。
- 源码和后续三文档 docs-only 提交身份分离，文档提交不触发等价源码 CI。
  **旧 Windows WinError5 拒绝来源仍未知；当前绿色不表示已解决。**
  合成 CLI 证明协议/自有进程/安全参数与清理，不证明官方账号或真实模型可用，
  也不继承旧 `eec92a5` 的用户首测结果。当前不要求用户重装、安装 CLI 或登录。

<a id="request-snapshot-checkpoint"></a>

## 请求快照检查点已验证（2026-09-14）

基线源码 `4021270362418c0876dfd7aa51c4c697694f3758`、文档 `ae04dc1`；
历史/config 业务链已验收，不重复旧源码 CI。按用户授权继续到真正人工前置，
不将付费签名资格或未完成全部真人矩阵当作纯工程开发的停止条件。

- [x] 复用 frozen `ProviderRequest`，冻结执行配置副本、输入、prompt、provider/model、
  cache 签名、方向/任务元数据；实际接 Windows 请求启动与执行消费者。
- [x] 覆盖主翻译、OCR vision、词典补充和结果追加动作；取消/UI session 独立，
  `_record_history` 仍检查当前 job、当前 history 开关和上限。
- [x] 无 I/O 共享契约、嵌套变更隔离、Windows 实际消费者/字节和路由回归；
  包内同源模块/严格测试发现、正常 hooks、15 producer → 14/26 同包验证。
- [x] 源码与随后 docs-only 验收记录分离，精确测试/制品证据；源码已通知协调方做增量审查。
- 后续按依赖逐个接 Darwin provider 执行/streaming/取消，再接原生显式翻译交互；
  本检查点不声称已调用官方 CLI/真实模型或实现完整翻译 UI。
- 旧历史 `WinError5` 拒绝来源仍未知；不加生产重试、不绕过 hook、不以本轮成功掩盖旧失败。

本轮执行记录（失败保留，不代替后续真实 Mac provider 业务验证）：

- 共享快照初版首次 **30 / 0.122s / 1 failure，0 errors**：
  导入探针无条件禁止 `os.listdir`，误拦截 Python `_fill_cache` 的导入目录枚举，
  子进程退出码断言失败。仅修正测试边界，继续禁止用户目录/写盘/网络/平台依赖；
  随后 **30 / 0.166s，OK**，补历史只读元数据后 **34 / 0.181s，OK**。
  再增加错误中不回显 caller key 的合成用例，纯契约现为35项。
- 第一次 Windows 联合 **201 tests / 1.979s / 14 failures + 1 error**：
  旧 metadata 精确期望未含新增 snapshot/独立 stream session，14 个 subtests 不符；
  词典启动 fixture 把 selection mock 成字符串，不能构造真实 ProviderSelection。
  两处更新为真实契约，保留全部旧字段/字节/调用次数断言并新增完整快照比较，不删测试。
- 第二次联合 **357 tests / 12.065s，OK**，包括完整新快照/真实 Windows 消费者及打包清单。
- 继续追踪 warm 真消费者，新增两反例先实际得到 **2 tests / 0.028s / 2 failures**：
  原 model/direction key 无法区分另一语言的预热 prompt，且会重新读当前配置。
  已改为显式快照路径还须匹配实际 prompt，不再重算当前配置；错配候选没有发送 query，
  只在提交前走原冷路径。旧 key/cache 版本不改，不新增已提交模型请求重试。
- OCR vision 仍在原 UI 回调位置写历史，只把输入/签名换为捕获值；不将写盘提前到 worker。
  图像路径指向现有逐请求 UUID 自有临时文件，不声称纯快照冻结了外部文件系统。
- 包含 warm 修复与 Claude vision 新 API 回归的最终针对性联合：
  **360 tests / 12.400s，OK**；35 项纯快照、16 项新 Windows 接线测试，
  既有 routing/stream/one-shot/历史当前策略/结果动作/词典/打包断言均保留。
- 一次正常源码推送：privacy/逐文件编译通过，完整 hook **1357 tests / 71.305s，OK**，
  无失败/skip；保留既有 Tk teardown stderr。此次未发生 WinError5，不等于旧拒绝来源已解决。

### 真实源码/同包三系统

源码 **`8797fc7acbc9de9fad05d47f394589c3305da2ba`**；
[run34823367426](https://github.com/mclight-ship-it/cc-translate/actions/runs/34823367426)，
attempt 1，三个 jobs、33 个 steps 全部 success。源码之后的三文档验收提交不更换该制品身份，
不对仅文档变化重跑相同源码 CI。

| 系统 / harness | 真实 process | 真实 bundled core | 后置 Foundation |
|---|---:|---:|---:|
| 15.7.9 / Xcode16.4 / Swift6.1.2 / SDK15.5 | 90 / 71.373s | 277 / 2.186s | 9 / 17.603s |
| 14.8.9 / Xcode16.2 / Swift6.0.3 / SDK15.2 | 90 / 67.132s | 277 / 1.821s | 9 / 13.907s |
| 26.6.2 / Xcode26.6 / Swift6.3.3 / SDK26.5 | 90 / 72.849s | 277 / 2.823s | 9 / 18.658s |

全部 arm64；镜像依次为 `20260907.0337.1`、`20260831.0302.1`、`20260907.0351.1`。
产品只由15/Xcode16.4构建，另外两系统只编译独立harness，不重建/重签被测App。
三系统均逐项核对全部35个新增快照方法各执行一次且ok，core/process/后置Foundation零fail/error/skip。
producer普通portable **465 / 13.639s，OK**；普通Swift **64 = 55 passed + 9 初始无包skip**，
不能把初始skip当集成通过，后置精确9方法才是包内Foundation证据。
HTTPS证书验证、SQLite读写、显式临时storage、helper取消/EOF、文件清理和App不可变均通过。

- [App artifact10339228273](https://github.com/mclight-ship-it/cc-translate/actions/runs/34823367426/artifacts/10339228273)
- [14报告10338878137](https://github.com/mclight-ship-it/cc-translate/actions/runs/34823367426/artifacts/10338878137)
- [26报告10339104167](https://github.com/mclight-ship-it/cc-translate/actions/runs/34823367426/artifacts/10339104167)
- 内层zip **18,400,071 bytes**，SHA-256
  **`836cac429e9f7e1b63fec42e731460d31dd7a8ff0fc63f0067abd8f231fc8ad9`**。
- 内容/模式/相对链接 tree：
  **`f0ad33650e18c1ce0a9c225807d7516a51742349ccc7226eebad6e20183806d8`**。
- 本地只读完整zip审计：**675库存 / 65资源hash / 37源码路径（36唯一Git blob）/ 6 arm64 Mach-O /
  19 runtime许可**；逐项对固定源码blob与实际Mach-O load commands核验，源码clean/lock匹配。
  producer本run固定runtime/full-build的605保留文件匹配，包内许可内容/metadata覆盖再核对。
  两runtime complete/unchanged和同archive/tree成立，临时zip及审计脚本已删除，仅保留去敏小证据。
- 本包仍为免费开发探针，不是完整翻译产品；不继承旧 `eec92a5` 用户实测，
  不宣称Finder/TCC/IME/多屏/Intel/正式签名验证。下一依赖为Darwin provider执行/streaming/取消，
  当前不要求用户重装、安装CLI或登录。

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

CI 先运行普通 Swift 测试，此时包尚不存在，需要包的集成测试会明确 skip；
构建 `.app` 后再设置 `CC_TRANSLATE_APP`，用 `--filter HelperIntegrationTests` 真正执行
Foundation.Process→包内 helper。当前门槛为精确五个方法：原 fixture/SQLite/关闭，
以及配置读保存重开、坏文件保护、owner 竞争接管、保存/迁移可读性预算；逐项名称与数量都校验，拒绝 skip。
首轮历史中的单项计数保持原样；初次 skip 不能计为集成通过。
Python HTTPS smoke 是另一步，不替代原生客户端链路。

首次 Mac 编译/自动化已覆盖这些此前仅静态检查的 API/生命周期边界：
`SCScreenshotManager.captureImage` / `SCShareableContent` 的 async 导入，
`MainActor.assumeIsolated` 与 OCR 任务的并发诊断，Carbon/AX 的 CF 桥接，
`F_SETNOSIGPIPE`、DispatchSourceRead 和关闭管道的顺序。
实际编译通过；真实事件/TCC/焦点行为仍须实机，不凭自动化结果扩大 P2–P6。

首轮 CI 当时已运行 macOS 编译和 CI；尚未运行真实用户 GUI/TCC、Developer ID 签名、公证或发布。
上述证据只解锁依赖安全、可独立回归的 P1 纯核心。

## P1 — 已完成切片见下；其余依赖继续待办，完整 P1 未完成

Mac 编译/XCTest/原生包内 IPC/Mach-O/HTTPS/SQLite 及Codex翻译链已验证，现继续依赖已满足的P2界面。
自动化不代表完整P0首开/TCC矩阵通过；这些未测项单独保留，不再作为所有P2实现的总阻断。
当前采用免费 GitHub 分发路线，Developer ID/公证为未选择的可选增强，不是 P1 或免费首测的强制准入。
- [x] Mac平台路径，Application Support/Caches 分工，业务配置/历史单一写入者；
  以最新业务IPC检查点为准，下面保留各依赖最初验证时的范围。
  - [x] 路径/原子基础层：显式 Mac home/应用身份解析（不创建/迁移），共享 JSON 原子写入；
    源码 `84ab360` / [run 34764132000](https://github.com/mclight-ship-it/cc-translate/actions/runs/34764132000)
    通过，见[基础层证据](#存储基础可靠检查点2026-09-13)。
    Windows 原入口/默认路径/日志不变，Mac 仅临时合成诊断；不是唯一 writer 或迁移服务。
  - [x] 共享历史仓库与 Windows 兼容入口、add/clear 统一锁、Mac 显式跨进程 owner；
    源码 `c78d8ee` / [run 34768088072](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072) 三系统通过，
    详见末尾历史仓库证据。仅历史 I/O，不等于整个配置/历史服务或业务 helper 接线。
  - [x] 无 I/O 配置默认/Config 与 raw 迁移计划共享，Windows 真实入口接线；
    源码 `7770b70` / [run 34801568838](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838)
    同包三系统通过，正常完整 Windows hook 1194 项通过，见[配置规则证据](#配置规则自动化检查点)。
    该次针对性运行出现 WinError 5，失败保留；该次只完成规则依赖，后续配置 owner 见下。
  - [ ] Windows 历史矩阵偶发原子替换拒绝访问的根因：2026-09-14 二十轮复核已复现，
    不是全部通过；新旧历史路径的单次 `os.replace` 均观察到 WinError 5。
    见[复核与阻断记录](#历史矩阵二十轮复核2026-09-14)，未用重试或削弱断言规避。
    后续[实际旧 writer 对照](#抽取前后实际-writer-有界对照2026-09-14)也复现，不能归因于未关闭 FD，
    但拒绝来源仍未知，不标解决。
  - [x] Mac 配置仓库/owner 服务：显式 home + 应用身份选择
    Application Support，严格读取/raw 迁移/独立保存快照；与历史共用稳定侧文件所有权。
    源码 `0fd56c2` / [run 34803920265](https://github.com/mclight-ship-it/cc-translate/actions/runs/34803920265)
    同包三系统通过，见[配置 owner 证据](#config-owner-checkpoint)。
    该历史检查点尚未接业务 helper；不改变 Windows 配置入口或后台共享 cfg 策略。
  - [x] 配置业务私有 helper + Swift 可调用 API，含独立review后的保存/迁移可读性修复：
    源码 `c459652` / [run34809961745](https://github.com/mclight-ship-it/cc-translate/actions/runs/34809961745)
    同包三系统76进程/218核心/5Foundation通过，正常Windows hook1280通过，
    见[业务 IPC 证据与历史失败](#configuration-ipc-checkpoint)。
    旧9614eab绿色未覆盖完整性缺陷；4d769e7的历史WinError5推送失败仍保留，不称拒绝来源已解决。
    该配置检查点未接设置 UI、历史业务或完整请求快照，不是全 App 配置线程安全完成。
  - [x] 历史业务私有 helper/Swift API：同一显式连接持有双owner，
    有界revision分页/记录/清空，保留配置与默认诊断。含独立review的worker未启动终态修复，
    源码`4021270` / [run34817356816](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816)
    同包三系统90进程/242核心/9精确Foundation通过，正常Windows hook1306通过；
    两轮真实Mac失败及修复均保留，见[本轮依赖与证据](#history-ipc-checkpoint)。
    旧dc0ba9c绿灯未覆盖worker_start_failed跨端缺陷，不能作为该分支的通过证据。
    不是历史UI、自动模型记录、请求快照或整个P1完成。
- [ ] 抽取分类/方向/提示词、请求快照、缓存签名与词典结构；保留 Windows 兼容入口。
  - [x] 完整执行快照共享契约与Windows实际请求入口接线，源码`8797fc7`；
    同包三系统277核心（新增35）、90进程、9精确Foundation验证，详见本页当前检查点。
    不代表全部词典格式/平台提示词调用方或Mac provider执行已迁移。
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
- [x] Codex native配置/目录/生命周期及真实版本/预热；本轮已有用户翻译成功反馈。
  不做exec假兼容；全部版本/账号仍不作通用保证，下面保留历史依赖范围。
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

## P2 — 正在实施正式原生主流程
- [ ] 双击 Cmd+C 关联状态机及保守复制回退；不吞复制、不哨兵、不读历史；[已交付待原生验收](#native-associated-copy-checkpoint)。
- [x] 原生结果/输入、流式合并刷新、选择滚动、取消、迟到事件隔离；同包自动化见上。
- [x] 普通翻译/代码解释/摘要/重译/复制及六种追加动作闭环。
- [x] 本地词典优先首屏及无Codex查词/安装管理闭环；b86507f同包三系统及原生下载/绘制证据见上。
- [ ] 真人IME、键盘、VoiceOver与多屏交互矩阵，不将合成渲染当完整交互签收。
- [ ] 来源按钮稳定 identity，按下时异步更新不吞 click；[原生接线与验收进度](#native-dictionary-sources-checkpoint)。

## P3 — 基础设置/历史随P2接线，其余功能继续待办
- [x] 同帧区域截图、多显示器坐标转换、本地Vision及明确OCR文字翻译；082aad6同包验证见截图检查点，真人多屏/TCC仍另列待验。
- [ ] 真正图片provider及图片明确发送已实现；[同包三系统验收进行中](#native-image-translation-checkpoint)，不以OCR文字翻译或构包前skip冒充完成。
- [x] 本地词典URLSession下载；核心校验安装/删除互斥；离线/损坏/取消合成与实际下载验证。
- [x] 分页历史/全库搜索筛选、基础设置/主题/语言与独立诊断。
- [x] 原生关于与完整第三方许可界面；d699185同包三系统及真实包读取验证见关于检查点。
- [x] 手动自定义模型设置、精确保存/重开/请求及中英混合OCR改进；b33515d同包验证见模型设置检查点。
- [x] 明确刷新Codex模型目录、设置/主窗口/Capture共享选择、空/失败不阻断手填ID；d637bea同包三系统验证见模型目录检查点。
- [ ] 完整设置/模型管理；[原生文字大小已接线，待原生验收](#native-text-scale-checkpoint)。
- [x] 主动纯文本粘贴、原生设置/独占快捷键/本应用编辑命令；2f371fa同包三系统及46项私有剪贴板/生命周期验证见上。
- [ ] 真实外部编辑器、多格式/跨设备Universal Clipboard、访问ask/allow/deny及更新后权限保持验收；不以合成数据代替。

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

### 抽取前后实际 writer 有界对照（2026-09-14）

补足上次“旧 history 仍使用当前共享 writer”的证据缺口，只做本地合成诊断，不改变生产或原矩阵。

- 测试 HEAD **242e29103903f27587825e3b6287e36178c749e2**，当前 `cc_storage.py` blob
  `266c1d0100cdb4f779a9586aedec01b9dad3e398`。
  从 **84ab360^ = 5ecc98770742b7f387e9dfa9d8e341ef153407e9** 的 `translator.pyw`
  （blob `b841723087550b3a6a977883bdd302299e084d6e`）仅提取实际 `_atomic_write_json` FunctionDef，
  原 AST 编译执行，注入其原有 os/tempfile/json/Any 依赖；不导入旧 UI/`cc_core`，不改函数体或 FD 流程。
  冻结函数源码 SHA-256 `2b131f241c48e46d6a6b2d4917850ebbc6ec334779846b472c9707dd66968b4c`，
  无位置属性 AST SHA-256 `5edf01d6bc590e474b71fb66566d074d3aa8ee9a4f56f5c0f49718970a605c1b`。
- 继续用现有 Python **3.12.10 / AMD64 unittest runner**，会话外诊断 TestCase；
  复用原矩阵 224 组合的循环规模，但每次都传**同一个固定合成 payload**（719 字节、含 Unicode/换行）。
  每轮两个 writer 共享同一临时目录内同一个 `history.json`，每对交错旧→新/新→旧顺序，
  各自仍创建自己的唯一临时 JSON。20 轮都完整执行，失败后不提前结束或重试。
  payload 字节 SHA-256 `488e56ae347a4ec6f662c921fffe1ef74e3f6b2ac17326f5880b0095a9ee0089`。
  这是专门的 writer 对照，不将它冒称原 89 项套件再次通过。

| 实际执行 | 旧 `_atomic_write_json` | 当前 `cc_storage.atomic_write_json` |
|---|---|---|
| 轮次 / 真 writer 调用 / 真 `os.replace` 调用 | 20 / 4,480 / 4,480 | 20 / 4,480 / 4,480 |
| 实际 `PermissionError` / errno 13 / WinError 5 | 1 次：round 4 / pair 53 | 2 次：round 6 / pair 146、round 19 / pair 43 |
| replace 前 stream.closed / 原 FD 的 fstat | 全部 true / EBADF(9) | 全部 true / EBADF(9) |
| 清理及目标字节检查 | 全部通过 | 全部通过 |

- unittest 总计 **20 tests / 49.992s，3 failures，0 errors/skip，exit 1**；
  总计 **8,960 次**实际调用和逐次轨迹，不是 8,960 个 unittest。
  在此之前仅做旧/新各一次成功的合成 profile 能力探针，不计入以上固定轮次。
- 不 mock/wrap `os.replace`，不替换 os.close/fdopen；通过 `sys.setprofile` 观察原 C 调用/返回。
  旧路径实际顺序：flush → fsync → `__exit__` 返回 → replace；
  当前路径：flush → fsync → `__exit__` 返回 → 显式 os.close 返回 → replace。
  每次 replace 入口只调用 fstat 观察原 fd，得到 EBADF，未替它打开/关闭描述符；
  三次失败也具有该完整证据，**不是“新 writer 到 replace 时尚未 close”**。
  profile 有观察开销，不能用这些少量错误数量推断哪种实现更可靠或故障发生率。
- 两实现成功后与失败后目标都仍等于固定 payload、无本操作临时文件残留；
  因前后 payload 相同，这只说明目标完整且错误后的清理符合观察，
  **不能把失败写入算成功，也不据此判定该次 replace 已提交**。所有真实异常均作为失败保留。
- **结论仅为：此现象可跨 84ab360 的 writer 抽取重现。** 旧函数原样执行也失败，
  目前没有确定的项目 FD/生命周期缺陷可修；不推断系统/杀毒软件根因，不排除尚未定位的问题，
  不删除或改绿前一轮失败。拒绝来源仍未定位，根因待办继续未完成。
  本次到非侵入式合成进程观察边界为止；不安装主机监控工具、不提权、不采集全机路径，
  不开展未经授权的进一步监控，也不新增生产重试、配置代码或其它功能。
- 固定源码/AST、payload/hash、20 份逐调用 JSONL、完整 runner 日志、三次异常栈和汇总保留在本地会话证据；
  测试临时目录及执行脚本清理，公开文档不带原始主机路径或用户数据。
  本次只文档正常 hooks 提交/push，源码不变，不新增 Mac CI 或制品；
  三系统源码仍为 `c78d8ee` / [run 34768088072](https://github.com/mclight-ship-it/cc-translate/actions/runs/34768088072)，
  旧绿色工程证据不覆盖本次 Windows 失败。

### 下一配置持久化候选（只读调查，尚未开放实施）

以下为前次只读建议。2026-09-14 新授权仅开放无 I/O 配置规则/默认与迁移计划共享，
不是下述配置 owner、线程锁或迁移文件服务；实施与验收记录另列于末尾。

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

### 无 I/O 配置规则切片（2026-09-14）

1. [x] 单一 `cc_config` 常量/Config/迁移计划来源，Windows 真实 Config/load_config 接线，
   保留 dict 子类、字段/对象身份、原异常范围、内存与 raw 磁盘写回区别和最多一次 save。
2. [x] 冻结旧实现/AST 差分、类型/边界/marker/未知键/失败及真实 Windows 消费者回归已接入并实际执行；
   同源模块与测试进入包内 isolated Python，验证零环境/用户文件/平台导入副作用。
3. [x] 正常针对性/完整 hooks、免费同包 15/14/26 CI 已执行；以下分别记录失败与成功，不混称全绿。

WinError 5 拒绝来源仍未定位，前述原 writer 对照失败保留；不加生产重试或改绿旧矩阵。
仅纯规则可以继续独立验证，不等于配置持久化、线程安全、Mac config owner 或业务 helper 已完成；
不扩 runtime JSON、UI、全局锁、后台 cfg 竞态、RequestSnapshot/provider。

- 首次联合针对性 **215 tests / 13.171s，4 failures**：包内 runner 负例中四个断言仍写旧 128，
  实际新增 19 项纯配置规则后计数为 147。已同步负例的完整计数（包括 failure/error/skip 与 fixture 失败），
  不删除负例、不改失败判定、不降低下限；这次失败不是 WinError 5。修正后结果另记。
- 修正计数后的同组联合 **215 tests / 12.376s，82 failures**：新 Windows 实际磁盘差分矩阵
  出现 `save_config` 的 `PermissionError / WinError 5`，首个原始磁盘 payload 字节断言失败，
  后续子用例仍看到保留的同一错误日志并继续失败；82 是断言失败数，不等于 82 次独立写入异常。
  本次不是“targeted 全绿”，拒绝来源仍未知，不猜测与旧现象具有同一个外部原因。
  已保留完整日志，不换目录、不清日志规避、不削弱断言或重复运行直到成功。
  新纯规则及修正后的 runner 负例执行正常；后续仅按原授权尝试正常完整 hooks，结果另记。

### 配置规则自动化检查点

- 源码 **7770b704f05890b60b734a3dc0652f674847c960**。原 `CFG`、`DEFAULT_CONFIG`、整个
  `Config` 类（包括 `_coerce`、typed accessors）与抽取前 AST 相同；不是复制两份默认目录。
  Windows `cc_core`/入口导出相同对象，公开 Config factory/save/日志/atomic writer seam 保留。
  迁移计划显式收 raw 与已构造 cfg，只进行原五段迁移语句，最多写一次 raw 副本；
  缺失语言键、字段顺序/未知嵌套对象身份、marker“缺失”而非真假判断、旧 model/provider 同步、
  mini→auto-fast、原 TypeError/ValueError 捕获与 OverflowError 传播不变。
- 新纯规则 **19 项**，包含 **468 组**类型差分与 **99 组**内存/raw 计划对照；
  原 class/constants AST 固定指纹、冻结旧 load 函数、计划语句 AST 同步验证。
  新 Windows **10 项**真实入口/字节/写入次数/失败/patch seam 回归；
  零副作用测试在 isolated 子进程禁止环境访问、平台/provider 导入、用户文件访问和写入。
  共享模块仅增加一个必需资源，不增加 IPC/runtime JSON、文件 owner 或用户操作。
- 上述两次 targeted 失败完整保留；之后**仅一次正常完整 pre-push**，
  privacy/编译及 **1194 tests，OK，67.382s**，无 failure/skip，正常允许 push。
  包含新 29 项、原 ConfigPersistence/ConfigWrapper/AtomicWrites/存储/历史/全 Windows 消费者。
  这不是重复 targeted 直到通过，也不能将该次完整成功解释为 WinError 5 根因已解决。
- [run 34801568838](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838)
  **attempt 1，success**，三 jobs 的全部 steps success；未重跑等价源码。

| 实际 job / 工具链 | 便携/普通 Swift | 包内进程 | 包内核心 | 强制 Foundation 集成 |
|---|---|---|---|---|
| [producer 103845130717](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838/job/103845130717)，15.7.9 / 24G830 arm64，Xcode16.4 / 16F6，SDK15.5 | **318 / 6.953s**；Swift **35总数=34pass+初次skip1 / 9.316s** | **44 / 57.171s** | **147 / 1.095s** | **1 / 2.653s** |
| [runtime 103845610219](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838/job/103845610219)，14.8.9 / 23J631 arm64，harness Xcode16.2 / 16C5032a，SDK15.2 | 不重建产品 | **44 / 57.712s** | **147 / 1.374s** | **1 / 3.120s** |
| [runtime 103845610264](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838/job/103845610264)，26.6.2 / 25G83 arm64，harness Xcode26.6 / 17F113，SDK26.5 | 不重建产品 | **44 / 53.502s** | **147 / 0.955s** | **1 / 2.428s** |

producer/runtime26 image `20260907.0337.1` / `20260907.0351.1`，runtime14 image `20260831.0302.1`。
三系统日志中新增 **19 项 ConfigRuleTests** 均逐项 `ok`，名称集合一致，非空发现且全部实际执行；
包内/后置均 **0 failures/errors/skips**。原 44 项 synthetic 进程、历史/存储显式临时入口、
HTTPS/SQLite/取消/EOF/资源审计及 App 不可变均通过；不把 producer 初次集成 skip 算通过。

#### 配置规则制品与限制

- [唯一 App artifact 10331488395](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838/artifacts/10331488395)，
  API 核验有效，到期 **2026-09-21T03:11:31Z**。内层 `CCTranslateMac-P0.zip`
  **18,366,347 字节**，SHA-256
  **d92109ec4d51d90590294034474bdd775099b5cf59ad9b7caeae7b4ebd2a6fc9**。
  外层 artifact digest `ccb3078f78070f2a34d06535a9fbe4479ac4f83047ce94b2632a3ba4a30b4094`
  与内层 zip hash 分开，不混用。
- 小报告 [Mac14 10331103518](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838/artifacts/10331103518)
  **2,393 字节**，到期 2026-09-21T03:13:47Z；
  [Mac26 10331965382](https://github.com/mclight-ship-it/cc-translate/actions/runs/34801568838/artifacts/10331965382)
  **2,395 字节**，到期 2026-09-21T03:13:34Z。同 run/commit 的精确 producer 制品，
  runtime 不重建/重签、不回退宿主 Python；没有重复上传整份 App。
- 独立下载核验 **668 库存 / 58 资源 hash / 30 同源 Git blobs / 6 arm64 Mach-O / 19 runtime 许可证**，
  新 `cc_config.py` 与固定源码及 manifest 必需资源一致；许可锁的必需子集为 10，实际保留 19 份，
  原 full-build **605 文件**覆盖记录不变。应用自身许可仍沿用 manifest 的
  `requires separate confirmation`，不把第三方许可等同项目授权或正式发布通过。
  ZIP CRC/0755/所有相对链接、字节和清单审计通过；三系统及下载归档摘要均为
  **e0598c8a65ed70ffa96cd310a36d104f8e2d4b0fc06f3ecc73b40fd6bc5cb529**。
- 本地保留逐项日志、三个系统 JSON 和独立审计；下载 zip/临时审计脚本清理。
  最终三份文档用 docs-only 正常 hooks 提交/push，最终文档 HEAD 与上述源码 SHA 分开报告，
  不因文档重新运行未变源码 CI。

**仅无 I/O 配置规则依赖完成。** Windows 写入偶发拒绝访问仍为显著未解决项，
本轮 targeted 的失败没有删除或改绿；完整 hook/三系统通过也不保证下一次写入不会失败。
Mac 配置 owner/迁移文件服务/全局配置锁、后台 cfg 竞态、业务 helper、完整 RequestSnapshot/provider/UI
均不在本次内，整个 P0/P1/P2–P6 未完成。当前新包只有自动化证据，旧 `eec92a5` 用户报告不迁移；
免费分发/零预算及真实 TCC/干净首开/CLI账号/Intel 边界不变，不要求现在重装或登录。

<a id="config-owner-checkpoint"></a>

### Mac 配置 owner 服务检查点（2026-09-14）

1. [x] 从已验证历史 owner 提取稳定侧文件所有权原语，history/config 共用；
   保持旧历史 API、`.lock` 名称、非阻塞竞争、fork 子不 unlock 父、失败 FD 清理与 close 串行。
2. [x] 显式路径配置仓库和 Mac owner：load 的读取/规范化/raw 迁移/原子写入共用同一操作锁；
   仅确实缺失返回默认且不创建配置文件，其它读取/格式/转换或迁移失败显式，不覆写损坏输入。
   save 明确写独立 JSON payload，不保存调用者可变引用；load 不泄漏内部状态。
3. [x] 真实临时 Application Support/bundle ID fixture，第二进程竞争/替换后稳定 inode/
   正常退出/崩溃接管、fork/close/故障保护；新增便携与真实进程测试并保留原历史覆盖。
4. [x] 正常联合 targeted、一次完整 hooks、免费同包 15/14/26 CI 与资源/许可/不可变核验；
   WinError5 失败原样记录，不循环凑绿或跳过 hooks。

当前只实现可调用服务依赖，不接 Swift/业务 helper 协议或按钮，不修 Windows 后台共享 cfg 竞态，
不加全局配置锁/请求快照/provider。调用方必须显式选择路径并管理 owner 生命周期；
协作式文件锁不等于全 App 状态快照或恶意篡改防护，Windows 拒绝访问根因仍未知。

本轮接口选择：`MacConfigOwner(home, application_id)` 使用显式绝对 home 和调用方给定的
已验证 bundle ID，通过已有纯路径模块选择 `Library/Application Support/<id>/config.json`。
应用数据目录由调用方先创建；owner 不自动 mkdir 或查 HOME，也不探测旧 Windows 目录。
获取 owner 会创建/打开稳定 `config.json.lock`，缺失配置的 load 不创建 `config.json`。
关闭与操作共用 RLock；fork 子必须在尝试该锁之前拒绝，不 unlock 父的侧文件。

配置转换仍是单份规则：Windows `Config._coerce` 保持旧容错策略；新服务显式采用 strict 模式，
转换的 TypeError/ValueError/OverflowError 不变成默认配置后落盘。已定义的合法数字/字符串/
布尔字符串规则不增加范围校验；strict 下无法转换的布尔对象同样拒绝。
旧 `_coerce` 在测试中按原源码冻结，恢复原类 AST 后仍须匹配原始指纹，再与 Windows 当前类差分；
不是将新 helper 当旧实现对照。共享转换和原历史 owner 首次针对性验证
52 项 / 1.294s / OK；此结果不代替新配置服务/真实进程/完整 hooks 或三系统验证。

首次联合 Windows targeted：337 项 / 19.061s，1 failure。
`test_storage_windows` 中另一个旧 Config AST 检查尚未采用已冻结的原 `_coerce` 重建，
仍直接比较新委托方法所在的类，因此指纹不同。修复仅复用同一 `legacy_class`；
原始 hash 不改、不删除断言，真实 Windows 差分继续运行。本次没有观察到 WinError5，
但旧的拒绝访问失败及未知根因仍保留，不能以本次无复现代替解决。
修复后仅复跑这个失败 selector：1 项 / 0.045s / OK。其余 336 项已有真实通过结果，
随后仅一次正常完整 pre-push：privacy/compile 成功，**1245 项 / 62.823s / OK**，允许正常推送。
保留既有 Tk teardown stderr；没有增加生产重试、换目录或跳 hooks。本轮没有观察到自然 WinError5，
不等于旧的二十轮/旧新 writer/配置 targeted 拒绝访问根因已解决。

#### 源码、运行与制品

- 源码 **0fd56c2d9f3630d03b078ad62e66e998fbc3419e**，
  [run 34803920265](https://github.com/mclight-ship-it/cc-translate/actions/runs/34803920265)
  attempt 1，3 jobs 和全部关键 steps success；没有重跑失败 Mac job 或修改平台声明。
- 本检查点随后的提交仅收尾本 TODO、开发指南和 ROADMAP，文档提交不是新制品源码；
  精确文档 HEAD 以这些文件的 Git 历史及交接报告为准，不把 docs-only SHA 绑定旧 App。
- 唯一 App [artifact 10332347322](https://github.com/mclight-ship-it/cc-translate/actions/runs/34803920265/artifacts/10332347322)，
  `macos-arm64-p0-development-NOT-A-RELEASE`，到期 **2026-09-21T03:52:53Z**。
  内层 `CCTranslateMac-P0.zip` **18,370,423 字节**；
  SHA-256 **21a37524448e129928540d5b76a32e934fa4e472117a19aecdd087d69a910987**。
- Mac14 [小报告 10333065999](https://github.com/mclight-ship-it/cc-translate/actions/runs/34803920265/artifacts/10333065999)
  2,389 字节；Mac26 [小报告 10332651126](https://github.com/mclight-ship-it/cc-translate/actions/runs/34803920265/artifacts/10332651126)
  2,391 字节；没有重复上传第二/第三份 App。
- 独立下载核验归档 CRC、0755、相对 symlink、全部 **672 库存/62 资源 hash**，
  **34 个包内源码路径与固定 Git blob 相等**（33 个不同 Git 路径，launch 有两个包内目标）、
  **6 arm64 Mach-O / 19 实际 runtime 许可**，完整构建保留文件核对数 **605**。
  锁文件必需许可子集仍为10，不与实际19混淆；项目自身许可仍标 `requires separate confirmation`。
  首次本地审计脚本误将源码映射去重后与34比较，已改为逐包内路径计数，不放宽字节/hash断言。
- 三系统同包内容/模式/链接摘要均为
  **a835a9c9489ce93e81dd186e638258c6a316dde6197d8053e819d1c8e164c8c9**；
  只有 Mac15/Xcode16.4 构建产品，14/26 不重建/重签，前后 App 不可变。

| 实际系统/构建 | 产品或 harness | 进程 63 | 核心 183 | 后置 Foundation 1 |
|---|---|---:|---:|---:|
| 15.7.9 / 24G830 / arm64 | producer Xcode16.4 / Swift6.1.2 / SDK15.5 | 58.914s | 1.525s | 2.565s |
| 14.8.9 / 23J631 / arm64 | harness Xcode16.2 / Swift6.0.3 / SDK15.2 | 55.463s | 1.180s | 2.596s |
| 26.6.2 / 25G83 / arm64 | harness Xcode26.6 / Swift6.3.3 / SDK26.5 | 55.304s | 1.353s | 2.393s |

实际 ImageVersion 分别为15=`20260907.0337.1`、14=`20260831.0302.1`、
26=`20260907.0351.1`；producer宿主 Python3.14.7，不与被测包内 Python3.12.14 混用。
producer便携 **369 / 10.953s / OK**；普通Swift **35总数=34通过+包未构建时集成skip1 / 9.780s**。
该 skip 不算集成通过：构建后强制 Foundation 实际1项通过，14/26各自独立编译 harness 后同样实际1项。
三系统包内核心/进程/后置集成均0 failure/error/skip。

逐项日志另核对每个系统的新增 **36 ConfigRepository + 19 ConfigOwnerProcess** 名称集合与源码一致；
旧 **19 HistoryOwnerProcess + 19 ConfigRuleTests** 同样全部逐项 ok，不因共享锁/转换抽取减少旧覆盖。
原44进程/147核心都保留，新下限为63/183；共享测试 harness 也必须来自同一 checkout，不计为空测试凑数。
配置 fixture 使用实际 Info.plist bundle ID 和临时 Unicode/空格/#/% home，真实 save/load/迁移一次/重开；
第二 owner、replace 稳定侧 inode、退出/崩溃后 reap 接管、兄弟存活、fork/close/FD/真实权限拒绝/
坏数据/写失败保旧等实际执行。既有存储、历史、HTTPS/SQLite、helper cancel/EOF、资源/不可变门槛不缩减。

**边界：**可调用配置服务/owner 依赖已完成，不是整个 P1、全 App 配置线程安全或业务 helper 接线。
Mac严格 load 不把坏数据当空配置覆盖；显式 save 只保存独立 raw JSON 快照，不自动默认化/迁移，
非法字段可保存但下次严格 load 拒绝；调用方不能在快照创建时并发改输入。
真实用户文件选择/旧文件迁移、RequestSnapshot、provider/新UI仍待后续明确切片。
旧 `eec92a5` 的用户正向实测只属于旧包，新包仅本次三系统自动化；未新增用户账号/模型或实机结论。
免费 GitHub 路线和零付费不变；未 Release/master/Windows部署，不要求用户现在重装、安装CLI或登录。
下载的内层 ZIP 与本轮临时审计脚本已清理，保留去敏 JSON/测试日志；未在 Windows 执行 Mac 二进制。

<a id="configuration-ipc-checkpoint"></a>

### 配置业务私有 IPC 切片（2026-09-14，已完成本次接线与可读性修复）

1. [x] 保留普通启动/空 hello/fixture/runtime_probe 的零用户配置 I/O；
   仅显式配置连接启动参数选择 caller home + 实际 Info.plist 身份，首次有效 hello 创建目录/取得 owner。
2. [x] Python/Swift 同步 config_load/config_save 严格契约、真实/诊断能力区分及客户端可调用 API。
   不允许 request 指定路径；完整帧仍64KiB/16层，配置对象另限16KiB/10层与可精确互操作数值。
3. [x] accepted 只表示排队，started 后本地操作不可撤销；只有 started 前可 cancelled。
   EOF/shutdown 等待已经开始的操作、释放 owner 后退出；响应丢失/强制终止为结果未知，不重试/重放。
4. [x] 真实 Foundation -> 包内 helper -> 临时配置 load/save/退出/重开/竞争/坏文件；
   包内真进程加测试端 writer 屏障覆盖 cancel/EOF/shutdown 等待，不增加生产测试开关。
5. [x] 同步后置 XCTest 的准确方法集合/数量与严格0skip门槛，正常 Windows/hooks 与同包15/14/26，
   固定源码/文档/制品证据及临时包清理后停止。

本轮不接 history/provider/完整 RequestSnapshot，不改原生应用启动或现有诊断面板，
不操作真实用户配置/账号。旧 WinError5 风险及协作锁/原子文件完整性边界继续保留。

实现使用原 `MacConfigOwner` 和共享 writer，不建平行配置存储。连接启动只解析显式参数；
配置模式第一次有效空 hello 才创建 Application Support/取锁，不读配置文件。
普通诊断仍 `fixture=true`/原两项能力；配置模式 `fixture=false`/仅 config_load 与 config_save，
后者是真实本地业务 I/O，不是翻译/provider 能力。路径不在每次请求中指定。
配置 raw 与规范化结果都先校验；磁盘 decoder 的重复 key/非有限值/过深/超限错误在迁移写前拒绝。
新增可选 decode/validate 在原仓库锁内执行，不改变默认仓库或 Windows 读取接口。
配置 FIFO worker 非 daemon，started 与取消决策共用 server 锁；正常 EOF/shutdown 无两秒提前释放。
管道失效触发可中断 reader，仍等待本地操作和 owner 关闭；强制终止/响应丢失是结果未知，不回滚或重放。

本轮 Windows 验证按真实执行保留：
- 首次 backend 命令在新测试 bytes literal 引号处 SyntaxError，**0 tests 执行**；已修正字面量。
- 修正后 backend 联合 **152 项 / 23.922s / OK**；当时尚无全部最终门槛改动。
- 最新联合协议/配置/Windows真实兼容/隔离/打包/harness 回归 **294 项 / 32.131s / OK**。
  本轮未观察到自然 WinError5，不表示旧拒绝来源已解决。
- 第一次正常完整 pre-push：**1273 项 / 68.709s / OK**，privacy/compile 通过；保留既有 Tk teardown stderr。
- 首个源码 `3ca013f516d3bd6982e89a18fb8d273d0aa69c1f` /
  [run 34808053099](https://github.com/mclight-ship-it/cc-translate/actions/runs/34808053099) 失败：
  Swift 已编译，49 tests / 4 初次无包 skip / **1 failure** / 9.831s。
  新编码负例证明 Foundation 对 NaN 抛 Objective-C exception，不能被 Swift throws 捕获；
  产品和包内步骤尚未执行，两个 runtime 未运行，没有此 SHA 的成功 App 制品。
  修复编码前用 `JSONSerialization.isValidJSONObject` 校验（用数组包裹以保留合法 fragment），
  固定抛 Swift invalidJSON；保留原 NaN 断言并扩 Infinity/嵌套/fragment，不 catch NSException 或跳测试。
- 修复后正常完整 hook **1273 项 / 63.577s / OK**；源码 `9614eaba2975f21913346bab977e4262b210aa7a` /
  [run 34808290474](https://github.com/mclight-ship-it/cc-translate/actions/runs/34808290474) 三系统通过。
  该阶段每系统73进程/211核心/4后置Foundation，新增28便携/10真进程逐项核对；完整App审计通过。
  本地审计首次误用 producer 文件名读取 runtime 小报告，修正为实际 process.json/core.json 后通过，
  未改变产品或放宽断言；此阶段制品不与下述最终补充源码混用。
- 收尾补全初始化错误边界：Python3.12 Path.resolve 的符号链接环抛 RuntimeError，
  原配置 open 只映射 OSError/ValueError/TypeError，可能将私人路径泄漏到 traceback。
  仅在两处路径 resolve 的共用边界映射固定 config_unavailable，不吞掉其它业务错误；
  新便携故障用例和真包内自引用 Library 链接验证无 stderr 路径/无配置写入。
  原覆盖保留，最终门槛增加为74进程/212核心，Foundation仍精确4项。

#### 主链绿色源码与独立制品证据

- **仅下列绿色证据属于 `9614eaba2975f21913346bab977e4262b210aa7a`**，
  [run 34808290474](https://github.com/mclight-ship-it/cc-translate/actions/runs/34808290474) attempt 1；
  三 jobs/全部 steps success。此前失败 run 不改写为通过，未重跑相同失败源码凑绿。
- producer portable **397 项 / 9.692s / OK**。普通 Swift **49 项 / 9.677s**：
  45 pass、4 个无包集成明确 skip、0 failure；构建后的4项真实集成另计，不用 skip 充数。

| 实际系统 / build / arm64 | 产品或独立 harness | 包内进程73 | 包内核心211 | 后置 Foundation4 |
|---|---|---:|---:|---:|
| 15.7.9 / 24G830 | producer Xcode16.4 / Swift6.1.2 / SDK15.5 | 62.290s | 1.566s | 4.308s |
| 14.8.9 / 23J631 | harness Xcode16.2 / Swift6.0.3 / SDK15.2 | 56.798s | 1.236s | 3.754s |
| 26.6.2 / 25G83 | harness Xcode26.6 / Swift6.3.3 / SDK26.5 | 63.710s | 2.025s | 5.652s |

上述包内三组均0 failure/error/skip；14/26只运行同一个15/Xcode16.4产品，不重编译或重签产品。
image 分别 `20260907.0337.1`、`20260831.0302.1`、`20260907.0351.1`。
独立逐项核对每系统新增 **28 配置业务便携测试 + 10 真 helper 进程测试**，方法集合与固定源码一致，
原183核心/63进程全部保留；Foundation精确4个方法的 started/pass/数量/零skip同步核验。
实际执行缺失无写、raw保存/迁移/重开、坏盘和超限拒绝、双helper竞争接管；
真实 fsync 后 FIFO 屏障验证 started cancel=false、排队cancelled、EOF/shutdown不提前解锁、
duplicate不重放、丢stdout仍完成已开始写入并退出；日志不含业务配置/路径原文。
storage fixture、HTTPS证书/SQLite、旧helper取消/EOF、资源/模式/相对链接与前后不可变仍全部通过。

- [唯一完整 App artifact 10333318934](https://github.com/mclight-ship-it/cc-translate/actions/runs/34808290474/artifacts/10333318934)，
  名称 `macos-arm64-p0-development-NOT-A-RELEASE`，API未过期，到期 **2026-09-21T05:07:29Z**。
  内层 `CCTranslateMac-P0.zip` **18,387,887 字节**，SHA-256
  **7859d99db350399af8780b67bc7b4c23228d6594a6feaa52c6c06a4f11482123**。
  外层 artifact 是18,174,300字节，不把外层 digest/大小当内层zip校验。
- Mac14 [小报告10333982530](https://github.com/mclight-ship-it/cc-translate/actions/runs/34808290474/artifacts/10333982530)
  2,520字节，到期2026-09-21T05:09:21Z；Mac26
  [小报告10333512541](https://github.com/mclight-ship-it/cc-translate/actions/runs/34808290474/artifacts/10333512541)
  2,521字节，到期2026-09-21T05:10:17Z。没有重复上传App。
- 全部 **673库存 / 63资源hash / 35包内源码路径**独立核对（34不同Git路径；launch双目标），
  新configuration.py及server/protocol/仓库与固定Git blob一致。
  **6 arm64 Mach-O / 19实际runtime许可证 / 605保留文件覆盖**保持；
  项目自身许可仍标 `requires separate confirmation`，不是正式Release通过。
- 下载归档与三系统内容/模式/链接 tree SHA-256完全相同：
  **932c5c21180be2e04d7dd10ac0c28832d3e47a25b0395762226d358afdf7b62c**。
  内层zip与临时审计脚本已清理，保留本会话小JSON/日志；未在Windows运行Mac二进制。

#### 历史推送阻断：以下为67dc9ac时状态，后续实质修复后的新结果另记

本地补充源码 **4d769e7066c7a0d1be6c77d018e529c40e8f781e**：
初始化固定错误/严格runner targeted **62项 / 1.048s / OK**。
随后仅一次正常完整pre-push，privacy和编译通过，但 **1274项 / 68.728s / 2 failures**，
hook拒绝推送。真实旧Windows历史矩阵的一个subtest
`kind=text, is_dict=true, is_code=false, sig=None, limit=0`
在单次原子replace遇到 **PermissionError / WinError5 / Access is denied**，导致字节不匹配；
末尾“日志不存在”断言随之失败。**两条失败不是两次独立写入拒绝**，实际异常已在日志保留。
这与既有未解决拒绝访问现象一致，但本次没有失败瞬间外部因果证据，不能猜测OS/AV/磁盘根因。

没有重试生产写入、换目录、删断言、绕过hooks或重复跑完整suite凑绿。
因此远端仍为上述绿色 `9614eab`，本地 `4d769e7` 未推送；
**新增第29便携/第11进程（符号链接环）没有最终三系统证据，74/212只是新门槛，不是通过计数。**
后续三文档仅本地正常提交；不能假称docs-only push成功，也不能在仍含待推源码时
加 skip-ci 或推动不变源码重跑。本地文档HEAD、代码HEAD与远端制品身份分别交接。

配置主链真实可调用，但本切片的最终补充仍受阻，整个P1/完整产品未完成。
没有设置UI、历史业务IPC、RequestSnapshot/provider或全App可变配置线程安全。
旧 `eec92a5` 用户实测仍只属于旧包；本轮不验证用户真实文件/TCC/干净首开/官方CLI/账号/Intel。
免费GitHub/零预算路线保持，用户不需要现在重装、安装CLI或登录。

#### 独立review后的保存/迁移可读性修复（已验证）

父独立review发现两个真实数据完整性问题；旧9614eab绿色不能当它们的通过证据。
先在未改生产实现上运行三个新增反例：**3 tests / 0.072s / 3 failures**，全部未按新不变式拒绝写入。
另用真实临时JSON复现旧结果：8层数组/4000个零的compact8026字节，save返回saved:true，
Windows原缩进写入88205字节，随后两次load均invalid_config；
history_enabled长度16362的raw compact16384字节，save成功，第一次load迁移成功，
第二次load invalid_config（旧盘16391字节被改为16548字节）。均为合成数据，无用户文件。

选择最小兼容方案：**保持原indent=2 writer、wire64KiB、config compact16KiB/depth10/数值限制，
不扩大decoder或改成compact落盘**。保存前检查真实原始缩进表示、未来raw迁移payload的
compact和缩进表示，以及规范化后的返回视图。缩进UTF-8按writer平台换行计最多65535字节，
为现有decoder的LF保留1字节；因此4000零扩张例明确invalid_config，不再先saved成功后不可读。
每次load迁移写之前在原操作锁内单独validate_migration(payload)，不只检查normalized cfg；
异常保留旧字节/owner，默认仓库和Windows读取/写入/日志契约不变。

新增旧缺陷反例、compact和disk未来迁移payload恰好/多1字节、只迁移一次、无temp残留、
原盘不变、关闭等待validator、真实helper拒绝后仍持锁/接管、Foundation保存读取重开/固定拒绝。
实现时增加门槛为76进程/218核心/精确5Foundation，未提前标通过；随后真实执行结果见最终检查点。
上一1274项WinError5失败继续保留，本次是实质完整性修复后的新验证，不是对旧代码重复凑绿。
修复后最新联合Windows协议/仓库/旧Config与AtomicWrites/隔离/打包/严格runner回归：
**301项 / 32.124s / OK**；包含三个原失败反例及exact/over边界，不替代真实Mac或正常完整hook。

#### 可读性修复最终自动化检查点

- 源码 **c4596526bd7429f76b701228bf35f031f737a3a5**；
  [run34809961745](https://github.com/mclight-ship-it/cc-translate/actions/runs/34809961745)，attempt1，
  三jobs和全部steps success。正常完整pre-push **1280项 / 69.220s / OK**，privacy/compile通过。
  这是实质修复后的单次正常验证，随此前两个本地提交一起正常推送；没有绕过失败hook。
  旧1274项WinError5失败、拒绝来源未知及既有Tk teardown stderr均保留，不能据这次成功标为根因已修。
- producer portable **404项 / 9.474s / OK**。普通Swift **50项 / 9.107s**：
  45 pass、5个尚无App的集成明确skip、0fail；后置精确5个方法都必须实际运行且0skip。

| 实际系统 / build / arm64 | 产品或独立harness | 进程76 | 核心218 | 后置Foundation5 |
|---|---|---:|---:|---:|
| 15.7.9 / 24G830 | producer Xcode16.4 / Swift6.1.2 / SDK15.5 | 63.121s | 2.320s | 6.315s |
| 14.8.9 / 23J631 | harness Xcode16.2 / Swift6.0.3 / SDK15.2 | 65.843s | 2.004s | 6.184s |
| 26.6.2 / 25G83 | harness Xcode26.6 / Swift6.3.3 / SDK26.5 | 60.075s | 1.350s | 5.189s |

包内各组全部0 failure/error/skip；image分别
`20260907.0337.1` / `20260831.0302.1` / `20260907.0351.1`。
独立从固定源码AST核对每个系统 **33配置业务便携、38配置仓库、13配置IPC真进程**
全部逐项ok且各执行一次；包括原3个失败反例、实际缩进预算、compact/disk恰好和超过1字节、
既存raw迁移拒绝与原盘/temp/lock保护、close等待validator、正常保存/读/重开。
路径符号链接环的固定错误也在此包真实执行，不再沿用4d769e7当时“尚未Mac运行”的状态。
Foundation新增第五项通过真实Swift API验证：合法嵌套保存/读取/重开；
4000零扩张和near-limit raw明确failed且旧值不变，外部near-limit文件连续load不覆写。
原诊断、竞争、坏盘、取消/EOF、HTTPS/SQLite、storage/history/config fixtures与资源不可变门槛均保留。

- [唯一App artifact10335050150](https://github.com/mclight-ship-it/cc-translate/actions/runs/34809961745/artifacts/10335050150)，
  `macos-arm64-p0-development-NOT-A-RELEASE`，API未过期，到期 **2026-09-21T05:34:52Z**。
  内层 `CCTranslateMac-P0.zip` **18,388,246字节**，SHA-256
  **d661cff7cfe5ea768a4e65a0fec6639b1a027da0ff316ee3e76d05f805316753**。
  外层artifact18,174,788字节，不能用它代替内层大小/hash。
- Mac14 [小报告10334900913](https://github.com/mclight-ship-it/cc-translate/actions/runs/34809961745/artifacts/10334900913)
  2,550字节，到期2026-09-21T05:37:23Z；Mac26
  [小报告10333998396](https://github.com/mclight-ship-it/cc-translate/actions/runs/34809961745/artifacts/10333998396)
  2,549字节，到期2026-09-21T05:37:08Z。同run/commit原始App只在15构建，14/26不重建/重签。
- 独立zip CRC/0755/全部相对链接与 **673库存 / 63资源hash / 35包内源码路径**
  （34不同Git路径）逐字节核对通过；**6 arm64 Mach-O / 19实际runtime许可 / 605保留文件覆盖**保持。
  本次修改模块与固定Git blobs一致；项目自身许可仍标requires separate confirmation，未Release。
  归档及三系统内容/模式/链接摘要：
  **8189c0940c11322c98d69d87671b47efe3f22661af901117edf32d50eaa97937**。
- 内层zip与本轮临时审计脚本已清理，小JSON/逐项日志保留。最终仅三文档提交与上述源码分开，
  正常privacy/docs-only hooks推送，不为未变源码重复CI；精确最终文档HEAD以Git与交接记录为准。

本配置业务链切片及两个review完整性修复已有真实自动化证据，不等于全P1/完整产品。
用户路径选择UI、旧配置文件迁移服务、全App共享cfg/完整RequestSnapshot、历史业务IPC/provider仍未做。
旧eec92a5用户实测不迁移到本包，TCC/干净首开/用户官方CLI/账号/Intel仍单列待验；
免费GitHub/零预算不变，不要求用户现在重装、安装CLI或登录。

<a id="history-ipc-checkpoint"></a>
### 历史业务私有 IPC 切片（2026-09-14，已完成本次接线与自动化）

1. [x] 现有显式配置连接提升为config/history业务连接；保留旧启动参数和配置API，
   首次有效hello同时持有config.json.lock/history.json.lock，部分初始化失败释放已取得owner。
   普通诊断/模块导入/原生启动仍不读取或创建用户配置/历史。
2. [x] 同一Server/FIFO接history_load、history_add、history_clear，Swift同协议可调用API；
   路径只由连接home/实际Info.plist ID确定，复用HistoryRepository及原缩进writer/时间/字段顺序。
3. [x] 分页按实际完成帧（ID/seq/metadata/LF均计入）取最大可放前缀，不丢条目；
   cursor包含revision与offset，revision绑定连接随机代次、成功mutation计数与文件原字节hash。
   追加/clear、外部内容变化或新连接使旧cursor明确过期，不混页，不静默当空历史。
4. [x] 新add字段严格有界，实际未来文件及每条最坏分页envelope在写前验证；
   单条legacy无法入帧报固定错误，不写/删旧数据。配置16KiB预算不用于历史页。
5. [x] 真实并发/取消/EOF/shutdown/pipe失败释放两个owner，started mutation不冒称撤销；
   Windows兼容/新便携、包内真进程与Foundation、同包三系统及制品审计后正常3docs收尾。

本轮协议选择（版本仍v1，capabilities区分，默认诊断不变）：
- 业务ready fixture=false，能力精确为config_load/config_save/history_load/history_add/history_clear。
- history_load请求含operation/page_size(1..100)/cursor(null或{revision,offset})；
  完成为{entries,revision,total,next_cursor}。revision是64小写hex，offset正整数不超过10000；
  分页按原数组顺序，空数据只在明确文件缺失或合法空数组时返回。
- history_add含operation/input/output/is_dict/is_code/kind/sig/limit；无路径或客户端timestamp。
  input/output各最多24000 UTF-8字节，sig最多4096字节，kind严格text/dict/code/ocr，
  flags严格bool，limit严格1..10000整数，完成{recorded:true,revision}。不查询模型或自动记录翻译。
- history_clear只含operation，完成{cleared:true,revision}，包括空库clear也推进mutation代次。
  显式clear可清除坏文件；load/add的读取/格式/预算失败绝不以空数据覆盖。
- 文件读/写预算8MiB、最多10000条；legacy未知字段保留，但必须是安全可编码JSON，
  entry最多相对13层、有限数且abs<=2^53-1，已有字符串/null及bool字段规则沿用。
  新写入每条按最长合法ID、seq2、完整最坏分页metadata预检；实际页仍严格64KiB含LF。
- 固定错误包括history_in_use/history_unavailable/history_io_failed/invalid_history、
  history_too_large/history_entry_too_large/invalid_history_record/invalid_history_cursor/
  history_cursor_expired；连接资源关闭失败用state_io_failed，不输出文本/路径或异常原文。

这些是有界业务API限制，不改Windows泛型仓库/default/schema/缓存或上层history开关策略。
没有历史UI、请求快照、provider或真实用户数据操作；旧WinError5未知风险继续保留。

#### 实施过程验证与失败记录（最终结果在下一节，不回写历史失败）

- 第一轮Windows联合155项 / 20.337s：2 errors，旧owner合同明确禁止caller注入reader/writer；
  初版扩构造参数违反该既有合同。已恢复MacHistoryOwner的path-only公开API，不削弱负例，
  业务层改用固定有界仓库策略的内部适配，继续同一Mac owner/稳定侧文件锁。
  对应104项 / 1.315s通过。
- Windows真实消费者/新业务/配置/协议/存储/owner/打包/清单联合338项 / 33.797s通过。
- 另先证明尾页预算非单调边界：1项中1 error + 1 failure；
  中间前缀含next_cursor会超帧，但加上末尾空legacy条目后cursor消失，完整尾页反而恰好64KiB。
  已在首个超限处检查合法完整尾页，而不是错误报单条过大或返回非最大前缀；
  保留原断言并增加真包内反例。修复后新业务/严格清单57项 / 1.722s通过。
- 当前新核心24项、history真进程13项已接严格runner清单与同源support校验，
  门槛提高为242核心/89进程，旧218/76覆盖保留。此处是实际发现要求，尚不是Mac执行结果。
- Windows固定业务适配MRO/旧path-only API/FD与路径环合同35项 / 0.201s通过；
  最终Windows联合355项 / 33.022s通过。Swift新增9项协议负例及3项Foundation历史方法，
  原5项保留、强制精确集合提高为8项；本轮Swift尚待真实Mac编译/运行，不以静态差异检查替代。
- 源码`e7ad36a`正常完整Windows hook **1306项 / 75.538s，OK** 后推送；
  真实[run34814366721](https://github.com/mclight-ship-it/cc-translate/actions/runs/34814366721)
  producer通过便携/Swift编译测试/构建审计，但包内89项 / 67.548s有1 failure：
  原配置IPC的目录库存仍只期待config两个文件，未同步业务连接现在持有的`history.json.lock`。
  新13项history真进程均通过；不把后置Foundation/core/smoke或未运行的14/26填成成功。
  修正为精确三文件集合并额外断言未创建history.json，不删除/放松库存保护、不删除稳定侧文件。
- 库存修复`7a8250d`的针对性清单33项 / 0.852s及正常完整Windows hook
  **1306项 / 76.357s，OK**。随后[run34814743999](https://github.com/mclight-ship-it/cc-translate/actions/runs/34814743999)
  包内89进程全部通过，8项强制Foundation实际运行，其中历史竞争方法有2条关联断言失败：
  测试在clear尚未started时立即stop，却假设一定completed；实际accepted/cancelled是既定queued取消合同。
  修正测试为先观察真实started事件再stop，仍严格要求accepted/started/completed及0/1/2；
  新增清空后文件不存在、再次连接取得双owner并读到空历史。没有把两种终态都放行或改生产取消策略。
  受控进行中fsync/EOF/shutdown的强并发证明仍由真实进程屏障用例覆盖，不用事件观察冒充文件操作屏障。

#### 历史业务自动化可靠检查点

最终执行源码 **`dc0ba9c8cfcf39fd49e6220c69eeebe85c04df9d`**；
[run34815172344](https://github.com/mclight-ship-it/cc-translate/actions/runs/34815172344)，
attempt1，**全部3jobs/33steps success**。最后两次修复只改对应测试和事实文档，
未修改生产终态语义、不重试相同源码凑绿；不将此前两个失败run算作通过。
最后修复的Windows严格runtime选择/集合14项 / 0.381s通过，
正常完整hook **1306项 / 70.511s，OK** 后推送。前三次正常hook结果分别为
1306/75.538s、1306/76.357s、1306/70.511s，均有既有Tk teardown stderr警告，
这三次完整hook无failure/error/skip；初期155项的2 errors仍按上文保留。
这些成功仍不证明旧WinError5拒绝来源已解决。

| 实际系统与构建/测试身份 | 真包内进程 | 包内共享核心 | 强制Foundation |
|---|---:|---:|---:|
| producer macOS15.7.9 / arm64 / Xcode16.4 / Swift6.1.2 / SDK15.5 | 89 / 69.453s | 242 / 1.665s | 8 / 9.987s |
| 同包macOS14.8.9 / arm64；仅harness用Xcode16.2 / Swift6.0.3 / SDK15.2 | 89 / 62.220s | 242 / 1.200s | 8 / 7.283s |
| 同包macOS26.6.2 / arm64；仅harness用Xcode26.6 / Swift6.3.3 / SDK26.5 | 89 / 68.644s | 242 / 1.797s | 8 / 10.826s |

全部表中套件0 failures/errors/skips。producer另有便携 **430项 / 10.607s，OK**；
普通Swift **62项，54 pass + 8初次无包skip，0 failures**，其中协议39项实际通过。
构建后的8个精确Foundation方法在每个系统均实际运行通过，初次skip不是后置通过证据。
独立从固定源码AST逐方法核对：每系统**24项历史业务核心、13项历史IPC真进程、
原13项配置IPC真进程**各执行一次；新9项Swift历史协议负例包含在producer的39项中。
没有重复继承旧测试充数；新共享进程support与所有测试文件均来自同一checkout SHA，
包内全部`cc_*`来源验证不回退宿主或仓库Python。临时storage fixture、HTTPS证书验证、
SQLite真实读写、合成CLI监督、cancel/EOF、probe清理和App不可变检查均通过。

- App：[artifact10336113899](https://github.com/mclight-ship-it/cc-translate/actions/runs/34815172344/artifacts/10336113899)，
  `macos-arm64-p0-development-NOT-A-RELEASE`，API核实时未过期，到期`2026-09-21T06:54:21Z`。
- 小报告：[macOS14 artifact10335824928](https://github.com/mclight-ship-it/cc-translate/actions/runs/34815172344/artifacts/10335824928) /
  [macOS26 artifact10336223743](https://github.com/mclight-ship-it/cc-translate/actions/runs/34815172344/artifacts/10336223743)；
  两者均`stage=complete/status=passed/bundle_unchanged=true`，不重复上传App。
- 内层`CCTranslateMac-P0.zip` **18,398,003字节**，
  SHA-256 **`eaf0668fdf6c20b49ad76f29f37ccf0e175a143ae5e9d2cae066aef260b49f25`**。
- 内容/模式/相对链接树摘要
  **`f3b48e310acc3613e5507960d2ab1078934485c504c49b1e78caee5f26d9d8be`**，
  与producer及两个runtime前后报告一致。14/26使用该同包，没有重建或重签产品。
- 本地独立按ZIP真实字节核对**674库存、64资源hash、36包内源码路径（35唯一Git路径）、
  6个arm64 Mach-O、19份runtime许可证**；源锁与仓库相同，保留605个runtime文件的许可覆盖记录。
  包内新history模块及所有Core源码与上述固定提交Git blob逐一一致，source_tree_dirty=false。
  主程序/Python0755、相对symlink、CRC、必需资源、树摘要全部一致。
- 已清理下载的App zip和临时审计脚本，保留小型匿名JSON/实际测试日志与checkpoint。
  源码身份为上述固定SHA；记录本节的最终三文档提交独立，不冒称新的执行源码或重新触发未变CI。

本轮真正接通Swift客户端→包内helper→显式临时历史文件，包含有界分页、记录、清空、错误保护、
退出释放与重开。默认原生启动/诊断面板仍不打开业务连接，不读写用户配置或历史。
没有新历史UI、自动翻译记录、RequestSnapshot、provider/native官方账号或完整翻译闭环；
不称整个P1完成。用户现在无需重装/安装CLI/登录。旧`eec92a5`用户正向报告仍只属于旧包；
此新包只有三系统自动化证据，Finder首开/Gatekeeper/TCC/GUI/Intel仍独立待验。
未发布Release、未改master/Windows部署、未购买或运行用户模型；旧WinError5未知风险继续显著保留。

#### 独立review补充：业务worker启动失败的跨端确定终态（修复与新CI已完成）

独立review发现上述绿灯未覆盖的真实缺陷：Python线程启动抛RuntimeError时发
accepted(seq0)→failed(worker_start_failed, seq1)，不发started、不执行配置或历史I/O；
Swift却对所有accepted业务failed强制started/seq2，导致合法失败被判invalidTransition，
pending未结束，HelperConnection错误归类OutcomeUnknown。旧三系统成功不作为该分支通过证据。

- [x] 保留Python现有固定失败语义；Swift仅对accepted且未started的worker_start_failed/seq1放行，
  对该code的seq0/seq2/已started、其他code缺started、重复终态仍严格拒绝。
- [x] 原便携真实handler测试扩为五操作(config load/save、history page/add/clear)与缺失/旧盘两类，
  断言精确frame序列、perform未调用、两个owner关闭各一次、无任务/线程残留和旧盘不变。
- [x] 新真Mac进程矩阵和Foundation跨端测试：用不变App的Python -I -B及真实server，
  只在测试脚本注入Thread.start失败；实际stdout交给HelperConnection共用的ProtocolState，
  检查确定failed/无pending/无unknown，再用真实HelperConnection重开验证双owner释放。
  无生产注入开关或App修改，既有8项Foundation/89进程/242核心不删减；
  新强制门槛为9个精确Foundation、90进程、242核心，尚待本次真实CI。
- [x] 针对性、正常完整hooks、新同包15/14/26与制品核验后记录修复检查点，不开启下一功能。

Windows相关联合148项 / 20.970s，OK，含真实handler五操作/两类文件状态的精确frame及原Windows历史链。
Swift新增状态机负例尚未在Windows执行；Foundation直接运行包内Python生成真实失败frames，
不编造native消息，不改变Python服务行为；新Mac CI另行记录。

最终修复执行源码 **`4021270362418c0876dfd7aa51c4c697694f3758`**，
[run34817356816](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816)，
attempt1，**3jobs/33steps全部success**。生产仅改Swift精确failed状态分支；
Python已有固定失败/资源释放行为和HelperConnection消费路径不改，无生产重试或测试注入入口。
正常完整Windows hook **1306项 / 69.888s，OK**，包括原Windows历史回归；
仍有既有Tk teardown stderr警告，没有跳hook。旧WinError5已复现/来源未知的记录不变。

| 实际系统与工具链 | 包内真进程 | 包内核心 | 强制Foundation |
|---|---:|---:|---:|
| macOS15.7.9 arm64，producer Xcode16.4 / Swift6.1.2 / SDK15.5 | 90 / 65.594s | 242 / 1.446s | 9 / 13.945s |
| 同包macOS14.8.9 arm64，harness Xcode16.2 / Swift6.0.3 / SDK15.2 | 90 / 72.316s | 242 / 2.197s | 9 / 16.032s |
| 同包macOS26.6.2 arm64，harness Xcode26.6 / Swift6.3.3 / SDK26.5 | 90 / 66.118s | 242 / 1.393s | 9 / 13.736s |

表中均0 failures/errors/skips。producer便携430项 / 7.891s通过；
普通Swift64项=55 pass+9初次无包skip，包含40项协议单测及新的精确错误code/seq/终态负例。
后置9项在三系统全部实际运行，不能以初次skip代替。
逐系统日志确认扩展的真实handler方法、新Mac worker失败方法及新Foundation方法各执行一次；
五操作×缺失/旧文件10个子例均在对应方法内严格执行，没有删除原覆盖或重复继承充数。
Foundation测试实际运行不变包内Python产生ready→accepted→failed(seq1)后，
由真实共用ProtocolState解码消费，断言无pending/OutcomeUnknown；之后实际HelperConnection重开成功。
这不是修改BundleRuntime/HelperConnection来伪造回包，旧盘字节、无新JSON/临时文件和双owner释放均验证。

- 新App：[artifact10336766780](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816/artifacts/10336766780)，
  API核实时未过期，到期`2026-09-21T07:22:43Z`。
- 新小报告：[macOS14 artifact10336713348](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816/artifacts/10336713348) /
  [macOS26 artifact10336708374](https://github.com/mclight-ship-it/cc-translate/actions/runs/34817356816/artifacts/10336708374)。
- 内层zip **18,398,253字节**，SHA-256
  **`bb607badc1cb5de1c489d9c21431a9304f3d365e08d96375c64448bf7bc71741`**；
  同包内容/模式/相对链接树摘要
  **`956427e9349ba099548490537ed5a24857f10bbe9c44c43d988d62a8723359a0`**。
- 完整App独立核对674库存/64资源hash/36Core源码路径（35唯一Git路径）/6 arm64 Mach-O/
  19 runtime许可证及605保留文件覆盖，全部与固定源码/源锁/报告一致。
  两runtime均complete/unchanged，同包未重建/重签；HTTPS/SQLite/临时storage/cancel/EOF也全部通过。
- 已清理新下载zip/临时审计脚本，匿名小报告/日志/检查点保留；最后三文档提交与执行源码分开，
  正常docs-only privacy hook，不重新运行未变源码CI。

本项修复已取得新自动化证据，不把旧89/242/8绿灯或旧用户包实测赋给新包。
不扩大到RequestSnapshot/provider/UI，不操作真实用户配置或历史；本轮用户无需安装/登录/重装。
