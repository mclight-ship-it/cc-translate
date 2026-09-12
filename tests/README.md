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

## P1 共享分类与方向

```bash
python -B -m unittest tests.test_classify tests.test_is_single_word tests.test_direction tests.test_prompts tests.test_classify_import
```

Windows 兼容验证另加 `tests.test_classify_windows tests.test_direction_windows tests.test_prompts_windows`。Mac CI 还会用 `.app` 内的 Python
以 `-I -B` 执行相同便携用例，并断言导入的是包内模块，而非源码或宿主 Python。
分类/方向抽取不改变 P0 协议能力；helper/UI 仍只有合成 fixture 和诊断，没有真实翻译。
