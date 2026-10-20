"""站点配置校验（validate_site_config）的专项测试。

覆盖两个层面：

1. **行为**：三个配置段（stream_config / file_paste / prompt_padding）各自
   的合法值与非法值，以及它们在整体函数里的组合语义。这些用例在拆分前
   就已经成立（改动前跑同一批断言结果相同），用于证明拆分是纯结构变化。
2. **结构**：锁定"三段各自独立、按表分派"这个形状，防止日后又被塞回一个
   巨型函数、或引入跨段耦合。
"""

import ast
import pathlib

import pytest

from app.models.schemas import (
    _SITE_CONFIG_VALIDATORS,
    _validate_file_paste,
    _validate_prompt_padding,
    _validate_stream_config,
    validate_site_config,
)

SCHEMAS = pathlib.Path(__file__).resolve().parents[1] / "app" / "models" / "schemas.py"


def base_config(**extra):
    """最小合法骨架，再叠加待校验的配置段。"""
    cfg = {"selectors": {}, "workflow": []}
    cfg.update(extra)
    return cfg


# --------------------------------------------------------------------------
# 1. 前置守卫（与配置段无关）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cfg",
    [
        {},
        {"selectors": {}},
        {"workflow": []},
        {"selectors": [], "workflow": []},
        {"selectors": {}, "workflow": {}},
    ],
)
def test_missing_or_malformed_required_keys_rejected(cfg):
    assert validate_site_config(cfg) is False


def test_valid_minimal_config_accepted():
    assert validate_site_config(base_config()) is True


def test_invalid_workflow_step_propagates():
    assert validate_site_config({"selectors": {}, "workflow": [{"action": "bogus"}]}) is False


# --------------------------------------------------------------------------
# 2. stream_config 段
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stream_config",
    [
        {},
        {"mode": "dom"},
        {"mode": "network"},
        {"mode": "  DOM  "},  # 大小写与空白被规范化
        {"hard_timeout": 60},
        {"request_transport": {"mode": "xhr", "profile": "p", "options": {}}},
    ],
)
def test_stream_config_valid_values(stream_config):
    assert _validate_stream_config(base_config(stream_config=stream_config)) is True
    assert validate_site_config(base_config(stream_config=stream_config)) is True


@pytest.mark.parametrize(
    "stream_config",
    [
        [],  # 不是 dict
        "dom",
        {"mode": "bogus"},  # 只允许 dom / network
        {"hard_timeout": "60"},  # 必须 int
        {"request_transport": "xhr"},
        {"request_transport": {"mode": 1}},
        {"request_transport": {"profile": 1}},
        {"request_transport": {"options": []}},
    ],
)
def test_stream_config_invalid_values(stream_config):
    assert _validate_stream_config(base_config(stream_config=stream_config)) is False
    assert validate_site_config(base_config(stream_config=stream_config)) is False


# --------------------------------------------------------------------------
# 3. file_paste 段
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "file_paste",
    [
        {},
        {"enabled": True, "threshold": 120000, "temp_file_type": "chunk"},
        {"temp_file_type": " PDF "},  # 大小写与空白被规范化
        {"temp_file_type": "error"},
        {"state_probe": {"enabled": True, "code": "c"}},
        {"send_confirmation": {"retry_interval": 1}},
    ],
)
def test_file_paste_valid_values(file_paste):
    assert _validate_file_paste(base_config(file_paste=file_paste)) is True
    assert validate_site_config(base_config(file_paste=file_paste)) is True


@pytest.mark.parametrize(
    "file_paste",
    [
        [],
        {"enabled": "yes"},  # 必须 bool
        {"threshold": "120000"},  # 必须 int
        {"temp_file_type": "zip"},  # 不在允许集合内
        {"hint_text": 1},
        {"state_probe": "x"},
        {"state_probe": {"enabled": "x"}},
        {"send_confirmation": "x"},
    ],
)
def test_file_paste_invalid_values(file_paste):
    assert _validate_file_paste(base_config(file_paste=file_paste)) is False
    assert validate_site_config(base_config(file_paste=file_paste)) is False


# --------------------------------------------------------------------------
# 4. prompt_padding 段
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt_padding",
    [
        {},
        {"enabled": True},
        {"marker_text": "M", "segments_per_side": 2},
        {"random_insert_enabled": False, "random_insert_chars": "abc"},
    ],
)
def test_prompt_padding_valid_values(prompt_padding):
    assert _validate_prompt_padding(base_config(prompt_padding=prompt_padding)) is True
    assert validate_site_config(base_config(prompt_padding=prompt_padding)) is True


@pytest.mark.parametrize(
    "prompt_padding",
    [
        [],
        {"enabled": 1},
        {"marker_text": 1},
        {"segments_per_side": "2"},
        {"random_insert_enabled": "no"},
        {"random_insert_chars": 1},
    ],
)
def test_prompt_padding_invalid_values(prompt_padding):
    assert _validate_prompt_padding(base_config(prompt_padding=prompt_padding)) is False
    assert validate_site_config(base_config(prompt_padding=prompt_padding)) is False


# --------------------------------------------------------------------------
# 5. 段与段之间的独立性
# --------------------------------------------------------------------------


def test_sections_are_independent_one_bad_section_fails_whole():
    good = {"mode": "dom"}
    bad = {"mode": "bogus"}
    assert validate_site_config(base_config(stream_config=good, file_paste={"enabled": True})) is True
    assert validate_site_config(base_config(stream_config=bad, file_paste={"enabled": True})) is False
    assert validate_site_config(base_config(stream_config=good, file_paste={"enabled": "x"})) is False


def test_absent_section_is_not_validated():
    """缺失的段不该被校验——否则会给历史配置引入新的失败。

    这一条同时锁住"按 key 是否存在来分派"的形状：如果将来改成无条件校验，
    这个用例会立刻失败。
    """
    assert validate_site_config(base_config()) is True
    # 单个配置段缺失时整体依然通过
    for key, _ in _SITE_CONFIG_VALIDATORS:
        assert validate_site_config({k: v for k, v in base_config().items() if k != key}) is True


def test_validators_require_their_own_key_present():
    """校验函数的前置条件：config 必须包含自己那一段。

    "键是否存在"的判断留在 validate_site_config 的分派处，校验函数内部不
    重复判断（方法体与拆分前逐字节一致）。这条用例把这个契约固定下来，
    免得有人直接调用私有校验函数时被 KeyError 打个措手不及。
    """
    for key, validator in _SITE_CONFIG_VALIDATORS:
        with pytest.raises(KeyError):
            validator(base_config())


def test_validators_only_read_their_own_key(monkeypatch):
    """每个校验函数只应读取自己那一段，不碰其它段。

    实现方式是给每段放一个"读就炸"的哨兵，段内不读它，整体就必须通过。
    """

    class Boom(dict):
        def __getitem__(self, key):
            raise AssertionError(f"读取了不该读的段: {key}")

    spy = Boom()
    for key, validator in _SITE_CONFIG_VALIDATORS:
        # 只有自己那一段是真实值，其余都是哨兵
        cfg = {k: spy for k, _ in _SITE_CONFIG_VALIDATORS}
        cfg[key] = {"mode": "dom", "enabled": True, "marker_text": "m"}
        assert validator(cfg) is True


# --------------------------------------------------------------------------
# 6. 结构锁定：防止重新长回巨型函数
# --------------------------------------------------------------------------


def test_site_config_validators_table_is_complete():
    """校验表必须覆盖 validate_site_config 里原本的每一个配置段。"""
    assert [k for k, _ in _SITE_CONFIG_VALIDATORS] == [
        "stream_config",
        "file_paste",
        "prompt_padding",
    ]


def test_validator_signature_is_config_only():
    import inspect

    for _, validator in _SITE_CONFIG_VALIDATORS:
        params = list(inspect.signature(validator).parameters)
        assert params == ["config"], f"{validator.__name__} 的入参应为 (config,)，实际 {params}"
        assert inspect.signature(validator).return_annotation is bool


def test_validate_site_config_does_not_inline_section_logic():
    """validate_site_config 里不应再出现任何具体配置段的字段校验。

    出现 stream_config / file_paste / prompt_padding 之外的东西是可以的；
    出现字段级 isinstance 判据就说明又内联回去了。
    """
    tree = ast.parse(SCHEMAS.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "validate_site_config"
    )

    # 注意：函数开头的 selectors / workflow 前置守卫仍会用到 isinstance，那是
    # 它自己的职责，不算"内联段内逻辑"。因此这里只禁止直接索引配置段。

    # 三个段名只允许出现在分派用的字符串常量里，不应再有 config["..."] 直接取值
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript):
            src = ast.unparse(node)
            for key in ("stream_config", "file_paste", "prompt_padding"):
                assert f'"{key}"' not in src, f"validate_site_config 仍在直接索引 {key}"


def test_section_validators_are_module_private():
    """拆分出的校验函数是内部实现，不应从 app.models 公开导出。"""
    import app.models as pkg

    for name in ("_validate_stream_config", "_validate_file_paste", "_validate_prompt_padding"):
        assert not hasattr(pkg, name), f"{name} 不应出现在 app.models 的公开接口里"
