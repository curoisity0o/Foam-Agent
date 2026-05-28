# Known Issues

## 1. input_writer 偶发遗漏 fvSolution 中的 *Final 子字典

### 现象

`input_writer` 生成的 `fvSolution` 偶发遗漏 OpenFOAM 要求的 `*Final` 子字典（如 `pFinal`、`UFinal`、`kFinal`），导致仿真启动报错：

```
keyword pFinal is undefined in dictionary "system/fvSolution/solvers"
keyword UFinal is undefined in dictionary "system/fvSolution/solvers"
```

之后 reviewer 循环自动修复，浪费 1-2 轮 loop。

#### 背景：什么是 `*Final`

PISO/PIMPLE 算法在每个时间步内有多次内循环修正（由 `nCorrectors` 控制）。`*Final` 子字典控制**最后一次内循环**的求解器设置，普通条目控制**中间循环**。

以 `pFinal` 为例，假设 `nCorrectors = 3`：

```
时间步内的 3 次压力修正：
  第 1 次：用 p       { relTol 0.05 }  → 宽松收敛，快
  第 2 次：用 p       { relTol 0.05 }  → 宽松收敛，快
  第 3 次：用 pFinal  { relTol 0 }     → 严格收敛，精确
```

核心思路：中间迭代用 `relTol`（相对容差）快速求解，节省计算量；最后一次用 `relTol 0`（绝对容差）确保完全收敛。同理适用于 `UFinal`、`kFinal`、`epsilonFinal` 等。

Foundation OpenFOAM v10 开始**强制要求**这些条目存在，缺少则直接报错退出。旧版本不检查，因此多数 LLM 训练数据中的 OpenFOAM 示例未包含 `*Final`。

### 复现情况

该问题**非必现**，取决于 LLM 当次生成的行为。以下为 DeepSeek V4-Pro 在 OpenFOAM v10 上的测试记录：

| 测试场景 | 初始遗漏项 | 修复 loops |
|----------|-----------|-----------|
| icoFoam cavity | `pFinal` | 已知 |
| pisoFoam cavity (RAS) | 无遗漏 | 1 (其他错误) |
| pimpleFoam planarCouette | 无遗漏 | 1 (其他错误) |

测试脚本：`tests/test_pisoFoam_cavity_multirun.py`、`tests/test_pimpleFoam_couette_multirun.py`

### 根因分析

1. **Prompt 缺少显式约束** — `_build_prompts()`（`src/services/input_writer.py`）对 `controlDict` 有条件约束（不包含后处理），但对 `fvSolution` 没有类似约束来确保 `*Final` 条目存在。

2. **LLM 行为不确定** — RAG 数据库中有 66 处 `pFinal`、14 处 `UFinal` 参考，但 LLM 是否采纳取决于当次生成的注意力分配，无法保证稳定输出。

### 建议修复方向

在 `_build_prompts()` 中，按 `file_name == "fvSolution"` 条件注入约束：

```python
if file_name == "fvSolution":
    code_user_prompt += (
        "CRITICAL: For pressure-velocity coupling solvers (PISO/PIMPLE), "
        "the solvers dictionary MUST include "
        "corresponding *Final sub-dictionaries for each solver entry "
        "(e.g., pFinal for p, UFinal for U). "
        "Typically { $<field>; relTol 0; }. "
        "Also ensure the PIMPLE/PISO sub-dictionary matches the solver's requirement."
    )
```

这遵循了已有的 `controlDict` 条件约束模式，仅在生成 `fvSolution` 时生效。

### 影响范围

- `src/services/input_writer.py` — `_build_prompts()` 函数
- 涉及所有 PISO/PIMPLE 系列求解器（icoFoam、pisoFoam、pimpleFoam、interFoam 等）

### 状态

- [x] 已记录，reviewer 循环可兜底
- [x] 已在 `_build_prompts()` 添加 prompt 约束 — 测试结果从 1 loop 降为 0 loop（DeepSeek V4-Pro）
- [ ] 注：所有测试均使用 DeepSeek V4-Pro。该问题非必现，reviewer 循环已能兜底自动修复。prompt 约束可降低浪费 review+rewrite 调用的概率（每次约省 1-2 次 LLM 调用），但非严格必要。
