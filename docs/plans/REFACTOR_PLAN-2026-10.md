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
| 5c | `eb13d08` | ✅ | 收窄 `config.py` 门面：**132 个再导出 → 20 个**（删 112 个零引用名） |
| 3 | `cffe404` | ✅ | 协议层 mypy 基线：7 文件 / 51 条 + 棘轮 |
| 6a-test | `1e49ed0` | ✅ | 先补入口守卫行为测试 14 项（拆分的前置条件） |
| 6a | `106f752` | ✅ | 提取入口守卫：**CC 357 → 350**（56 组差分零差异） |
| 6b | `9e1dc11` | ✅ | 提取媒体后处理 159 行：**CC 350 → 304**（字节级等价） |
| 6c | `6b6e09e` | ✅ | 提取命令中断处理 62 行：**CC 304 → 291**（21/21 语句等价） |
| 6d | `1a2085b` | ✅ | 合并两处重复的 Battle 页面判定：**CC 291 → 289** |
| 7 | | ⬜ | `_execute_action`（CC 123）注册表化 |
| 8 | `2548ac0` | ✅ | `_cuteify_debug_message` 619 行/CC 127 → 10 段各 CC 13 |
| 9 | `7975510` | ✅ | 修复补丁静默失败 + 写回复核 + 版本上界（10 项新测试） |

### 当前度量（步骤 0 / 1 / 3 / 4 / 5a / 5b / 5c / 6a–6d / 8 / 9 之后）

| 指标 | 基线 | 当前 | 变化 |
| --- | --- | --- | --- |
| **环中模块** | 93 (38%) | **78 (32%)** | **−15** ✅ |
| **导入环个数** | 4 | **3** | −1 ✅ |
| 最大纠缠分量 | 63 | 61 | −2 ✅ |
| **测试** | 1331 passed | **1414 passed** | **+83** ✅ |
| **ruff 启用规则** | 5 条 | **14 条** | ✅ |
| **棘轮数量** | 2 条 | **5 条**（异常 / 驱动 / 导入环 / 分层 / 类型） | ✅ |
| **类型检查** | 0 模块 | **协议层 7 文件、基线 51 条** | ✅ |
| 死导入 / 死变量 | 399 / 16 | 门面外已清零 | ✅ |
| CC > 50 的块 | 30 | **29** | −1 ✅ |
| 该巨型函数 CC | 127 | **拆为 10 段，各 CC 13–14** | ✅ |
| **`_execute_workflow_stream_once` CC** | **357** | **289** | **−68** ✅ |
| 该函数行数 | 1348 | 1015 | −333 ✅ |
| **`config.py` 导出面** | 132 个（106 私有） | **20 个** | **−112** ✅ |
| 覆盖率 | 41.8% | **42%** | ±0（见下） |

> **覆盖率为什么几乎没动？**
> 取决于步骤 6 的 `_execute_workflow_stream_once`（覆盖率仅 16.7%）与
> 步骤 7 的 `_execute_action`（CC 123）。它们耦合在 61 模块的环里，
> **结构上写不进测试**——不是不写，是写不进去。
> 正确顺序是**先按阶段边界拆分，再补测试**，步骤 6a–6d 正是这么做的：
> 拆出的 4 个方法各自有了隔离测试（共 56 项），但主函数自身仍是
> 端到端拼装，覆盖率要等步骤 6e/7 完成后才会明显抬升。

### 本轮额外发现并修复的真实缺陷

**`patch_drissionpage.py` 的失败是完全静默的（步骤 9）**
`main()` 从不返回非零退出码，因此 `bootstrap.py` 里
`if result.returncode != 0` 的告警**永远不会触发**。而本项目启动时直接改写
DrissionPage 源码，一旦注入点因升级而消失，表现只是"网络监听模式不可用"，
进程照常启动，极难排查。已改为明确退出码（0/1/2）+ 写回后复核
（`verify_patch()`），并新增 10 项测试。

### 剩余工作

| 步骤 | 内容 | 难度 |
| --- | --- | --- |
| 6e | 继续拆分 `_execute_workflow_stream_once`（现 1015 行 / CC 289）：逐步预处理段（~120 行，含 continue/break/yield）与动作派发 Try（195 行） | 高 |
| 7 | `_execute_action`（CC 123）改为 `dict[str, ActionHandler]` 注册表 | 高 |

### 步骤 6 的拆分方法（可复用于其它巨型函数）

CC 357 的函数不是靠"勇气"拆的，而是靠**每一步都能拿出等价性证据**。
四次提交分别用了三档证据，代价递增：

| 档 | 适用 | 做法 | 证据 |
| --- | --- | --- | --- |
| **A · 字节搬家** | 被提取的块与外层只有数据耦合，没有 `break`/`continue`/`return` | **形参名与原有局部变量完全同名**，方法体只需改缩进 | `ast.get_source_segment` 逐字节比对（6b：159 行一致） |
| **B · 语句等价** | 块内有控制流跳转，必须改写成返回值 | 引入出口对象（如 `_InterruptOutcome`），跳转 → `return` | AST 展平后逐条比对，剔除已知改写（6c：21/21 语句一致） |
| **C · 行为差分** | 无法结构化比对（如守卫重排） | 改前/改后双实现同输入对比 | 归一化 `id`/`created` 后逐字节比对（6a：56 组零差异） |

三档都要求**先有测试或同步补测试**。步骤 6 累计新增 56 项测试，其中
入口守卫、中断出口、Battle 判定等都是**提取之后才第一次变得可测**的路径——
这正印证了评估里的判断：不是不写测试，是结构不允许写。

拆到 289 之后，剩下的块都不再是"独立的阶段"，而是与主循环变量深度
交织（逐步预处理段会写 13 个后续要用的变量，动作派发 Try 的三个
handler 都以 break 收尾）。继续拆需要先把循环状态收敛成显式的状态对象，
风险等级上升，因此**暂停在 289，转入步骤 7**。

### 剩余三个导入环分量（断开难度递增）

| 分量 | 模块数 | 断开思路 |
| --- | --- | --- |
| `arena_image_generation` ↔ `arena_image_stream_observer` | 2 | 需把 `ArenaImageGenerationGuard` 等 4 个被多处依赖的符号抽到第三模块；收益仅 2 个模块，故本轮主动跳过 |
| `app.api.*` ↔ `services.adapter_updates` / `catalog_routing` | 15 | 找出 api → services 的回边，抽成接口或下沉 |
| core ↔ services 主纠缠 | 61 | **最大工程**：48 处 core→services 延迟 import，主流向是 `command_engine` / `request_manager` 两个单例。需引入依赖注入或事件回调反转方向，建议按「一次一个模块 + 棘轮锁进度」推进 |

### 一条重要的结构性结论（供步骤 6/7 参考）

类型检查全量跟进导入时出现 **1415 条 attr-defined**，绝大多数源于项目的
**mixin 组合模式**：如 `WorkflowExecutorSendMixin` 直接使用
`self._attachment_monitor` / `self.tab`，而这些属性由最终组合类里的其它 mixin
提供，单独分析某个 mixin 时静态检查看不到。即 mixin 之间靠**隐式契约**耦合。
拆分巨型类时应顺带把这些契约显式化（宿主类声明或 Protocol），
否则类型检查的收益会被这类噪音长期掩盖。
