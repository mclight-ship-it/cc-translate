# 测试说明

这里是 CC Translate 的单元测试，只覆盖**纯函数**（不依赖 GUI、剪贴板、
网络或 Claude CLI），投入小、回归保护大。测试文件是独立的，**app 运行时
不会加载它们**，因此对功能、依赖、打包零影响。

覆盖范围：

| 文件 | 覆盖的函数 |
|---|---|
| `test_classify.py` | 直接导入共享 `cc_classify` 的原有分类矩阵；Windows/macOS 使用同一规则 |
| `test_classify_import.py` | isolated Python 导入/运行分类和方向时无 GUI、平台、provider、文件写入或网络副作用 |
| `test_classify_windows.py` | Windows 入口兼容导出为同一组函数/阈值，复跑分类矩阵 |
| `test_direction.py` | 共享方向目录、路由矩阵、混合文本/日文/韩文、原有阈值和精确 prompt 内容 |
| `test_direction_windows.py` | `cc_core` / Windows 入口导出同一函数/常量，界面语言标签仍用原 wrapper |
| `test_prompts.py` | 12 项静态文本提示词/revision 的抽取前精确 UTF-8 快照、数据/代码边界和动作 identity |
| `test_prompts_windows.py` | Windows 主入口、warm、结果操作消费同一提示词对象，避免未使用的重复目录 |
| `test_provider_contracts.py` | 无 CLI 的原请求/结果/状态契约、冻结语义、registry identity 与退出错误传播 |
| `test_provider_exports.py` | 显式请求后端导出仍返回原对象并缓存；未知属性/导入失败不回退 |
| `test_dictionary_store_portable.py` | 合成词典的原生 URI、只读约束、来源保留、线程局部关闭/重开及错误；随包 Python 同源执行 |
| `test_is_single_word.py` | 直接导入共享 `is_single_word`；词典触发的长度/标点/混合文本/空白及既有边界行为 |
| `test_rich_segments.py` | `iter_rich_segments` / 行内解析 / 流式安全 / 代码块高亮分流 |
| `test_highlight.py` | `highlight_code` / token→tag 映射 / Pygments 缺失时的优雅降级 |

## 怎么跑

无需安装任何东西（用标准库 `unittest`）：

```bash
python -m unittest discover -s tests
```

或者用 pytest（更好看的输出，可选）：

```bash
pip install -r requirements-dev.txt
pytest
```

## 说明

- `_tr.py` 负责按路径把 `translator.pyw` 加载成可导入模块（因为它是 `.pyw`
  后缀，且 GUI 只在 `__main__` 下启动，import 时不会弹窗）。
- 断言值都是从真实函数的实际输出捕获来的，不是凭空想象——改动这些纯函数后
  跑一遍就能立刻知道有没有改坏原有行为。

## macOS P0 便携测试

`test_macos_protocol.py` 直接导入无界面 helper，并测试真实私有管道子进程、取消/EOF、
输入边界和临时 SQLite 读写；不导入 Windows 主入口，不访问账号或调用模型。
`test_macos_bundle.py` 验证打包规则，不在 Windows 执行 Mac 二进制。

```bash
python -m unittest tests.test_macos_protocol tests.test_macos_bundle
```

原生 XCTest、真实 HTTPS、GUI/TCC 和签名包必须另在 Mac 验证，不能用这些测试替代。
环境、命令及未通过的门槛见 [macOS 验收清单](../docs/MACOS_TODO.md)。

同制品矩阵控制层：`python -B -m unittest tests.test_macos_runtime tests.test_macos_bundled_tests tests.test_macos_bundle tests.test_macos_protocol`。
这些离线负例不执行 Mac 二进制。Mac15 producer 与 Mac14/26 consumer 共用
`tools/macos/bundled_tests.py`（明确包内 `-I -B`、原 process/core 套件、真实 storage fixture）。
`runtime_matrix.py` 校验同 run/SHA 的精确 artifact、zip hash/资源/模式/链接与前后不可变，
并复用 HTTPS/SQLite/cancel/EOF smoke。独立 Swift harness 复制当前 checkout 的 support/C 与
原 HelperIntegrationTests，只编译测试宿主，不生成或修改被测 App；失败/skip/空套件不能通过。
真实 OS/编译器与执行结果必须取 Actions 日志/去敏报告，不从 runner README 推定。

## P1 共享分类与方向

```bash
python -B -m unittest tests.test_classify tests.test_is_single_word tests.test_direction tests.test_prompts tests.test_provider_contracts tests.test_dictionary_store_portable tests.test_classify_import tests.test_result_rules
```

Windows 兼容验证另加 `tests.test_classify_windows tests.test_direction_windows tests.test_prompts_windows`。Mac CI 还会用 `.app` 内的 Python
以 `-I -B` 执行相同便携用例，并断言导入的是包内模块，而非源码或宿主 Python。
分类/方向抽取不改变 P0 协议能力；helper/UI 仍只有合成 fixture 和诊断，没有真实翻译。

缓存签名/history-kind 的固定字节矩阵与冻结旧方法差分在 `tests.test_result_rules`；
Windows 另运行 `tests.test_result_rules_windows` 验证真实 wrapper、元数据捕获和缓存/历史消费者。

存储基础层：`python -B -m unittest tests.test_storage tests.test_storage_windows tests.test_classify_import`。
便携矩阵使用临时目录，验证显式 Mac 路径、不创建/回退、真实 JSON 字节/重开/替换、
fdopen/部分写入/flush/fsync/replace 故障及单操作 temp/descriptor 清理；
Windows 消费者仍走原配置/历史/日志入口。Mac CI 还从实际 bundle Info.plist 取应用身份，
显式调用包内 `cc_macos.storage_fixture`，生命周期由 TemporaryDirectory 管理。

共享历史仓库：`python -B -m unittest tests.test_history tests.test_history_windows tests.test_history_owner_contract tests.test_storage_windows`。
Windows 真实入口与冻结旧实现比较字段/序列化、缓存/日志及错误策略；配置/路径旧 AST 仍冻结，
历史旧 AST 改为验证差分 oracle，并额外用可观察的真实 RLock 验证 add/clear/close 竞争，不靠短 sleep。
Mac 的 `macos/PythonTests/test_history_owner_process.py` 必须由现有包内 process runner 运行，
检查稳定侧文件 flock、第二 owner、JSON replace/clear、真实退出/崩溃/fork、FD/临时文件和显式 history fixture。
不在 Windows skip 冒充完成，不扩业务 IPC；process/core 下限提高到 44/128，原有测试全部保留。
这不是唯一 writer/跨进程事务测试，也不访问用户配置或新增 runtime JSON 字段。
配置默认/i18n/provider selection 留在 UI，纯模块不导入 `cc_core` 或访问用户磁盘/网络。
这些测试不表示完整请求快照或历史写入所有权已实现。

## P1 native config / catalog 进程监督

`tests.test_codex_config_darwin_contract` 在 Windows 检查 dispatch、库边界和清理顺序，
不执行 Darwin 二进制。`macos/PythonTests/test_codex_config_process.py` 只在 Mac CI
使用真正包内 Python/C 库执行，覆盖合成 app-server、后代与 EOF；非 Mac 直接失败而非 skip。
其 fake CLI 不代表真实账号、官方 CLI 版本或完整 native provider 已通过。

`tests.test_codex_catalog_darwin_contract` 另验 catalog Darwin 调度、8 秒/8 MiB 参数、
取消隔离、fatal 监督失败不降级或提交请求，以及 Windows 旧路径不加载桥接。
`macos/PythonTests/test_codex_catalog_process.py` 使用实际包内模块/CLI 子进程，
覆盖冷缓存三次与磁盘重开一次真实调用、双流总限额、期限、取消/EOF、后代清理和 sibling 存活。
配置与 catalog 共用 C 所有权边界；正常/错误均先处理组再回收 leader，ECHILD 后停止所有组操作。
