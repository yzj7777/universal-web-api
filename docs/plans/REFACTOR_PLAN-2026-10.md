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
| 0 | | | |
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
