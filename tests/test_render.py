import re

import pytest
from nonebot_plugin_pixiv_novel import render, store

AVATAR_A = "https://i.pximg.net/a.jpg"
AVATAR_B = "https://i.pximg.net/b.jpg"
DATA_URI_A = "data:image/jpeg;base64,AAAA"

GROUP = 1094538078


@pytest.fixture
def _db(tmp_path):
    """用**真 store** 产出行。

    ⚠️ 这个 fixture 是必须的。初稿的 `_rows()` 手写的是 **dict 列表**，
    而生产环境 store 设了 `conn.row_factory = sqlite3.Row`，行**全是 Row**。
    Row 没有 `.get()` —— 于是 `render.py` 里 `r.get("author_name")` 在生产必抛
    AttributeError，被「回退纯文本」吞掉，**订阅列表的图片渲染整个是死的**，
    而单测因为用的是 dict 全绿。

    现在行直接从真 store 取，形状与生产完全一致，这类 bug 跑不掉。
    """
    store.init(tmp_path / "r.sqlite3")
    # 注：store.subscribe 的 created_at 由 store 自己填 time.time()，
    # 所以这里不传（订阅时间只断言格式，不断言具体值）。
    store.subscribe(GROUP, 111, baseline=100, author_name="作者甲",
                    author_avatar_url=AVATAR_A)
    store.subscribe(GROUP, 222, baseline=50, author_name="", author_avatar_url="")
    store.set_last_seen(GROUP, 111, 900)
    store.set_last_seen(GROUP, 222, 800)
    yield


def _rows():
    """真 store 产出的行（`sqlite3.Row`），不是手写 dict。"""
    return store.list_by_group(GROUP)


def test_rows_are_really_sqlite3_rows(_db):
    """前提断言：真实行是 `sqlite3.Row` 且**没有** `.get()`。

    这行要是挂了，说明 store 的实现变了（比如换成 dict），
    下面那些「兼容 Row」的测试就失去意义了。
    """
    import sqlite3

    rows = _rows()
    assert rows, "fixture 没产出数据"
    assert isinstance(rows[0], sqlite3.Row)
    assert not hasattr(rows[0], "get")
    assert hasattr(rows[0], "keys")
    assert rows[0]["author_name"] == "作者甲"          # 但下标可以


def test_build_context_maps_rows_to_items(_db):
    ctx = render.build_context(_rows(), group_id=1094538078)
    assert ctx["group_id"] == 1094538078
    assert ctx["count"] == 2
    assert ctx["items"][0]["index"] == 1
    assert ctx["items"][0]["author_id"] == 111
    assert ctx["items"][0]["author_name"] == "作者甲"
    assert ctx["items"][0]["author_url"] == "https://www.pixiv.net/users/111"
    assert ctx["items"][0]["last_seen"] == 900
    assert ctx["truncated"] == 0


def test_build_context_includes_all_four_required_fields(_db):
    """需求：订阅列表要显示 **作者名 / 作者头像 / 订阅时间 / 作者id**，四个都不能少。"""
    item = render.build_context(
        _rows(), group_id=1, avatars={AVATAR_A: DATA_URI_A}
    )["items"][0]
    assert item["author_name"] == "作者甲"
    assert item["avatar_uri"] == DATA_URI_A
    assert item["subscribed_at"]                       # 订阅时间（已格式化）
    assert item["author_id"] == 111


def test_build_context_formats_subscribed_at(_db):
    ctx = render.build_context(_rows(), group_id=1)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", ctx["items"][0]["subscribed_at"])


def test_build_context_avatar_uri_none_when_missing(_db):
    """没头像 → avatar_uri 为 None，模板走首字占位块。"""
    assert render.build_context(_rows(), group_id=1)["items"][1]["avatar_uri"] is None


def test_build_context_avatar_uri_none_when_fetch_failed(_db):
    """URL 有但下载失败（不在 avatars 映射里）→ 也是 None，不能 KeyError。"""
    ctx = render.build_context(_rows(), group_id=1, avatars={})     # 空映射 = 全失败
    assert ctx["items"][0]["avatar_uri"] is None
    assert ctx["items"][0]["author_name"] == "作者甲"                # 其他字段不受影响


def test_build_context_falls_back_to_id_when_no_name(_db):
    item = render.build_context(_rows(), group_id=1)["items"][1]
    assert item["author_name"] == "222"        # 没名字就显示 ID，不显示空白


def test_build_context_caps_items_but_keeps_total(_db):
    # 造 200 条：直接用真 store（保证行的类型与生产一致）
    # ⚠️ 区间要避开 fixture 已用的 111/222，否则 INSERT OR IGNORE 会去重、数量对不上
    for i in range(1000, 1200):
        store.subscribe(GROUP, i, baseline=0, author_name="", author_avatar_url="")
    rows = _rows()
    assert len(rows) == 202          # 2（fixture） + 200
    ctx = render.build_context(rows, group_id=1, max_items=50)
    assert ctx["count"] == 202            # 总数照实报（2 + 200）
    assert len(ctx["items"]) == 50        # 只渲染前 50
    assert ctx["truncated"] == 152


def test_build_context_handles_empty():
    ctx = render.build_context([], group_id=1)
    assert ctx["count"] == 0
    assert ctx["items"] == []


@pytest.mark.asyncio
async def test_render_calls_template_to_pic_with_three_positional_args(monkeypatch, _db):
    """回归：template_to_pic 的位置参数是 (template_path, template_name, templates)。"""
    calls = {}

    async def fake_template_to_pic(template_path, template_name, templates, **kwargs):
        calls["template_path"] = str(template_path)
        calls["template_name"] = template_name
        calls["templates"] = templates
        calls["kwargs"] = kwargs
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr(render, "_load_htmlkit", lambda: fake_template_to_pic)
    out = await render.render_subscription_list(_rows(), group_id=1)

    assert out.startswith(b"\x89PNG")
    assert calls["template_name"] == "subscription_list.html"
    assert calls["template_path"].endswith("templates")
    assert calls["templates"]["count"] == 2
    assert calls["kwargs"]["image_format"] == "png"


@pytest.mark.asyncio
async def test_render_passes_avatars_into_template_context(monkeypatch, _db):
    """头像必须进到模板上下文里，否则列表上不显示头像。"""
    seen = {}

    async def fake_template_to_pic(template_path, template_name, templates, **kwargs):
        seen.update(templates)
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr(render, "_load_htmlkit", lambda: fake_template_to_pic)
    out = await render.render_subscription_list(
        _rows(), group_id=1, avatars={AVATAR_A: DATA_URI_A}
    )
    assert out is not None
    assert seen["items"][0]["avatar_uri"] == DATA_URI_A
    assert seen["items"][0]["author_name"] == "作者甲"


@pytest.mark.asyncio
async def test_render_works_without_avatars_argument(monkeypatch, _db):
    """不传 avatars（= 全部下载失败）也要能渲染，不能抛。"""
    async def fake_template_to_pic(*a, **kw):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr(render, "_load_htmlkit", lambda: fake_template_to_pic)
    assert await render.render_subscription_list(_rows(), group_id=1) is not None


@pytest.mark.asyncio
async def test_render_returns_none_when_htmlkit_import_fails(monkeypatch, _db):
    def boom():
        raise ImportError("no htmlkit here")

    monkeypatch.setattr(render, "_load_htmlkit", boom)
    assert await render.render_subscription_list(_rows(), group_id=1) is None


@pytest.mark.asyncio
async def test_render_returns_none_when_render_raises(monkeypatch, _db):
    async def boom(*a, **kw):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(render, "_load_htmlkit", lambda: boom)
    assert await render.render_subscription_list(_rows(), group_id=1) is None


@pytest.mark.asyncio
async def test_render_skips_empty_rows_without_touching_htmlkit(monkeypatch, _db):
    called = []

    async def spy(*a, **kw):
        called.append(1)
        return b"x"

    monkeypatch.setattr(render, "_load_htmlkit", lambda: spy)
    assert await render.render_subscription_list([], group_id=1) is None
    assert called == []          # 空列表直接返回，交给调用方回退文本


def test_build_context_works_on_sqlite3_row_without_get(_db):
    """**核心回归**：`sqlite3.Row` 上没有 `.get()`，取字段必须照样成功。

    修复前 `r.get("author_name")` → AttributeError → 被 render 吞成
    「回退纯文本」→ 订阅列表永远出不了图，且**完全不报错**。
    """
    rows = _rows()
    assert not hasattr(rows[0], "get")           # 确认前提：真的没有 .get

    ctx = render.build_context(rows, group_id=GROUP, avatars={AVATAR_A: DATA_URI_A})
    item = ctx["items"][0]
    assert item["author_name"] == "作者甲"        # 修复前这里就炸了
    assert item["author_id"] == 111
    assert item["avatar_uri"] == DATA_URI_A
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", item["subscribed_at"])
    assert item["last_seen"] == 900


def test_row_get_handles_both_row_and_dict():
    """`_row_get` 要同时吃 Row 和 dict（别的调用点可能传 dict）。"""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute("select 1 as a, 'x' as b").fetchone()
    conn.close()

    assert render._row_get(row, "a") == 1
    assert render._row_get(row, "b") == "x"
    assert render._row_get(row, "missing") is None            # 缺键不抛
    assert render._row_get(row, "missing", "d") == "d"

    assert render._row_get({"a": 1}, "a") == 1
    assert render._row_get({"a": 1}, "missing", "d") == "d"
