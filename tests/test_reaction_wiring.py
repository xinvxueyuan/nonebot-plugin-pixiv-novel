"""表态接线：三态（处理中 / 完成 / 失败）且**终态发生在消息发出之后**。

用户两条明确要求（2026-10-02）：
  ① 慢操作要有 🔨/🍼/❌ 三态反馈；
  ② **成功表态移到发完消息之后**，随后追加「**失败表态也移到发完消息之后**」。

② 不是锦上添花 —— 它决定了代码结构：`with_reaction` 只在函数返回/抛出时才打终态，
所以「回文案/发卡片」的动作必须**在被装饰的函数内部**。把发送留在外面
（无论 handler 还是独立小函数）都会退化成「先 ❌ 再回文案」。
本文件里带「顺序」字样的用例就是钉这件事的。

边界：**库内部正确性由库自己的测试负责**（EMOJI_MAP 取值、私聊跳过、
MatcherException 判成功等）。这里只验证插件的接线。
"""

from types import SimpleNamespace

import pytest
import source_introspect
from conftest import load_fixture
from nonebot.adapters.onebot.v11 import GroupMessageEvent

reactlib = pytest.importorskip(
    "nonebot_plugin_message_reaction",
    reason="表态库是可选依赖，拿不到它的源码时跳过（插件降级路径另有测试）",
)

from nonebot_plugin_pixiv_novel import (  # noqa: E402
    _card_flow,
    _deliver_full_text,
    _fetch_detail_and_text,
    reaction_available,
)


def _r18_detail():
    """真实录制的 R18 作品详情。

    ⚠️ 必须取 `["novel"]` 再传给卡片构造函数：App 接口的响应顶层是
    `{"novel": {...}}`，传整个响应体进去的话 `novel.user` 会是 `None`，
    报错点看着像「卡片组装坏了」，其实只是测试喂错了层（踩过）。
    """
    return load_fixture("novel_detail_r18.json")["novel"]


def _group_event():
    """真实 `GroupMessageEvent` 的替身构造。

    **不能用 SimpleNamespace**：插件里判群聊/私聊走的是
    `isinstance(event, GroupMessageEvent)`，鸭子类型的假事件会被判成私聊 ——
    于是渠道轴按「私聊」走，测试结论全是错的（而且看起来只是「被策略拒绝了」）。

    `model_construct` 绕开 pydantic 的必填字段校验（真实事件有十几个字段），
    但拿到的仍是**真类实例**，`isinstance` 与 `get_user_id()` 都正常。
    """
    return GroupMessageEvent.model_construct(group_id=1094538078, user_id=1330509996)


@pytest.fixture
def db(tmp_path):
    """卡片映射要落库，`_card_flow` 的发送路径会写它。"""
    from nonebot_plugin_pixiv_novel import store

    store.init(tmp_path / "t.sqlite3")
    return tmp_path


@pytest.fixture
def use_event(monkeypatch):
    """把「当前事件」喂给库。

    ⚠️ 不能用 `current_event.set(event)` + reset：`ContextVar` 的 token 只能在
    创建它的上下文里 reset，而 sync fixture 与 async 测试体不是同一个上下文
    （会 `ValueError: token was created in a different Context`）。
    直接替换库模块上的对象最省事，也正好覆盖「库从模块全局取它」这一事实。

    不替换的话，`with_reaction` 会走 `LookupError` 分支**静默跳过所有表态**，
    于是「表态有没有发出」的断言全部落空（看起来通过，其实什么都没测）。
    """

    class _Current:
        def __init__(self, event):
            self._event = event

        def get(self):
            return self._event

    def _use(event):
        monkeypatch.setattr(reactlib.reaction, "current_event", _Current(event))
        return event

    return _use


@pytest.fixture
def timeline(monkeypatch):
    """**同一条时间线**记录表态与发送，用于断言先后顺序。

    这是本轮的核心测试装置：分成两个列表就只能断言「都发生了」，
    断言不了「谁先谁后」—— 而用户这次要改的恰恰是顺序。
    """
    events: list[str] = []
    holder = SimpleNamespace(events=events)

    async def fake_reaction(event, status):
        events.append(f"表态:{status}")
        return True

    # 打的是**库模块**的属性（而不是插件的导入名）—— `with_reaction` 在调用时
    # 才从模块全局取 `message_reaction`，所以这样能拦住所有被装饰的函数。
    monkeypatch.setattr(reactlib.reaction, "message_reaction", fake_reaction)

    def patch_send(matcher, label, response=None):
        """拦 `matcher.send()`，把「发了什么」记进同一条时间线。

        替身是**普通函数**（不是 classmethod）也没关系：通过类访问时不会被绑定，
        `matcher.send(msg)` 正好只传一个参数。
        """

        async def fake_send(message, **kwargs):
            events.append(f"发送:{label}")
            return response

        monkeypatch.setattr(matcher, "send", fake_send)

    holder.patch_send = patch_send
    return holder


# ── 插件确实加载到了真库，而不是降级替身 ──────────────────────────


def test_plugin_actually_loaded_the_real_library():
    """这条是**假绿的守门员**。

    插件的 `try: from nonebot_plugin_message_reaction import ...` 失败时会静默
    退到替身；没有这个断言，下面所有表态用例都会在「库根本没装上」的情况下
    通过 —— 而生产里同样会静默没有表情，谁也不会发现。
    """
    assert reaction_available is True, (
        "插件退到了表态降级替身，下面的断言测的不是真库的接线"
    )


# ── 顺序：终态表态必须在消息「发出之后」（本轮用户要求的重点）──────


async def test_card_flow_sends_card_before_done_reaction(
    timeline, use_event, db, monkeypatch
):
    """URL hook 成功：🔨 → 发卡片 → 🍼。

    装饰器只在函数返回时才打终态，所以卡片发送必须在被装饰的函数内部；
    把发送挪到外面这条断言就变红（见 `_card_flow` 的 docstring）。
    """
    from nonebot_plugin_pixiv_novel import client, url_hook

    async def detail(_novel_id):
        return _r18_detail()

    async def cover(*args, **kwargs):
        return b""

    monkeypatch.setattr(client, "novel_detail", detail)
    monkeypatch.setattr(client, "download_cover", cover)
    timeline.patch_send(url_hook, "卡片", response={"message_id": 123456})
    use_event(_group_event())

    await _card_flow("novel", 29277050)

    assert timeline.events == ["表态:resolving", "发送:卡片", "表态:done"]


async def test_card_flow_sends_failure_text_before_fail_reaction(
    timeline, use_event, db, monkeypatch
):
    """URL hook 失败：🔨 → 回文案 → ❌。**用户追加要求的那一条**。

    旧实现在这一步是「❌ → 回文案」（异常先穿出被装饰的函数，装饰器已经打过 ❌，
    handler 才去 finish 文案），用户看到的是「先红叉、再出现失败说明」。
    """
    from nonebot_plugin_pixiv_novel import client, url_hook

    async def boom(_novel_id):
        raise RuntimeError("pixiv 挂了")

    monkeypatch.setattr(client, "novel_detail", boom)
    timeline.patch_send(url_hook, "失败文案")
    use_event(_group_event())

    with pytest.raises(RuntimeError, match="pixiv 挂了"):
        await _card_flow("novel", 29277050)

    assert timeline.events == ["表态:resolving", "发送:失败文案", "表态:fail"]


async def test_fulltext_sends_text_before_done_reaction(
    timeline, use_event, monkeypatch
):
    """获取全文成功：🔨 → 发正文 → 🍼。"""
    from nonebot_plugin_pixiv_novel import client, plugin_config, text_cmd

    async def detail(_novel_id):
        return _r18_detail()

    async def text(_novel_id):
        return "正文"

    monkeypatch.setattr(client, "novel_detail", detail)
    monkeypatch.setattr(client, "novel_text", text)
    monkeypatch.setattr(plugin_config, "pixiv_text_targets", ["group"])
    monkeypatch.setattr(plugin_config, "pixiv_r18_text_allow_group", True)
    timeline.patch_send(text_cmd, "正文")
    use_event(_group_event())

    await _deliver_full_text(_group_event(), 29277050)

    assert timeline.events == ["表态:resolving", "发送:正文", "表态:done"]


async def test_fulltext_sends_failure_text_before_fail_reaction(
    timeline, use_event, monkeypatch
):
    """获取全文失败：🔨 → 回文案 → ❌。"""
    from nonebot_plugin_pixiv_novel import client, text_cmd

    async def boom(_novel_id):
        raise RuntimeError("超时")

    monkeypatch.setattr(client, "novel_detail", boom)
    timeline.patch_send(text_cmd, "失败文案")
    use_event(_group_event())

    with pytest.raises(RuntimeError, match="超时"):
        await _deliver_full_text(_group_event(), 29277050)

    assert timeline.events == ["表态:resolving", "发送:失败文案", "表态:fail"]


async def test_text_delivery_failure_also_reacts_fail_after_the_message(
    timeline, use_event, monkeypatch
):
    """正文交付阶段才炸（详情/正文都拿到了）：同样「先回文案、再 ❌」。"""
    from nonebot_plugin_pixiv_novel import client, handlers, plugin_config, text_cmd

    async def detail(_novel_id):
        return _r18_detail()

    async def text(_novel_id):
        return "正文"

    async def boom(*args, **kwargs):
        raise RuntimeError("发不出去")

    monkeypatch.setattr(client, "novel_detail", detail)
    monkeypatch.setattr(client, "novel_text", text)
    monkeypatch.setattr(plugin_config, "pixiv_text_targets", ["group"])
    monkeypatch.setattr(plugin_config, "pixiv_r18_text_allow_group", True)
    monkeypatch.setattr(handlers, "deliver_novel_text", boom)
    timeline.patch_send(text_cmd, "失败文案")
    use_event(_group_event())

    with pytest.raises(RuntimeError, match="发不出去"):
        await _deliver_full_text(_group_event(), 29277050)

    assert timeline.events == ["表态:resolving", "发送:失败文案", "表态:fail"]


# ── 三态取值：拒绝 ≠ 失败 ────────────────────────────────────────


async def test_policy_denial_is_done_not_fail_but_still_after_the_message(
    timeline, use_event, monkeypatch
):
    """策略拒绝（R18 不发群等）**不是失败**，不该打 ❌。

    它是「正常处理并给出了答复」，打 ❌ 会让人以为 bot 崩了。
    同时它也得遵守顺序要求：先回拒绝文案，再打 🍼。
    """
    from nonebot_plugin_pixiv_novel import client, plugin_config, text_cmd

    async def detail(_novel_id):
        return _r18_detail()

    monkeypatch.setattr(client, "novel_detail", detail)
    monkeypatch.setattr(plugin_config, "pixiv_text_targets", ["group"])
    monkeypatch.setattr(plugin_config, "pixiv_r18_text_allow_group", False)
    monkeypatch.setattr(plugin_config, "pixiv_admin_ids", [])
    timeline.patch_send(text_cmd, "拒绝文案")
    use_event(_group_event())

    await _deliver_full_text(_group_event(), 29277050)

    assert timeline.events == ["表态:resolving", "发送:拒绝文案", "表态:done"]


async def test_empty_body_is_done_not_fail(timeline, use_event, monkeypatch):
    """作品没有正文：也是「正常答复」，打 🍼 而不是 ❌。"""
    from nonebot_plugin_pixiv_novel import client, plugin_config, text_cmd

    async def detail(_novel_id):
        return _r18_detail()

    async def empty(_novel_id):
        return "   "

    monkeypatch.setattr(client, "novel_detail", detail)
    monkeypatch.setattr(client, "novel_text", empty)
    monkeypatch.setattr(plugin_config, "pixiv_text_targets", ["group"])
    monkeypatch.setattr(plugin_config, "pixiv_r18_text_allow_group", True)
    timeline.patch_send(text_cmd, "无正文文案")
    use_event(_group_event())

    await _deliver_full_text(_group_event(), 29277050)

    assert timeline.events == ["表态:resolving", "发送:无正文文案", "表态:done"]


# ── 表态不该扩散到纯取数函数 / handler ─────────────────────────────


async def test_pure_fetch_helper_does_not_react(timeline, use_event, monkeypatch):
    """`_fetch_detail_and_text` 是纯取数，**不**负责表态。

    表态由调用它的 `_deliver_full_text` 统一包住 —— 两边都打就会闪两个回合。
    """
    from nonebot_plugin_pixiv_novel import client, plugin_config

    async def detail(_novel_id):
        return _r18_detail()

    async def text(_novel_id):
        return "正文"

    monkeypatch.setattr(client, "novel_detail", detail)
    monkeypatch.setattr(client, "novel_text", text)
    monkeypatch.setattr(plugin_config, "pixiv_text_targets", ["group"])
    monkeypatch.setattr(plugin_config, "pixiv_r18_text_allow_group", True)
    use_event(_group_event())

    await _fetch_detail_and_text(_group_event(), 29277050)

    assert timeline.events == []


# ── 静态接线：装饰的是哪两个函数、闸门有没有排在表态前面 ───────────


def test_with_reaction_decorates_exactly_the_two_flows():
    """表态只包住「慢操作 + 发送」，不要包住整个 handler。

    包整个 handler 会把「白名单拒绝」「管理员拒绝」「用法提示」这些
    毫秒级的 finish 也裹进表态里 —— 那些路径本不该有任何表情。
    """
    tree = source_introspect.plugin_tree()
    decorated = {
        node.name
        for node in tree.body
        if isinstance(node, source_introspect.ast.AsyncFunctionDef)
        and source_introspect.decorated_with(node, "with_reaction")
    }
    assert decorated == {"_card_flow", "_deliver_full_text"}


def test_no_handler_is_decorated_with_with_reaction():
    """handler 上不该出现 @with_reaction。

    否则「闸门先于表态」的保证就没了：装饰器在 handler 入口就打「处理中」，
    而白名单检查在 handler 内部 —— 被排除的群里也会冒出表情。
    """
    tree = source_introspect.plugin_tree()
    for name, node in source_introspect.handlers(tree).items():
        assert not source_introspect.decorated_with(node, "with_reaction"), (
            f"{name} 被 @with_reaction 包住了，表领会绕过群闸门"
        )


def test_url_hook_group_gate_runs_before_any_reaction():
    """URL hook 里群闸门必须在表态之前。

    否则被排除的群里贴个链接就会冒出 🔨/🍼 表情 —— 卡片不发但表情乱跳，
    比什么都不做更像故障。
    """
    tree = source_introspect.plugin_tree()
    hook = source_introspect.handlers(tree)["url_hook"]
    gate = source_introspect.first_call_lineno(hook, "_is_group_allowed")
    react = source_introspect.first_call_lineno(hook, "_card_flow")

    assert gate is not None and react is not None
    assert gate < react, "URL hook 的表态排在了群闸门之前"


def test_fulltext_group_gate_runs_before_the_reaction():
    """「获取全文」同理：被闸门拒绝时直接回文案，不该已经打过「处理中」。"""
    tree = source_introspect.plugin_tree()
    cmd = source_introspect.handlers(tree)["text_cmd"]
    gate = source_introspect.first_call_lineno(cmd, "_is_group_allowed")
    react = source_introspect.first_call_lineno(cmd, "_deliver_full_text")

    assert gate is not None and react is not None
    assert gate < react, "「获取全文」的表态排在了群闸门之前"


def test_card_sender_does_not_finish_the_matcher():
    """`_send_card_and_remember` 里**不能**有 `finish()`。

    `finish()` 抛 FinishedException 会把控制流直接带走，于是
    ① 终态表态只能写在它之前（顺序又反了）；② 它后面的收尾逻辑全成死代码。
    收尾的 finish 归 handler 管。
    """
    tree = source_introspect.plugin_tree()
    fn = next(
        node
        for node in tree.body
        if isinstance(node, source_introspect.ast.AsyncFunctionDef)
        and node.name == "_send_card_and_remember"
    )
    called = {
        node.func.attr
        for node in source_introspect.ast.walk(fn)
        if isinstance(node, source_introspect.ast.Call)
        and isinstance(node.func, source_introspect.ast.Attribute)
    }
    assert "finish" not in called, "_send_card_and_remember 里出现了 finish()"


def test_terminal_reaction_is_wired_for_both_paths():
    """两条链路都要有终态表态的接线（而不是只有 🔨 就收工）。

    变异检验的对照：把某个 `with_reaction` 摘掉、或把发送挪出被装饰的函数，
    上面那些顺序用例会红；这条则保证「两个流程都确实被装饰」。
    """
    tree = source_introspect.plugin_tree()
    for name in ("_card_flow", "_deliver_full_text"):
        node = next(
            n
            for n in tree.body
            if isinstance(n, source_introspect.ast.AsyncFunctionDef) and n.name == name
        )
        assert source_introspect.decorated_with(node, "with_reaction"), (
            f"{name} 没被 @with_reaction 包住，终态表态不会发出"
        )
