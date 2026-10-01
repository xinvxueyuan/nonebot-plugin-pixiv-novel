"""群白名单：纯函数矩阵 + 配置归一化 + 接线（含静态断言）+ 推送过滤。

背景：用户要求「仅放行配置群，默认空为禁用白名单」。
这里最要紧的一条是**空列表的语义**：空 = 关闭白名单（全放行），
而不是「谁都不许用」。两者写反了的表现都是「群里发命令毫无反应」，
且不报任何错 —— 所以用测试把它钉死。
"""


import pytest
import source_introspect
from nonebot_plugin_pixiv_novel import policy, poller, store
from nonebot_plugin_pixiv_novel.config import Config
from test_poller import FakeClient, FakeNovel, _cfg

# ── 纯函数矩阵 ────────────────────────────────────────────────────


def test_empty_whitelist_disables_the_feature_and_allows_everyone():
    """空列表 = **关闭白名单**（默认），不是「全拒」。

    写法锚点：白名单是「限制名单」，空 = 没限制。
    （对照 `pixiv_text_targets` 是「允许名单」，空 = 都不许 —— 语义相反，别照搬。）
    """
    assert policy.is_group_allowed(123, whitelist=[]) is True
    assert policy.is_group_allowed(999999, whitelist=[]) is True


def test_whitelisted_group_passes():
    assert policy.is_group_allowed(123, whitelist=[123]) is True
    assert policy.is_group_allowed(123, whitelist=[123, 456]) is True


def test_group_outside_whitelist_is_denied():
    assert policy.is_group_allowed(456, whitelist=[123]) is False


def test_private_message_is_never_blocked_by_the_group_whitelist():
    """私聊不是「群」，白名单不该管它。

    如果这里返回 False，用户一配白名单就再也无法私聊取全文 ——
    而白名单的语义明明是「限制群」，属于把范围放大了。
    """
    assert policy.is_group_allowed(None, whitelist=[123]) is True


def test_int_and_str_group_ids_both_match():
    """NoneBot 的 `event.group_id` 是 int，而用户手改 `.env` 时可能写成字符串。

    不两边都转 str 的话，`123 in {"123"}` 是 False ——
    表现为「配了白名单但没生效」，完全静默。
    """
    assert policy.is_group_allowed(123, whitelist=["123"]) is True
    assert policy.is_group_allowed("123", whitelist=[123]) is True
    assert policy.is_group_allowed(123, whitelist=[" 123 "]) is True


# ── 群黑名单（2026-10-02 用户要求新增）──────────────────────────────


def test_empty_blacklist_is_disabled_by_default():
    """空黑名单 = 谁都不拉黑（默认），不是「全拒」。"""
    assert policy.is_group_allowed(123, whitelist=[], blacklist=[]) is True
    assert policy.is_group_allowed(999999, whitelist=[], blacklist=[]) is True


def test_blacklisted_group_is_denied_with_empty_whitelist():
    """用户的实际配置形态：**清空白名单 + 只拉黑一个群**。

    也就是「不限制别的群，单独禁掉这一个」。白名单空了，拦人的只剩黑名单。
    """
    assert policy.is_group_allowed(1094538078, whitelist=[], blacklist=[1094538078]) is False
    assert policy.is_group_allowed(868258211, whitelist=[], blacklist=[1094538078]) is True


def test_blacklist_beats_whitelist():
    """两份名单都命中同一个群时，**黑名单赢**。

    这条是刻意的优先级：用户明确说「禁掉这个群」却被一条白名单悄悄推翻，
    是最难查的一类「配置没生效」。
    """
    assert policy.is_group_allowed(123, whitelist=[123], blacklist=[123]) is False


def test_blacklist_does_not_block_other_whitelisted_groups():
    assert policy.is_group_allowed(456, whitelist=[123, 456], blacklist=[123]) is True


def test_private_message_is_never_blocked_by_the_group_blacklist():
    """私聊不是「群」，黑名单也不该管它（否则用户一拉黑就没法私聊取全文）。"""
    assert policy.is_group_allowed(None, whitelist=[], blacklist=[123]) is True


def test_blacklist_matches_int_and_str():
    assert policy.is_group_allowed(123, whitelist=[], blacklist=["123"]) is False
    assert policy.is_group_allowed("123", whitelist=[], blacklist=[123]) is False
    assert policy.is_group_allowed(" 123 ", whitelist=[], blacklist=[123]) is False


# ── 配置归一化 ────────────────────────────────────────────────────


def test_whitelist_defaults_to_empty():
    assert Config().pixiv_group_whitelist == []


def test_whitelist_normalizes_to_int_and_dedupes_keeping_order():
    cfg = Config(pixiv_group_whitelist=["123", 456, "123"])
    assert cfg.pixiv_group_whitelist == [123, 456]


def test_whitelist_drops_unparsable_items_instead_of_crashing():
    """手滑写个空串或带说明的文字，不能让整个插件起不来。

    但**丢掉的项必须留 warning**（在 config 里），否则用户会一直以为
    那个群被放行了、却收不到任何推送，还查不出原因。
    """
    cfg = Config(pixiv_group_whitelist=[123, "", "书友群456", 456])
    assert cfg.pixiv_group_whitelist == [123, 456]


def test_blacklist_defaults_to_empty():
    assert Config().pixiv_group_blacklist == []


def test_blacklist_normalizes_like_the_whitelist():
    """两份名单共用同一个校验器 —— 归一化行为必须完全一致。

    不共用的话，黑名单很容易漏掉「字符串群号转 int」这一步，
    于是 `.env` 里写 ["1094538078"] 就静默不生效（int 与 str 比不上）。
    """
    cfg = Config(pixiv_group_blacklist=["1094538078", 1094538078, " 123 "])
    assert cfg.pixiv_group_blacklist == [1094538078, 123]


def test_both_lists_are_independent():
    """两个字段互不干扰（别把黑名单误塞进白名单）。"""
    cfg = Config(pixiv_group_whitelist=[111], pixiv_group_blacklist=[222])
    assert cfg.pixiv_group_whitelist == [111]
    assert cfg.pixiv_group_blacklist == [222]


# ── 静态接线断言（走 AST，不匹配源码字符串 —— 注释里的名字会骗人）────


def test_the_handler_extractor_still_finds_all_handlers():
    """护栏：AST 提取一旦失效，下面那些断言会**恒真**（空字典 → 循环不执行）。

    所以先钉住 handler 名字集。少了/多了都会红，逼人回来修提取或补闸门。
    """
    found = set(source_introspect.handlers(source_introspect.plugin_tree()))
    assert found == {
        "subscribe_cmd",
        "unsubscribe_cmd",
        "list_cmd",
        "url_hook",
        "text_cmd",
    }


def test_every_handler_checks_the_group_whitelist():
    """四个命令 + 被动 hook 都必须过白名单。

    漏一个的症状很有欺骗性：比如只有 hook 漏了，则是「命令不能用但贴链接照回卡片」，
    用户会以为白名单没生效。
    """
    tree = source_introspect.plugin_tree()
    for name, node in source_introspect.handlers(tree).items():
        assert "_is_group_allowed" in source_introspect.called_names(node), (
            f"{name} 没有过群白名单闸门"
        )


def test_group_gate_runs_before_the_admin_gate():
    """白名单必须在管理员闸门**之前**。

    否则管理员在非白名单群里照样能用 —— 白名单就只限制了普通群友，
    等于没限制（用户的原话是「仅放行配置群」）。
    """
    tree = source_introspect.plugin_tree()
    for name, node in source_introspect.handlers(tree).items():
        group_line = source_introspect.first_call_lineno(node, "_is_group_allowed")
        admin_line = source_introspect.first_call_lineno(node, "_is_admin")
        if admin_line is None:
            continue
        assert group_line is not None
        assert group_line < admin_line, f"{name} 里管理员闸门排在群白名单前面"


def test_url_hook_returns_silently_when_group_not_allowed():
    """被动 hook 被白名单拒绝时要**静默返回**，不能回一句「不在白名单」。

    群里贴个链接就被 bot 呛一句「本插件未在此群启用」，比不回还烦人；
    而且会让别的群友以为 bot 坏了。
    """
    tree = source_introspect.plugin_tree()
    hook = source_introspect.handlers(tree)["url_hook"]
    assert source_introspect.guards_with_bare_return(hook, "_is_group_allowed"), (
        "url_hook 的白名单分支不是静默 return"
    )


# ── 推送过滤 ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _db(tmp_path):
    store.init(tmp_path / "t.sqlite3")
    yield


async def test_poller_does_not_push_to_a_group_outside_the_whitelist():
    store.subscribe(group_id=100, author_id=200, baseline=500)
    client = FakeClient(
        {200: [FakeNovel(600, title="新作600"), FakeNovel(500)]},
        {600: FakeNovel(600, title="新作600")},
    )
    sent = []

    async def send(gid, msg):
        sent.append((gid, str(msg)))

    cfg = _cfg(pixiv_group_whitelist=[999])
    n = await poller.poll_once(client, cfg, send=send, send_file=None)

    assert n == 0
    assert sent == []


async def test_poller_pushes_normally_when_whitelist_is_empty():
    """空白名单 = 关闭该功能，推送行为与加白名单之前完全一致。"""
    store.subscribe(group_id=100, author_id=200, baseline=500)
    client = FakeClient(
        {200: [FakeNovel(600, title="新作600"), FakeNovel(500)]},
        {600: FakeNovel(600, title="新作600")},
    )
    sent = []

    async def send(gid, msg):
        sent.append((gid, str(msg)))

    n = await poller.poll_once(client, _cfg(), send=send, send_file=None)
    assert n == 1
    assert [gid for gid, _ in sent] == [100]


async def test_whitelisted_group_still_gets_pushed():
    store.subscribe(group_id=100, author_id=200, baseline=500)
    client = FakeClient(
        {200: [FakeNovel(600, title="新作600"), FakeNovel(500)]},
        {600: FakeNovel(600, title="新作600")},
    )
    sent = []

    async def send(gid, msg):
        sent.append((gid, str(msg)))

    cfg = _cfg(pixiv_group_whitelist=[100])
    n = await poller.poll_once(client, cfg, send=send, send_file=None)

    assert n == 1
    assert [gid for gid, _ in sent] == [100]


async def test_excluded_group_has_its_watermark_advanced_no_backlog():
    """被白名单排除的群要高水位跑掉，不能留积压。

    若只是 `continue` 不写库，用户之后把这个群加回白名单，
    会一次性收到离线期间攒下的全部旧作（刷屏），而且与插件
    「订阅前的历史作品不推送」的既有取向相反。
    """
    store.subscribe(group_id=100, author_id=200, baseline=500)
    client = FakeClient(
        {200: [FakeNovel(600), FakeNovel(550), FakeNovel(500)]},
        {600: FakeNovel(600), 550: FakeNovel(550)},
    )

    async def send(gid, msg):
        raise AssertionError("被排除的群不该收到任何推送")

    cfg = _cfg(pixiv_group_whitelist=[999])
    await poller.poll_once(client, cfg, send=send, send_file=None)

    assert store.list_by_group(100)[0]["last_seen"] == 600


# ── 推送过滤：黑名单 ───────────────────────────────────────────────


async def test_poller_does_not_push_to_a_blacklisted_group():
    """用户的实际配置：空白名单 + 拉黑一个群 → 该群不推送，别处照常。"""
    store.subscribe(group_id=100, author_id=200, baseline=500)
    client = FakeClient(
        {200: [FakeNovel(600, title="新作600"), FakeNovel(500)]},
        {600: FakeNovel(600, title="新作600")},
    )
    sent = []

    async def send(gid, msg):
        sent.append((gid, str(msg)))

    cfg = _cfg(pixiv_group_blacklist=[100])
    n = await poller.poll_once(client, cfg, send=send, send_file=None)

    assert n == 0
    assert sent == []


async def test_poller_still_pushes_to_groups_not_in_the_blacklist():
    """黑名单只拦名单内的群 —— 其余群「清空白名单」后照常推送。"""
    store.subscribe(group_id=100, author_id=200, baseline=500)
    client = FakeClient(
        {200: [FakeNovel(600, title="新作600"), FakeNovel(500)]},
        {600: FakeNovel(600, title="新作600")},
    )
    sent = []

    async def send(gid, msg):
        sent.append((gid, str(msg)))

    cfg = _cfg(pixiv_group_blacklist=[999])
    n = await poller.poll_once(client, cfg, send=send, send_file=None)

    assert n == 1
    assert [gid for gid, _ in sent] == [100]


async def test_blacklisted_group_also_has_its_watermark_advanced():
    """和排除群同理：高水位要跑掉，放行回来时不能一次性吐积压。"""
    store.subscribe(group_id=100, author_id=200, baseline=500)
    client = FakeClient(
        {200: [FakeNovel(600), FakeNovel(550), FakeNovel(500)]},
        {600: FakeNovel(600), 550: FakeNovel(550)},
    )

    async def send(gid, msg):
        raise AssertionError("被拉黑的群不该收到任何推送")

    cfg = _cfg(pixiv_group_blacklist=[100])
    await poller.poll_once(client, cfg, send=send, send_file=None)

    assert store.list_by_group(100)[0]["last_seen"] == 600


# ── 黑名单也要真的被接线（不只是 policy 有参数）────────────────────


def test_gate_passes_the_blacklist_to_the_policy():
    """`_is_group_allowed` 必须把**两个**字段都传下去。

    漏传黑名单的表现是「配了黑名单但完全不生效」—— 而 policy 那层的单测
    全绿，因为它测的是 policy 自己。这类「参数没接上」只有静态接线断言能抓。
    """
    tree = source_introspect.plugin_tree()
    fn = next(
        node
        for node in tree.body
        # ⚠️ 这里用「函数定义」基类判定：`_is_group_allowed` 是**同步** def，
        # 只匹配 AsyncFunctionDef 会 StopIteration（我第一版就踩了）。
        if isinstance(node, source_introspect.FUNCTION_DEFS)
        and node.name == "_is_group_allowed"
    )
    passed = {
        kw.arg
        for node in source_introspect.ast.walk(fn)
        if isinstance(node, source_introspect.ast.Call)
        for kw in node.keywords
    }
    assert "blacklist" in passed, "_is_group_allowed 没有把黑名单传给 policy"
    assert "whitelist" in passed, "_is_group_allowed 没有把白名单传给 policy"


async def test_gate_actually_denies_a_blacklisted_group(monkeypatch):
    """端到端：改配置后闸门真的拒（而不是只有 policy 单测通过）。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent
    from nonebot_plugin_pixiv_novel import _is_group_allowed, plugin_config

    event = GroupMessageEvent.model_construct(group_id=1094538078, user_id=10001)

    monkeypatch.setattr(plugin_config, "pixiv_group_whitelist", [])
    monkeypatch.setattr(plugin_config, "pixiv_group_blacklist", [1094538078])
    assert _is_group_allowed(event) is False

    monkeypatch.setattr(plugin_config, "pixiv_group_blacklist", [])
    assert _is_group_allowed(event) is True

    # 生产形态：白名单清空 + 黑名单单个群 —— 别的群要照常可用
    monkeypatch.setattr(plugin_config, "pixiv_group_blacklist", [999])
    assert _is_group_allowed(event) is True
