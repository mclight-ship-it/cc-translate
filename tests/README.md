# 测试说明

这里是 CC Translate 的单元测试，只覆盖**纯函数**（不依赖 GUI、剪贴板、
网络或 Claude CLI），投入小、回归保护大。测试文件是独立的，**app 运行时
不会加载它们**，因此对功能、依赖、打包零影响。

覆盖范围：

| 文件 | 覆盖的函数 |
|---|---|
| `test_classify.py` | `classify_selection` / `code_ratio` / `_looks_like_code_line` |
| `test_is_single_word.py` | `is_single_word`（词典模式触发判定） |
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
