# CC Translate 路线图

本文件仅记录产品方向，不包含本机环境或内部工作记录。

## 正在开发：原生产品已接通，继续 P4 分发生命周期

**最新已核验包：4dfb04a，Mac 0.1.0/build146 / [run35374096842](https://github.com/mclight-ship-it/cc-translate/actions/runs/35374096842)。**
图片翻译、词典来源、关联复制、剪贴板隔离、字号/摘要/历史条数/输入上限/结果位置/双击间隔/截图快捷键/恢复默认已进入同一完整App，
另已补齐Claude完整后端、原生选择、独立路径/模型草稿、切换与恢复默认。
真实watch0、3jobs/42steps全通过；原生845项（23构包前可选skip、零失败）、140PNG已核验，
同App15/14/26各253process/744core/21Foundation及实际worker/剪贴板测试通过。
[当前下载与步骤](MACOS_DEVELOPMENT.md#native-translation-user-check)已更新；
[完整审计](MACOS_TODO.md#native-app-version-checkpoint)已绑定本源码与实际版本；
[登录项检查点](MACOS_TODO.md#native-login-item-checkpoint)保留原始失败历史，不将它们当作当前仍被阻断。
继续更新/分发/卸载及P5–P6；真实账号、TCC/IME/VoiceOver仍另验，**整个移植尚未完成**。
P4首项已接入SMAppService实际登录状态与原生设置，不自动注册、不以本地开关假装系统批准；
新增13项原生方法与3张图已通过自己的Mac验证，现已包含在当前推荐包。
系统批准、下一次登录启动和升级后保持仍待实机，不用合成服务冒充真实登录项更改。
Mac独立版本/递增开发构建号与清单一致性已在4dfb04a自己的完整Mac包验证，
保留已有bundle身份及数据位置；目标bundle/runtime80项、正常1933项hook通过，
三系统实际About均核对0.1.0/build146，已更新当前推荐包。
继续Sparkle接入、更新签名资产和N→N+1保护；已核对稳定2.10.0的官方依赖及延迟启动API，
后续18d6d6d已完成原样framework/helper/许可打包及受限架构审计，13项新便携契约、联合93项及正常1946项通过，
真实框架加载方法、846原生项和140PNG已通过；完整同包验证仍在运行。
其后已继续[原生检查更新入口](MACOS_TODO.md#native-update-ui-checkpoint)：明确操作才启动Sparkle，
未发布渠道时提供原生说明和独立下载入口；15项新Swift测试/3张图待Mac执行，便携联合94项通过。
没有发布feed或签名升级验证，不把框架/界面准备当作自动更新完成。
[双击复制间隔](MACOS_TODO.md#native-copy-interval-checkpoint)
已接入现有配置和实际监听，新增25项测试、2张渲染，现已完成验收并包含于推荐包。
ab83b48首轮已编译并通过24项新增测试，仅剩禁用按钮的测试OCR定位失败；
修正采用独立fixture唯一可见原生按钮的只读状态断言，实际保存交互保持，不改生产代码或删除断言。
2d68fb6已通过全部25项间隔方法；旧输入设置动态reload查找和私有剪贴板发布fixture复现失败，
修正c758 / run35330446905现已实际编译，780项/23构包前skip/0失败、127PNG完整，
其中原剪贴板7、输入上限26及新间隔25均通过，完整同包验收已完成，后续3ffb141继续包含这些功能。
[截图全局快捷键](MACOS_TODO.md#native-capture-shortcut-checkpoint)已继续接入原生设置、持久化、
实际Carbon注册和既有截图入口，默认关闭；4fa56e0实际编译且通过17/18新方法、130PNG完整。
剩余中文重试的测试定位与一项旧中文设置图片识别正在修正，未当作完整绿色包。
1507454复验仍有两个方法失败；下一修正改用已识别控件identity，并补齐图片失败阶段证据，不盲目重跑同源码。
f8a014b现已通过全部18项新增方法；仅余旧设置截图OCR把“结束”读成“纯束”的1项失败。
下一修正提高测试原生绘制分辨率，保留完整Form与精确语句断言，尚未当作新包通过。
18db461已通过该原失败方法，另一幅中文图因过宽的测试改动出现OCR差异；
现收窄双分辨率到目标图和负对照，保留其他截图原绘制路径及全部断言。
3ffb141 / run35338542005现已原生798项/23既定skip/零失败，131PNG完整；
新增18项及此前两个失败方法全部通过，完整App和同包15/14/26现也已完成独立核验。
当时推荐升级至3ffb141；该历史包包含截图快捷键，不包含后续恢复默认。
注册不启动helper或申请截图权限，按键不自动发送翻译；恢复默认、Claude及P4–P6继续。
[恢复默认设置](MACOS_TODO.md#native-restore-defaults-checkpoint)已接入原生只读预览、明确确认、
保存回读和本机偏好恢复，保留CLI/账号/权限/历史/词典文件；13项新Swift测试均已通过。
这部分后续源码不借用截图快捷键验收run，已独立完成完整同包三系统验证并进入当前推荐包。
91773dd首轮已编译且通过11/13新方法；已修复正常停止时未退休恢复请求的缺陷，
并修正独立原生按钮的测试定位，保留全部断言。
8f898aa / run35341468695已正常通过1850项hook、811原生测试、134PNG和完整App独立审计；
4阶段断连与英中预览/取消/确认/回读均通过。实际watch exit0、全部3jobs/42steps成功；
917原失败实际watch exit1且未发布App，原始证据保留。
下一项[完整Claude服务](MACOS_TODO.md#native-claude-provider-checkpoint)已开始：
已实现有界一次性CLI传输，并继续独立Claude provider/JSONL与base64图片契约；
新增17输出/19provider/4预算便携契约及9真实Darwin provider方法，目标联合455项通过。
前置6fc首run后续URLSession下载失败证据保留；同源码重跑35347450089已完成同包三系统验收。
新provider源码ceae3fb的producer966portable/249process/728core与新9项Darwin均通过，
同包消费者现已完成独立验收。后续3e53aec的Python/Swift绑定会话与请求/缓存/历史/图片已接线，
402目标与1925正常hook通过；816原生含新5Swift连接方法及134PNG通过，Darwin业务链仍待该源码结果。
继续接入实际原生服务选择、各自路径/模型草稿、保存回读/排空重绑、图片及两套模型恢复默认；
首UI运行因新增测试漏传既有视图回调而编译失败，原失败保留；修正后连同Claude错误提示共
16项新Swift产品/真实菜单/渲染测试，仍待修正源码Mac执行。
第二轮250e8c7编译通过、832项实际执行，但4个方法失败；继续修复服务断连清理、
菜单测试模型观察及原文OCR分辨率，不以136张图片已生成当成UI通过。
ff30204的新16项产品用例及原生菜单/断连修复已实际通过；全832项仍有1个旧摘要OCR失败，
该轮未推荐新UI包。后续f9781b7复用分块识别并补负例，832原生项零失败、137PNG完整，
16项新产品与原失败方法全部通过；完整App独立审计及同包三系统验收完成，现升级为当前开发包。
未调用真实Claude账号或模型，不要求用户重新设置或登录；继续P4而不是结束移植。

普通翻译链路已获用户实测通过；现已实现独立翻译窗口/结果浮窗、菜单栏、设置/历史、
中英与系统主题，输入后可直接翻译，诊断不作为使用前置步骤。**移植尚未完成**。
当前界面与六种结果动作源码`4807f62` /
[run35125864397](https://github.com/mclight-ship-it/cc-translate/actions/runs/35125864397)
已通过正常完整hook1606、真实Swift编译、36原模型/8动作模型/6原生渲染、16张截图检查，
同一个App在三系统各193process/504core/15Foundation通过，完整制品已独立字节核验。
动作保留原结果、追加区块，不读翻译缓存或写历史；取消/失败/未知结果不自动重放。
[下载与正常使用](MACOS_DEVELOPMENT.md#native-translation-user-check)不再沿用旧诊断操作步骤。
本地词典源码`b86507f`已接原生下载/管理及无Codex首屏，正常完整hook1687通过；
producer生产URLSession真实下载/安装67,948,544字节，意图到同源原生离屏绘制P95实测37.747ms，
不是物理键盘或打包GUI进程验收。同一个App在15.7.9/14.8.9/26.6.2各202process/536core/17Foundation通过，
run实际watch exit0，完整App已独立核验，保留21张原生截图。
见[词典检查点与下载](MACOS_TODO.md#native-dictionary-checkpoint)。
全库历史搜索源码`eaf0c15` / [run35157108608](https://github.com/mclight-ship-it/cc-translate/actions/runs/35157108608)
现已通过同包三系统各207process/553core/17Foundation；正常hook1705，
新增26历史模型/4原生渲染与生命周期/2协议测试通过，31PNG及完整App已独立核验。
搜索原文/译文/日期、类型过滤后分页，不要求先加载旧页；输入合并、迟到结果隔离和明确清空保持。
首轮透明背景fixture导致的4渲染断言失败及修复如实保留于
[全库搜索检查点](MACOS_TODO.md#native-history-search)。
同帧区域截图、本地OCR预览及明确文字翻译源码`082aad6` /
[run35168220762](https://github.com/mclight-ship-it/cc-translate/actions/runs/35168220762)
已通过同App三系统各214process/570core/18后置Foundation，正常完整hook1726、
Swift335（316通过及19构包前可选skip），41PNG和完整App均已独立核验。
菜单/主窗口可截图、原生框选、编辑本地识别结果，再明确翻译文字。
历次编译/合同/测试fixture请求ID错误及修复保留于[截图/OCR检查点](MACOS_TODO.md#native-capture-checkpoint)。
原生关于/完整第三方许可源码`d699185` /
[run35176360537](https://github.com/mclight-ship-it/cc-translate/actions/runs/35176360537)
现已通过同包三系统各214process/570core/18Foundation及独立About1，实际watch0、3jobs/40steps全通过。
正常完整hook1728、Swift356（336通过及20构包前可选skip）、48PNG和完整App已核验；
新增20个原生方法及各系统的真实包读取逐方法确认。菜单/设置进入同一独立窗口，
完整许可可选择/滚动，版本及构建元数据不冒充实时签名验证，不需要CLI或模型请求。
导航黑块和中文像素测试语言修复的原始失败证据见[关于/许可检查点](MACOS_TODO.md#native-about-checkpoint)；
该关于包保留为前置证据，当前下载已由下方模型设置新包替代。
自定义Codex模型设置及生产混合语言OCR修正源码`b33515d` /
[run35185087510](https://github.com/mclight-ship-it/cc-translate/actions/runs/35185087510)
现已通过实际watch0、attempt1的3jobs/40steps，正常完整hook1747；
380原生方法（359通过/21前置可选skip）、56张PNG和完整App已核验。
同一App三系统各214process/585core/19后置Foundation及About1，同源生产OCR4也逐项通过。
修复了合成CLI错误强求目录覆盖参数的前提，保留实际模型请求断言；
Picker还补齐字节精确identity，避免Swift规范等价比较丢失不同ID。
该模型设置包保留为前置证据，当前下载已由下方纯文本粘贴新包替代。
明确模型ID不再被重复默认迁移改写；没有增加版本白名单或账号探针作为使用前置。
完整范围及原始失败/修复见[模型设置与OCR检查点](MACOS_TODO.md#native-model-settings-checkpoint)；
主动纯文本粘贴源码`2f371fa` /
[run35210321489](https://github.com/mclight-ship-it/cc-translate/actions/runs/35210321489)
已实际watch0、attempt1的3jobs/40steps通过；正常完整hook1749，
469原生方法（21构包前可选skip）、71PNG及完整App均已核验。
Settings明确开启后独占⌥⇧⌘V，在外部应用去格式并提交一次粘贴；本应用编辑器走原生
Paste and Match Style，默认关闭，无需CLI、账号或诊断前置。
同一个App三系统各214process/585core/19后置Foundation、About1及同源OCR4/粘贴46通过，
没有后置skip或背景AppKit promise警告，真实AppKit publisher也纳入正向回归。
macOS26发现的跨句柄同步问题已用MainActor AppKit真实changeCount/立即写入修复，
可能等待的数据仍在后台读取；不以只通过15/14的旧包替代最终证据。
原始编译/状态/编码/渲染失败、旧Windows访问拒绝及完整制品核验见
[纯文本粘贴检查点](MACOS_TODO.md#native-plain-paste-checkpoint)。
动态模型目录与共享原生选择已接线，源码`d637bea` /
[run35229459129](https://github.com/mclight-ship-it/cc-translate/actions/runs/35229459129)
已实际watch0、attempt1的3jobs/40steps通过；正常完整hook1778，
Swift522（500通过、22构包前可选skip）、84PNG与完整App已独立核验并替换推荐下载。
同App三系统各222process/604core/20Foundation，后置About1/Vision4/粘贴46均通过。
跨系统旧剪贴板fixture使用唯一opaque item ID，保留原断言，C/AppKit前后计数各2；
未修改生产粘贴实现，不把失败重试当修复。仅明确刷新读取所选Codex目录；空/失败不阻断
Fast/Default、手工ID或普通翻译，不引入精确版本白名单、账号探针或自动重试。
Mac测试编译、协议派发、实际可读性、旧fixture受控复现及修复保留于[模型目录检查点](MACOS_TODO.md#native-model-catalog-checkpoint)。
真正图片provider及明确发送已接线，含选区PNG、双端附件所有权、取消/清理和仅输出历史；
源码bfd9382的75项新增非集成方法与16张新增原生渲染已通过；
同一App在15/14/26均通过231process/644core/21构包后Foundation，新增图片方法逐项实测。
但run35255188300整run仍失败：两个消费者仅旧私有剪贴板阶段未通过。
完整工程App已独立审计，尚不作为推荐版；继续修复，不将构包前skip或局部通过当成完成交付。
后续诊断源码667d17b/run35263101241实际失败于一个旧剪贴板fixture构造：
不同名称的新资源枚举出旧资源ID，但同一reference的名称和新ID的flavor查询正确；
第三个reference枚举正确，名称复用假说已被本次证据否定，底层机制及修复仍待验证。
该源码的76新增Swift方法和231process/644core/21后置Foundation通过，
原始unit失败门槛仍阻止App发布/消费者运行；没有用测试诊断冒充修复。
P2词典来源与许可按钮的稳定identity接线并行继续，不冻结其余可实现产品工作。
后续918f497释放顺序候选的producer通过，但同App两消费者仍在旧剪贴板阶段失败，
因此没有宣称修复或推广新包。独立来源功能420928b已接通共享来源元数据、
原生按钮/弹窗和生命周期回归；正常完整hook1823通过，新增18Swift/4core/1process，
首次Mac CI35271286020实际发现18个新增方法、17通过，来源更新时序方法失败；
112张真实PNG已核验。集成前已修正一个误嵌套、无法被XCTest发现的新方法。
仅测试时序修正62a9219/run35273880271使18项逐项实际start/pass各一次，
232process/648core/21后置Foundation通过；但旧C promise枚举仍返回旧ID，
整run实际watch1、没有App/消费者，不将新界面通过说成整run绿色。
dbf5b33改用AppKit单次交互条目读取；46项产品测试保持，明确以已知C发布ID/精确字节/
真实promise回调和后置AppKit顺序验证替代C枚举器符合性断言。
正常hook1823/95.502s通过，但run35275212199实际watch1：仅UTF-16无损解码失败，
自动UTF-8别名把BOM带入文字；46粘贴45通过、18Sources及21后置Foundation通过。
先前API步骤success为continue-on-error后的conclusion，不能当成原始通过；门槛实际阻止App发布。
237f3c9优先解码UTF-16本身并增加literal-prefix/TSV及精确写入字节检查，
正常完整hook1823/88.733s通过，但run35277832576实际watch1：
producer和同App14完整通过；26仅粘贴阶段仍有C promise身份错误和UTF-8 literal前缀丢失，
说明固定优先级不足，原断言继续保留并修复。237完整App已独立审计，仍不推荐；
同包三系统232process/648core/21Foundation全部通过，不拿独立阶段替代整体验收。
P2双Cmd+C关联回退另有37项新原生测试，父已审阅并接上共享多编码读取、
同包consumer私有剪贴板验证，离线回归68通过；实际Mac结果见下，不在237的App中。
237的26失败进一步归因已更正：case5读取完整25字节UTF-8，前缀丢失在Foundation解码，
不是取了UTF-16别名。后续共享解码采用Swift逐字节回验，promise测试改用真实AppKit
回调身份检查，原46方法和C eager字节oracle保留；同包消费者新增7项fresh-copy读取验证。
接线源码c7745ec/run35284938518已实际运行：653原生测试/23构包前skip/零失败，
37项新P2和18项来源方法各自真实通过；同App三系统的46粘贴/7新读取也均通过。
不过15/26出现后台AppKit promise线程警告，26被原有检查拒绝，完整run退出1；
14完成全部后置审计。继续修线程用法，不删警告检查冒充解决；工程包未替换推荐包。
主线程隔离reader已合并：复用App早期只读入口，独立测试producer履约，原46项保留并新增11项；
消费者新增同App worker验收，本地bundle/runtime联合72项已过，Mac执行仍待新源码CI。
P3原生文字大小已交付：90%/100%/125%/150%，默认保持现有字号，覆盖主界面/历史/截图，
不依赖helper或重写业务配置。新增16项测试和3张预期截图尚待新源码Mac执行；不在c7745ec中。
首次23cfd5e/run35292610415因字体测试编译错误退出1；已修正测试类型/throws声明，
保留全部方法和断言，继续新源码验证，尚不提供替代推荐包。
修正0812d47/run35293312910已实际编译/执行680Swift，producer11worker/46粘贴/7fresh通过且无旧线程警告；
但2项新字体交互方法失败（组合输入丢失、字号菜单项定位/动作），完整run仍退出1。
115PNG含新增3张字号图均已核验，14/26未执行、App未发布；继续修真实交互，不拿图或局部通过代替完整包。
随后交付主输入/OCR共享AppKit编辑器修复、真实菜单交互，以及长文自动摘要设置。
原失败断言保留；新增2字体方法和15摘要方法，现有联合120测试已过。
569bca6/run35296221920实际执行697项Swift：原IME/菜单问题已通过，
但主输入/OCR滚动及摘要控件定位共6方法失败（10断言，2 unexpected）；完整run退出1。
116/118PNG已核验，两个摘要确认态图缺失；失败态图存在也不代表测试通过。
11 worker/46粘贴/37关联复制及232进程/648核心/21后置Foundation均通过、无旧线程警告，
但App未发布、14/26未运行。继续补文档布局与真实控件测试，不放宽条件或替换推荐包。
历史保留条数设置也已交付（降低确认、回读、记录后列表待刷新），新增21原生方法/2张预期图；
7项实际存储契约与bundle库存联合236项通过，下一core最低655。该增量仍需自己的原生CI，不借用569结果。
编辑器布局和摘要/历史真实AX控件测试修复已合并待验，静态718原生方法/预期120PNG，
不以静态检查冒充新源码编译或交互通过。
464a9a7/run35299872986实际718项执行后仍失败：历史18模型/列表及7新Python契约通过，
但7设置UI、2滚动和1来源清空方法未过（26断言、9 unexpected）。116/120PNG，无App发布或14/26验收。
继续修原生呈现/异步渲染与诊断；CI提前保留unit日志，不削减测试或放松发布条件。
fc089e6的提前日志实际718项、24失败断言/9方法；来源清空此次通过。
诊断明确阅读锚点混用了合法负偏移下的clip/文档坐标，正在修正坐标转换，
并使测试读取SwiftUI公开AppKit语义接口；不以窗口呈现或116张截图冒充这些问题已经解决。
45edbfc/run35303254425的早期原生日志已确认18项字体方法全部通过，坐标根因修复有效；
但7设置UI方法仍未过，718项有18失败断言、23前置skip，实际116/120PNG，未推广为完整通过。
输入长度设置与主输入/选择/OCR/词典统一预检也已交付，分别呈现Unicode码点与UTF-8字节，
不截断原文，不把提高字符上限误作扩大协议预算，不取消/重放已提交请求。
新增26原生方法/2张预期图及10后端契约，后端连同库存联合237项通过；
下一源码core最低665，原生744方法/122PNG仍待自己的Mac执行，不借用45edbfc结果。
见[输入长度检查点](MACOS_TODO.md#native-input-limit-checkpoint)。
首轮76ae1f3/run35304718654通过正常1846项hook与Mac903项便携测试，但预算常量的模块可见性
导致App编译失败，没有原生执行或新包。正公开既有只读预算并补齐所属窗口的测试语义树入口，
不改变预算、控件或断言；继续新源码验证。
bb1f851已实际编译并执行744项，新增20模型/预检及18字体方法通过，但15方法有37失败断言，
116/122PNG，整run仍退出1。正在修真实编辑器对规范等价Unicode拼写的错误去重，
并以实际渲染控件/公开动作验证设置接线；不把provider getter当成完整AX客户端，也不将截图冒充VoiceOver验收。
一项私有剪贴板fixture与一项中文说明OCR也保持未通过并继续处理，全部原断言保留。
903便携、232进程、665核心及21后置Foundation通过只代表这些阶段，不代表完整App或整个移植完成。
后续7f54e7e因本次fixture编辑意外重复局部声明而未执行原生方法；已定位并删除重复声明，
保留失败原日志和全部原断言，继续新的完整源码验证。
a4ac252修复编译后真实执行744项，20输入模型/预检和94剪贴板通过一次，仍15方法失败。
正继续处理等价Unicode拼写的SwiftUI更新、真实控件交互和中文截图诊断；未删断言或改系统权限，
116/122图片完整性通过不等于原生产品验证通过。
实际渲染控件helper已交付并审阅，将与精确字节编辑器修正一起重新验证；
保留744方法/122预期PNG，不再混同provider getter、真实控件交互与完整AX/VoiceOver三种证据。
e314fa6实际通过18字号/精确编辑、94剪贴板及部分真实设置操作，仍10方法失败；
继续依据实际控件几何、渲染就绪与OCR诊断修复，不以部分绿灯提前结束。
95f3078剩5方法未通过，全部4项输入限额真实交互及新增设置的部分绘制已通过；
下一步继续本应用正常鼠标事件分派与失败图像诊断，没有修改产品权限或假造保存结果。
76f01c1仍有6个原生方法失败，不宣称事件修正成功；已用实际截图区分计数可见与OCR漏读，
继续修复测试交互和读回证据，后续P3设置与Claude接线并未完成。
67f280c已通过两处OCR，仍有4个原生方法待修；继续历史确认态、摘要点击和按钮刷新时序，
不因图片齐全或部分用例通过而宣称可交付新包。
87b4be6确认了历史模型修复，完整原生交互仍需修正测试事件匹配并实际重验。
243fd22实际通过摘要保存/回读/重开及全部输入操作，仅剩历史确认按钮原生操作未通过；
继续修复并等待完整App/同包消费者验收，不提前推广候选包。
27bc99f英文历史完整流程已执行，中文动态控件的渲染等待仍需修正，整体方法尚未通过。
5774386已通过历史恢复方法，仅剩实际存在的中文确认按钮未被测试OCR识别；
继续用公开原生角色定位与完整操作验证，不将测试识字问题当成产品不可用或移植完成。
bf89495实际原生744项/23构包前skip/零失败，英中历史操作完整通过；122图完整。
完整App及同包消费者验收尚未完成，不提前宣称这批功能完成验收。
并行推进[结果窗口位置](MACOS_TODO.md#native-result-position-checkpoint)：记住位置/居中/鼠标附近已接设置与真实面板，
保持流式更新不移动窗口；新增11项测试及3张预期PNG，尚待本源码实际Mac验证。
018b9de已实际通过位置功能10/11方法及3张渲染；继续修复损坏负尺寸解析与旧摘要动态控件测试，
保留该轮755项中的3个失败断言，不以部分通过代替新包验收。
6e2a341已通过位置11项和摘要15项；旧复制UTF测试在fixture发布阶段报AppKit私有板为空，
尚未进入生产reader。继续修正私有资源创建，保留原始字节及全部业务断言，不修改生产读取来迎合测试。
详见[关联复制进展](MACOS_TODO.md#native-associated-copy-checkpoint)。
详见[来源按钮检查点](MACOS_TODO.md#native-dictionary-sources-checkpoint)。
详见[图片翻译进展](MACOS_TODO.md#native-image-translation-checkpoint)；
上述早期候选当时不替换推荐下载；当前已升级为本节开头的8f898aa核验包，已包含结果位置、双击间隔、截图快捷键和恢复默认。
完整模型/设置余项及其余P3–P6仍未完成。
可实现部分不等待完整权限矩阵，
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
