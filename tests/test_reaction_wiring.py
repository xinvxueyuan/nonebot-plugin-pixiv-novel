"""表态接线：三态（处理中 / 完成 / 失败）包住慢操作，且只在白名单闸门**之内**。

这组测试的边界：**库内部正确性由库自己的测试负责**（EMOJI_MAP 取值、私聊跳过、
MatcherException 判成功等）。这里只验证**插件的接线**：
 ① 被装饰的是哪两个函数（而不是整个 handler）；
 ② 模块加载期真的拿到了库的实现，而不是静默退到降级替身（这是「假绿」的根源）；
 ③ 白名单闸门跑在表态之前 —— 不在白名单的群不该冒出 🔨/✅ 表情。
"""


import pytest
import source_introspect
from conftest import load_fixture
from nonebot.adapters.onebot.v11 import GroupMessageEvent

reactlib = pytest.importorskip(
    "nonebot_plugin_message_reaction",
    reason="表态库是可选依赖，拿不到它的源码时跳过（插件降级路径另有测试）",
)

from nonebot_plugin_pixiv_novel import (  # noqa: E402
    _build_card_with_reaction,
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


@pytest.fixture
def react_calls(monkeypatch):
    """拦住库的 message_reaction，记录收到的 status 序列。

    打的是**库模块**的属性（而不是插件的导入名）—— `with_reaction` 在调用时才
    从模块全局取 `message_reaction`，所以这样能拦住所有被装饰的函数。
    """
    recorded: list[str] = []

    async def fake(event, status):
        recorded.append(status)
        return True

    monkeypatch.setattr(reactlib.reaction, "message_reaction", fake)
    return recorded


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


# ── 三态接线 ──────────────────────────────────────────────────────


async def test_card_build_reacts_resolving_then_done(react_calls, use_event, monkeypatch):
    from nonebot_plugin_pixiv_novel import client

    async def detail(_novel_id):
        return _r18_detail()

    async def cover(*args, **kwargs):
        return b""

    monkeypatch.setattr(client, "novel_detail", detail)
    monkeypatch.setattr(client, "download_cover", cover)
    use_event(_group_event())

    msg = await _build_card_with_reaction("novel", 29277050)

    assert str(msg)                      # 卡片真的拼出来了
    assert react_calls == ["resolving", "done"]


async def test_card_build_reacts_fail_and_reraises(react_calls, use_event, monkeypatch):
    """拉卡片失败：打「失败」**并把异常继续上抛**。

    必须上抛 —— 外层要据此回「取作品失败」的文案；如果这里吞掉，
    用户看到的就是一个 ❌ 加上一片沉默。
    """
    from nonebot_plugin_pixiv_novel import client

    async def boom(_novel_id):
        raise RuntimeError("pixiv 挂了")

    monkeypatch.setattr(client, "novel_detail", boom)
    use_event(_group_event())

    with pytest.raises(RuntimeError, match="pixiv 挂了"):
        await _build_card_with_reaction("novel", 29277050)

    assert react_calls == ["resolving", "fail"]


async def test_fulltext_reacts_done(react_calls, use_event, monkeypatch):
    """取全文：处理中 → 完成（整段两次网络往返共用一对表态）。

    若拆成两步各表一次态，群里会闪「🔨✅🔨✅」两个回合，看起来像出了两次问题。
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

    detail_obj, body, denied = await _fetch_detail_and_text(_group_event(), 29277050)

    assert (body, denied) == ("正文", "")
    assert react_calls == ["resolving", "done"]


async def test_fulltext_reacts_fail_on_network_error(react_calls, use_event, monkeypatch):
    from nonebot_plugin_pixiv_novel import client

    async def boom(_novel_id):
        raise RuntimeError("超时")

    monkeypatch.setattr(client, "novel_detail", boom)
    use_event(_group_event())

    with pytest.raises(RuntimeError):
        await _fetch_detail_and_text(_group_event(), 29277050)

    assert react_calls == ["resolving", "fail"]


async def test_policy_denial_still_counts_as_done(react_calls, use_event, monkeypatch):
    """策略拒绝（R18 不发群等）**不是失败**，不该打 ❌。

    它是「正常处理并给出了答复」，打 ❌ 会让人以为 bot 崩了；
    真正的失败（网络/接口异常）才打 ❌。
    """
    from nonebot_plugin_pixiv_novel import client, plugin_config

    async def detail(_novel_id):
        return _r18_detail()

    monkeypatch.setattr(client, "novel_detail", detail)
    monkeypatch.setattr(plugin_config, "pixiv_text_targets", ["group"])
    monkeypatch.setattr(plugin_config, "pixiv_r18_text_allow_group", False)
    monkeypatch.setattr(plugin_config, "pixiv_admin_ids", [])
    use_event(_group_event())

    _detail, body, denied = await _fetch_detail_and_text(_group_event(), 29277050)

    assert body == "" and denied        # 确实被拒了
    assert react_calls == ["resolving", "done"]


# ── 静态接线：装饰的是哪两个函数、闸门有没有排在表态前面 ───────────


def test_with_reaction_decorates_exactly_the_two_slow_helpers():
    """表态只包住慢操作（拉卡片 / 取全文），不要包住整个 handler。

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
    assert decorated == {"_build_card_with_reaction", "_fetch_detail_and_text"}


def test_no_handler_is_decorated_with_with_reaction():
    """handler 上不该出现 @with_reaction。

    否则「闸门先于表态」的保证就没了：装饰器在 handler 入口就打「处理中」，
    而白名单检查在 handler 内部 —— 非白名单群里也会冒出表情。
    """
    tree = source_introspect.plugin_tree()
    for name, node in source_introspect.handlers(tree).items():
        assert not source_introspect.decorated_with(node, "with_reaction"), (
            f"{name} 被 @with_reaction 包住了，表领会绕过白名单闸门"
        )


def test_url_hook_group_gate_runs_before_any_reaction():
    """URL hook 里白名单闸门必须在表态之前。

    否则非白名单群里贴个链接就会冒出 🔨/✅ 表情 —— 卡片不发但表情乱跳，
    比什么都不做更像故障。
    """
    tree = source_introspect.plugin_tree()
    hook = source_introspect.handlers(tree)["url_hook"]
    gate = source_introspect.first_call_lineno(hook, "_is_group_allowed")
    react = source_introspect.first_call_lineno(hook, "_build_card_with_reaction")

    assert gate is not None and react is not None
    assert gate < react, "URL hook 的表态排在了白名单闸门之前"


def test_fulltext_group_gate_runs_before_the_reaction():
    """「获取全文」同理：白名单被拒时直接 finish，不该已经打过「处理中」。"""
    tree = source_introspect.plugin_tree()
    cmd = source_introspect.handlers(tree)["text_cmd"]
    gate = source_introspect.first_call_lineno(cmd, "_is_group_allowed")
    react = source_introspect.first_call_lineno(cmd, "_deliver_full_text")

    assert gate is not None and react is not None
    assert gate < react, "「获取全文」的表态排在了白名单闸门之前"
