# 项目结构修复清单（已执行）

> 依据 [`docs/STRUCTURE-REVIEW.md`](./STRUCTURE-REVIEW.md) 的 P0 / P1 结论，执行「阶段 A + 阶段 B 的包边界部分」。
> 原则：**只做可验证的结构调整，不改变任何运行时行为**。
> 验证基线见 §5：改动前后同为 **2 个既有失败**，新增 13 个结构测试全部通过。

---

## 0. 总览

| 编号 | 类别 | 改动 | 风险 | 状态 |
| --- | --- | --- | --- | --- |
| A1 | 清理 | 删除 `app/core/browser.py`（模块/包同名死文件） | 无 | ✅ |
| A2 | 清理 | 合并 `CHANGELOG-3.0.1.md` → `CHANGELOG.md`，更新所有引用 | 无 | ✅ |
| A3 | 清理 | 删除与 `mimo_runtime_parser.py` 完全重复的 `mimo_parser.py` | 无 | ✅ |
| A4 | 归位 | `generate.py` → `examples/arena_image_batch.py`（去硬编码路径） | 无 | ✅ |
| A5 | 归位 | `perf_watch.py` → `tools/perf_watch.py` | 无 | ✅ |
| A6 | 工程化 | 新增 `.github/workflows/ci.yml` | 无 | ✅ |
| A7 | 元数据 | 版本一致性由结构测试守护（`VERSION` 仍是唯一来源） | 无 | ✅ |
| A8 | 测试组织 | 7 个手动脚本 → `tests/manual/`，并禁止被 pytest 收集 | 低 | ✅ |
| B1 | 包边界 | `updater` / `update_preserve` / `clean_profile` → `app/update/`，根目录留兼容壳 | 中 | ✅ |
| B2 | 入口 | 新增 `app/__main__.py`，支持 `python -m app` | 低 | ✅ |
| B3 | 防回归 | 新增 `tests/test_structure.py`（13 项结构棘轮） | 无 | ✅ |
| C1 | 暂缓 | `app/core/paths.py` + 消除 24 处 CWD 相对路径 | — | ⏸ 见 §6 |
| C2 | 暂缓 | 拆分 `tab_routes.py`(5142) / `chat.py`(3299) / `system.py`(2674) | — | ⏸ 见 §6 |
| C3 | 暂缓 | `config/` 只读 + `var/` 可写数据目录 | — | ⏸ 见 §6 |
| C4 | 暂缓 | `src/` 布局、包改名 `app` → `uwapi` | — | ⏸ 见 §6 |

---

## 1. A 类：清理与归位（零行为变更）

### A1 删除 `app/core/browser.py`

**问题**：`app/core/browser.py`（9 行转发）与 `app/core/browser/`（包）同名。CPython 的 `FileFinder`
在解析 `app.core.browser` 时先检查目录 + `__init__.py`，再检查同名模块，所以该文件**永远不会被导入**，
是死代码，也是「改了没生效」的坑。

**改动**：删除文件。`app/core/browser/__init__.py` 继续导出 `BrowserCore` / `get_browser` / `browser`，
所有 `from app.core.browser import ...` 的调用方无需修改。

**验证**：`python -c "from app.core.browser import get_browser"` 正常；全量测试通过。

### A2 合并重复的 CHANGELOG

**问题**：`CHANGELOG.md` 与 `CHANGELOG-3.0.1.md` 逐字节相同（`cmp` 退出码 0），两份文件迟早漂移。

**改动**：
- 删除 `CHANGELOG-3.0.1.md`，保留 `CHANGELOG.md` 为当前版本变更的唯一入口；
- `CHANGELOG-3.0.0.md` 保留为历史版本；
- `README.md` / `README.zh-CN.md` / `README.en.md` 的 4 处链接改为 `CHANGELOG.md`。

**验证**：`tests/test_structure.py::test_no_duplicate_changelog_copies`（按内容哈希拒绝重复副本）
+ `test_deleted_paths_are_not_referenced`（README 不得再引用已删文件）。

### A3 删除重复的 MiMo 解析器

**问题**：`app/core/parsers/mimo_parser.py` 与 `mimo_runtime_parser.py` 除前三行注释外完全相同
（`diff` 无输出），而 `config/parsers.json` 指向 runtime 版。

**改动**：删除 `mimo_parser.py`；`app/core/parsers/__init__.py` 的导出来源改为
`from .mimo_runtime_parser import MimoParser`；顺带修正 runtime 版的文件头注释。

**验证**：`MimoParser.__module__ == "app.core.parsers.mimo_runtime_parser"`，
由 `test_parser_registry_points_at_existing_module` 固化。

### A4 `generate.py` → `examples/arena_image_batch.py`

**问题**：根目录脚本硬编码 `C:\Users\QIU\Desktop\新建文件夹 (2)`、`C:\Users\QIU\.gemini\...`，
并会把参考图**复制到用户桌面**；在任何其他机器上都会失败，还会被误认为项目组成部分。

**改动**：
- 移动到 `examples/arena_image_batch.py`，明确标注「不是服务的一部分」；
- 全部路径与地址改为命令行参数（`--base-url` / `--path` / `--model` / `--output-dir` /
  `--prompt-file` / `--character-image` / `--style-image` / `--both` / `--api-key`）；
- 去掉「复制图片到桌面」的副作用；
- 请求与解析逻辑（`media` 字段、markdown 图片、data URL、远程 URL）保持原样。

**说明**：该文件不读取任何环境变量——否则会与 `app/core/settings_registry.py`
的「环境变量唯一来源」约束冲突（`tests/test_settings_registry.py` 会失败）。

### A5 `perf_watch.py` → `tools/perf_watch.py`

**改动**：移动文件，并同步 docstring 里的 4 条示例命令（`python tools/perf_watch.py ...`）。
`tools/` 默认被 Git 跟踪，不需要改 `.gitignore`。

### A6 新增 CI

**改动**：`.github/workflows/ci.yml`，三个 job：

| job | 内容 |
| --- | --- |
| `ruff` | `uv sync --frozen --no-dev` → `ruff check .` |
| `pytest` | Ubuntu + Windows 矩阵，`pytest -q -m "not browser and not real_browser"`（Node 20 供前端单测） |
| `structure` | 单独跑结构、`.env.example`、仓库卫生、uv 环境四组测试 |

此前仓库里那些「只有定期执行才有意义」的守护测试（Tailwind 产物等价性、`.env.example` 新鲜度、
三个棘轮、Python 版本下限）没有任何自动执行环境。

### A7 版本单一来源（不引入 `dynamic`）

**问题**：版本在 `VERSION`、`pyproject.toml:21`、`app/__init__.py` 三处出现，无一致性校验。

**改动**：**保持 `VERSION` 为唯一来源**，用结构测试把另外两处钉死：
`test_version_has_single_source` 断言 `VERSION == pyproject.toml [project].version == app.__version__`。

**为什么不改成 `dynamic = ["version"]`**：本项目 `[tool.uv] package = false` 且没有
`[build-system]`，没有任何 backend 会去计算动态版本；而 `importlib.metadata` 在
「未安装成 distribution」时也会抛 `PackageNotFoundError`。在现有发布方式下，测试兜底是正确解。

### A8 手动脚本隔离

**问题**：7 个需要真实服务/浏览器的手动脚本与 `test_*.py` 平铺在 `tests/` 根，
`ls tests/` 无法回答「这里有多少自动化测试」。

**改动**：
- `tests/manual/{browser_operations_studio, browser_preset_transfer, browser_site_capabilities,
  browser_upgraded_sites, browser_workflow_studio, e2e_concurrency_headed, e2e_idle_maintenance_headed}.py`
- 脚本内 `ROOT = Path(__file__).resolve().parents[1]` → `parents[2]`（e2e 的 `dirname` 链同步加一层）；
- `tests/conftest.py` 增加 `collect_ignore = ["manual"]`；
- 新增 `tests/manual/README.md`（依赖、运行方式、约定）；
- `tests/test_repo_hygiene.py` 的文件清单补上 `tests/manual/*.py`，手动脚本同样不允许被 `.gitignore` 忽略。

---

## 2. B1：包边界修复（本次最实质的改动）

### 问题

```python
# 改动前
app/api/system.py:42          from update_preserve import ...
app/api/system.py:2465...     from updater import ...
app/runtime/bootstrap.py:276  from clean_profile import ...
app/services/adapter_updates.py:151   from updater import ...
app/services/config/site_store.py:265 from updater import ...
```

`app` 反向依赖仓库根目录模块，只要 `sys.path` 上没有根目录就导入失败；
`pytest` 的 `pythonpath = ["."]` 又让这个缺陷永远不在测试里暴露。

### 改动

| 旧路径 | 新路径 | 说明 |
| --- | --- | --- |
| `updater.py` | `app/update/engine.py` | 自动更新引擎 |
| `update_preserve.py` | `app/update/preserve.py` | 保留规则 |
| `clean_profile.py` | `app/update/profile.py` | profile 清理（新增 `main(argv)`） |

配套修改：

1. **路径常量**：`engine.py` 的 4 处 `Path(__file__).parent` → `PROJECT_ROOT = Path(__file__).resolve().parents[2]`；
   `profile.py` 的 `PROJECT_ROOT` / `_resolve_profile_dir` 的 `base_dir` 同步修正
   （否则版本号会从 `app/update/VERSION` 读、profile 会指向 `app/update/chrome_profile`）。
2. **内部导入**：`engine.py` 改为 `from app.update.preserve import ...`。
3. **调用方**：`app/api/system.py`(6 处)、`app/runtime/bootstrap.py`、`app/services/adapter_updates.py`、
   `app/services/config/site_store.py` 全部改为 `app.update.*`，`app/` 内不再出现根目录顶层导入。
4. **兼容壳**（根目录保留）：`updater.py` / `update_preserve.py` / `clean_profile.py`
   用 `sys.modules[__name__] = _impl` 把实现模块**顶替**自身，而不是复制名字。

   为什么必须「顶替」而不是 `from x import *`：现有测试会 monkeypatch 模块级常量，例如
   ```python
   monkeypatch.setattr(updater, "MAX_UPDATE_MEMBER_COUNT", 1)
   ```
   复制名字会得到两份模块级状态，patch 不生效；顶替后全仓库只有一份。
   同时 `from updater import _safe_extract_zip`（下划线内部名）也照常可用。

5. **脚本入口不变**：`python updater.py` / `python clean_profile.py [dir]` 仍可用；
   `python update_preserve.py` 亦可。
6. **异常棘轮基线**：`tests/fixtures/broad_except_ratchet.json` 的 3 个键做**纯路径重命名**
   （`updater.py`→`app/update/engine.py` 等），计数完全不变（19 / 4 / 2），
   证明没有新增宽泛捕获、只是文件搬家。

### 为什么 `patch_drissionpage.py` 仍留在根目录

它必须在**导入 DrissionPage 之前**独立执行（`bootstrap` 用 subprocess 调用），
放进包内反而会引入「先 import app 再打补丁」的顺序风险；`tests/test_cdp_hygiene.py`
也按文件路径加载它。因此它是**刻意保留的独立工具**，不是漏网脚本。

---

## 3. B2：包入口

新增 `app/__main__.py`：

```python
def main() -> None:
    runpy.run_path(str(PROJECT_ROOT / "main.py"), run_name="__main__")
```

- 行为与 `uv run python main.py` **完全一致**（同一份 `main.py`、同一个 `__main__` 守卫，
  bootstrap 只执行一次）；
- 不复制启动逻辑，因此不存在第二份入口需要同步维护；
- `README.md` / `README.zh-CN.md` / `README.en.md` 各补一行等价入口说明；
- `test_package_entry_point_exists_and_is_thin` 固化：入口必须存在、必须用 `runpy`、
  **不得**自己调用 `bootstrap_runtime`。

---

## 4. B3：结构棘轮 `tests/test_structure.py`

把本次结论变成可执行约束（避免下次重构又漂回去）：

| 测试 | 约束 |
| --- | --- |
| `test_app_does_not_import_root_level_modules` | `app/` 顶层不得 `from updater/update_preserve/clean_profile/patch_drissionpage import` |
| `test_root_only_holds_known_modules` | 根目录只允许 `main.py`、`patch_drissionpage.py` 与 3 个兼容壳 |
| `test_compat_shims_point_at_new_implementation` | 兼容壳必须仍指向 `app.update.*` |
| `test_version_has_single_source` | `VERSION` == `pyproject` == `app.__version__` |
| `test_no_module_package_shadowing` | 禁止「模块与包同名」的死文件 |
| `test_every_python_package_has_init` | `app/` 下含 `.py` 的目录必须是包 |
| `test_no_duplicate_changelog_copies` | 禁止与 `CHANGELOG.md` 内容相同的副本 |
| `test_package_entry_point_exists_and_is_thin` | `python -m app` 可用且入口薄 |
| `test_deleted_paths_are_not_referenced` | README 不得引用已删除文件 |
| `test_parser_registry_points_at_existing_module` | 解析器注册表/包导出指向真实模块 |

---

## 5. 验证结果

环境：Linux + Python 3.11（`uv sync --python /usr/bin/python3`，沙箱内无法下载 3.12 的 standalone 包），
依赖按 `uv.lock` 安装。

| 指标 | 改动前 | 改动后 | 结论 |
| --- | --- | --- | --- |
| `pytest` 通过 | 1302 | **1315** | +13，全部为新增结构测试 |
| `pytest` 失败 | 2 | **2** | 同一批既有失败，未新增 |
| `pytest` 跳过 | 157 | 157 | 不变 |
| `ruff check .` | 通过 | 通过 | 无新增告警 |
| `tests/test_exception_ratchet.py` | 通过 | 通过 | 基线仅重命名，计数一致（19/4/2） |

既有失败（与本次改动无关，改动前就存在）：

1. `tests/test_grok_parser.py::test_grok_network_pattern_matches_completed_load_responses_endpoint`
2. `tests/test_site_workflow_upgrade.py::test_only_requested_sites_and_workflows_change_and_upgrade_is_repeatable`

额外的手工验证：

```bash
# 兼容壳可导入（含下划线内部名）
python -c "from updater import _safe_extract_zip, merge_site_records, get_current_version; print(get_current_version())"
# → 3.0.1

# 包边界：不把仓库根放进 sys.path 也能导入 app（模拟安装后的环境）
cd /tmp && PYTHONPATH=/path/to/universal-web-api python -c "import app, app.update.engine"

# 新入口与旧入口等价（会真正启动服务，仅结构检查时不要执行）
python -m app
```

---

## 6. 暂缓项及原因

这些项在评审里列为 P1，但**不能只改目录**，必须连带改测试或数据迁移，故单独排期。

### C1 `app/core/paths.py` + 消除 CWD 相对路径

现状：`main.py` 的 `Path("static")` / `Path("download_images")`，`browser/media.py` 的 4 处
`save_dir = Path("download_images")`，`extractors/media_extractor.py` 1 处。

**为什么暂缓**：`tests/test_review_fixes_p1.py` 等用例刻意用 `monkeypatch.chdir(tmp_path)` +
`Path("download_images")` 做**磁盘隔离**，断言「图片写进了临时目录」。改成绝对路径会让这些用例
开始往仓库里写文件。正确做法是同 PR 内引入可注入的 `DOWNLOAD_DIR` 并改写这些用例，
属于「改行为 + 改测试」的组合改动，不适合与本次结构整理混在一起。

### C2 拆分巨型路由文件

`app/api/tab_routes.py`(5142) / `chat.py`(3299) / `system.py`(2674)。路由**注册顺序**会影响
带路径参数的匹配（`/url/{route_domain}/...` vs 静态路径），必须逐一比对 `app.routes` 的最终顺序；
建议按 `openai/ anthropic/ admin/` 重排时，用「导出 `app.routes` 快照 → 拆分 → 断言快照一致」的方式做。

### C3 `config/` 只读 + `var/` 可写

涉及用户数据迁移（`runtime.sqlite3`、`commands.json`、`sites.local.json`…）、`update_preserve.py`
的保留清单、备份/恢复接口，需要迁移脚本与回滚方案。

### C4 `src/` 布局 / 包改名

评审结论已说明：本项目 `package = false`、按压缩包替换文件发布，`src/` 收益为 0；
包改名会打断更新器路径与用户数据定位。除非要发布到 PyPI，否则不做。

---

## 7. 变更文件一览

```
删除    CHANGELOG-3.0.1.md
删除    app/core/browser.py
删除    app/core/parsers/mimo_parser.py
新增    .github/workflows/ci.yml
新增    app/__main__.py
新增    app/update/__init__.py
新增    tests/test_structure.py
新增    tests/manual/README.md
新增    docs/PROJECT-STRUCTURE.md（仓库地图）
新增    docs/STRUCTURE-REVIEW.md（对照评审）
新增    docs/STRUCTURE-FIX-CHECKLIST.md（本文件）
移动    updater.py            → app/update/engine.py
移动    update_preserve.py    → app/update/preserve.py
移动    clean_profile.py      → app/update/profile.py
移动    generate.py           → examples/arena_image_batch.py（并参数化）
移动    perf_watch.py         → tools/perf_watch.py
移动    tests/browser_*.py    → tests/manual/（5 个）
移动    tests/e2e_*_headed.py → tests/manual/（2 个）
修改    app/api/system.py
修改    app/runtime/bootstrap.py
修改    app/services/adapter_updates.py
修改    app/services/config/site_store.py
修改    app/core/parsers/__init__.py
修改    app/core/parsers/mimo_runtime_parser.py
修改    tests/conftest.py
修改    tests/test_repo_hygiene.py
修改    tests/fixtures/broad_except_ratchet.json
修改    README.md / README.zh-CN.md / README.en.md
保留    updater.py / update_preserve.py / clean_profile.py（兼容壳，下一大版本再删）
```
