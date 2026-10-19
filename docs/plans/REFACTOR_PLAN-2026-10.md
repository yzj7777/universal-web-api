# 重构执行计划（2026-10）

> 依据：[`../review/HEALTH_ASSESSMENT-2026-10.md`](../review/HEALTH_ASSESSMENT-2026-10.md)
> 分支：`arena/8cd09dd0-universal-web-api` · 基线：`a99281d`
> 执行方式：**每步一个 commit，每步必须 `pytest` 全绿才能进入下一步。**

## 铁律

1. **不重写。** 只做局部、可验证的结构调整。
2. **每步一个 commit**，commit 前 `pytest -q -m "not browser and not real_browser"` 必须为 **0 failed**。
3. **任何结构性改动都有棘轮锁住**，只允许变好，不允许变差。
4. 不可自动验证的改动（拆函数、改依赖方向）必须**先有测试再动**，或**同步补测试**。
5. 单步失败 → `git revert` 该步，不强行推进。

## 验收脚本

```bash
# 每步提交前执行
uv run --no-sync pytest -q -m "not browser and not real_browser"
uv run --no-sync ruff check .
```

---

## 度量基线（2026-10-19）

| 指标 | 基线 | 目标 |
| --- | --- | --- |
| 测试中模块循环数 | 93 / 245 (38%) | < 40 |
| 最大纠缠分量 | 63 模块 | < 20 |
| 函数内延迟 import (PLC0415) | 690 | < 400 |
| 覆盖率 | 41.8% | 55% |
| CC > 50 的块 | 30 | < 10 |
| 最大函数 CC | 357 (1348 行) | < 60 |
| ruff 启用规则 | 5 条 | 20+ |
| 类型检查覆盖模块 | 0 | 协议层 |
| 死代码 (vulture ≥90%) | 9 | 0 |

---

## 步骤清单

### 步骤 0 — 循环依赖棘轮　`[零风险 · 已完成]`
新增 `tests/test_import_cycle_ratchet.py` + `tests/fixtures/import_cycle_ratchet.json`。
AST 构图 + Tarjan 求 SCC；模块数/环数只减不增。
**不改任何运行时代码。**

### 步骤 1 — ruff 第一档：机械修复　`[低风险]`
启用 `F401 F841 I W291 W293 W292 E701 E702 RUF100 UP015 RUF010`。
用 `--fix` 自动修复，逐个文件验证。
**注意**：`__init__.py` 与门面文件的 "unused import" 是再导出，用 `per-file-ignores` 排除，不删。

### 步骤 2 — 死代码清理　`[低风险]`
处理 vulture ≥90% 的 9 处（门面里无人使用的再导出）。

### 步骤 3 — 类型检查基线（只报告，不阻断）　`[零风险]`
引入 pyright，作用域限定协议层：
`app/models/`、`app/api/openai/`、`app/api/anthropic_routes.py`、`app/api/openai_responses_support.py`。
报错数存基线，新 PR 不允许新增。

### 步骤 4 — `core → services` 方向守护　`[中风险]`
新增 `tests/test_structure.py::test_core_does_not_import_services`，
对现有 33 处违规用显式 allowlist（每处带 TODO 说明），只允许列表变短。

### 步骤 5 — `app.core.config` 去环　`[中风险]`
1. `cute_translator → services.error_metadata` 的被依赖部分下沉为叶子模块；
2. `utils.image_validation → core.config` 反向依赖解除；
3. 门面只导出公开名，私有名改由调用方直接导入子模块。

### 步骤 6 — 拆分 CC 357 的 `_execute_workflow_stream_once`　`[高风险]`
`app/core/browser/workflow.py:1583`（1348 行 / 13 参数 / 覆盖 16.7%）。
**前置条件**：先补该工作流的端到端测试（用现有 fake driver）。
按阶段边界提取方法，5 个 `_` 前缀标志合并为 `_Continuation` 状态对象。

### 步骤 7 — `_execute_action` 注册表化　`[高风险]`
`app/services/command_engine_actions.py:860`（CC 123）。
改为 `dict[str, ActionHandler]`，每个 action 一个函数/类。

### 步骤 8 — `_cuteify_debug_message` 数据表化　`[中风险]`
`app/core/config_parts/cute_translator.py:891`（CC 127）。
内联正则抽为模块级数据表。

### 步骤 9 — 隔离 `patch_drissionpage.py`　`[中风险]`
1. 锁 DrissionPage minor 版本；
2. 启动校验 patch 生效，失败明确报错；
3. 长期改为运行时 monkey-patch，不再改写 site-packages。

---

## 执行记录

| 步骤 | commit | 状态 | 关键变化 |
| --- | --- | --- | --- |
| 计划 | `7fd43de` | ✅ | 评估 + 9 步计划、度量基线 |
| 0 | `24e4daa` | ✅ | 导入环棘轮：基线 93 模块 / 4 环 / 最大分量 63 |
| 1a | `7f4812c` | ✅ | 清理 1305 处空白（零语义改动） |
| 1b | `1dc7ff9` | ✅ | 统一 import 排序（215 文件，AST 校验零增删） |
| 1 | `15817cb` | ✅ | 删 132 处死导入 + 16 处死变量；`app/api/chat.py` 因字符串引用回退 |
| 4 | `f42d558` | ✅ | 分层棘轮：core→services 54 处、utils→core 11 处 |
| 5a | `92e7bd0` | ✅ | 抽 `app/core/error_codes.py`：**环中模块 93 → 81** |
| 5b | `04442d0` | ✅ | 断 `app.api.system` 内部环：**环中模块 81 → 78，导入环 4 → 3** |
| 3 | | ⬜ | 类型检查基线（pyright，只报告不阻断） |
| 5b | | ⬜ | 收窄 `config.py` 门面导出面（131 → 只留公开名） |
| 6 | | ⬜ | 拆分 CC 357 的 `_execute_workflow_stream_once`（需先补测试） |
| 7 | | ⬜ | `_execute_action`（CC 123）注册表化 |
| 8 | | ⬜ | `_cuteify_debug_message`（CC 127）数据表化 |
| 9 | | ⬜ | 隔离 `patch_drissionpage.py` |

### 当前度量（步骤 0 / 1 / 4 / 5a / 5b 之后）

| 指标 | 基线 | 当前 | 变化 |
| --- | --- | --- | --- |
| **环中模块** | 93 (38%) | **78 (32%)** | **−15** ✅ |
| **导入环个数** | 4 | **3** | −1 ✅ |
| 最大纠缠分量 | 63 | 61 | −2 ✅ |
| 测试 | 1331 passed | **1338 passed** | +7（新增棘轮）✅ |
| ruff 启用规则 | 5 条 | **14 条** | ✅ |
| 死导入 / 死变量 | 399 / 16 | 门面外已清零 | ✅ |
| 分层守护 | 无 | **2 条规则 + 基线** | ✅ |
| 覆盖率 | 41.8% | **42%** | ±0（见下） |
| CC > 50 的块 | 30 | 30 | 0（见下） |
| 最大函数 CC | 357 | 357 | 0（见下） |

> **覆盖率与复杂度为什么几乎没动？**
> 这两项不由「清理死代码」或「断环」决定，而取决于步骤 6–8 的三个巨型函数
> （CC 357 / 127 / 123）。它们单函数 1348 行、耦合在 61 模块的环里，
> **结构上无法测试**——不是不写测试，是写不进去。
> 正确顺序是：**先按阶段边界拆分，再补测试**，而不是先补测试。
> 本轮先把棘轮和断环做完，正是为了让拆分时每一步都可验证、不会越拆越糟。

### 剩余三个分量（断开难度递增）

| 分量 | 模块数 | 断开思路 |
| --- | --- | --- |
| `arena_image_generation` ↔ `arena_image_stream_observer` | 2 | 把 `ARENA_NATIVE_STOP_SELECTOR` 等常量下沉；再把 observer 用到的 4 个逻辑符号抽到第三模块 |
| `app.api.*` ↔ `services.adapter_updates` / `catalog_routing` | 15 | 找出 api → services 的回边，抽成接口或下沉 |
| core ↔ services 主纠缠 | 61 | **最大工程**：48 处 core→services 延迟 import，主流向是 `command_engine` / `request_manager` 两个单例。需引入依赖注入或事件回调反转方向，建议按「一次一个模块 + 棘轮锁进度」推进 |
