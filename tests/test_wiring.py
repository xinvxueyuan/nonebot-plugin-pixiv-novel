"""被动 hook 与 4 个命令的**接线**断言。

为什么单独一个文件：这类错误纯函数测试**看不见** ——
「hook 的 priority / block 取什么值」全写在装饰器参数里，逻辑函数一个都不受影响。
变异一下（把 hook 改回 priority=10 或 block=True）这里必须变红。

钉住的两条：
  1. 命令 `priority=10, block=True`、hook `priority=20, block=False`
     → `获取全文 <链接>` 只走命令那条路，**不会**被 hook 再回一次卡片；
       hook 也不吞别人的消息（别的插件照常收到）。
  2. 命令集合恰好是那 4 个（多出来 / 少掉都要被发现）。
"""

import re

import pytest

pytest.importorskip("nonebot")

import nonebot_plugin_pixiv_novel as plugin

COMMAND_MATCHERS = ("subscribe_cmd", "unsubscribe_cmd", "list_cmd", "text_cmd")
EXPECTED_COMMANDS = {"订阅", "退订", "订阅列表", "获取全文"}


def _command_names(matcher) -> list[str]:
    """从 matcher 的 rule 里抠出命令名。

    NoneBot 2.5 把命令名藏在 `rule.dependent` 的私有属性里（`dep.call` 取到的是 None），
    可读的稳定形式是 repr：`Rule(Dependent(call=Command(cmds=(('订阅',),))))`。
    所以这里用 repr + 正则提取。

    如果哪天 NoneBot 改了 repr，`_require_extraction_works()` 会先炸 ——
    而不是让这些断言对着**空集合**真空通过。
    """
    return re.findall(r"'([^']+)'", str(getattr(matcher, "rule", "")))


def _require_extraction_works() -> None:
    """护栏：提取手段必须真的能拿到命令名，否则整组测试是假的。"""
    got = _command_names(plugin.subscribe_cmd)
    assert got, (
        "从 rule repr 里提取不到命令名了（NoneBot 换了 repr 或 rule 结构）。"
        "请更新 _command_names 的提取方式，别让接线测试真空通过。"
    )


def test_extraction_guard():
    _require_extraction_works()


def test_command_names_are_exactly_the_four_expected():
    _require_extraction_works()
    found: set[str] = set()
    for name in COMMAND_MATCHERS:
        found.update(_command_names(getattr(plugin, name)))
    assert found == EXPECTED_COMMANDS, f"命令集合变了：{found}"


def test_commands_block_and_run_before_the_hook():
    """**接线核心**（变异测试的靶子）。

    命令 `block=True` 会阻断**更低优先级** matcher 的传播，
    hook 又在更低的 20 → `获取全文 <pixiv链接>` **不会**被回两次。
    """
    for name in COMMAND_MATCHERS:
        m = getattr(plugin, name)
        assert m.priority == 10, f"{name}.priority 变成 {m.priority}，顺序关系被破坏"
        assert m.block is True, f"{name}.block 必须为 True，否则 hook 会对同一条消息再回一次"


def test_hook_runs_last_and_never_blocks():
    assert plugin.url_hook.priority == 20, (
        f"url_hook.priority 变成 {plugin.url_hook.priority} —— "
        f"它必须**大于**命令的 10，否则命令的 block 拦不住它"
    )
    assert plugin.url_hook.block is False, (
        "url_hook 必须是旁观者（block=False）；改成 True 会吞掉别人的消息"
    )


def test_hook_has_no_command_rule():
    """hook 不该绑命令规则（否则就变成第 5 个命令了）。"""
    assert _command_names(plugin.url_hook) == []


def test_hook_config_defaults():
    assert plugin.plugin_config.pixiv_url_hook_enabled is True
    assert plugin.plugin_config.pixiv_url_hook_cooldown == 60
