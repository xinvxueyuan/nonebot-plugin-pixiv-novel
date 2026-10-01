import re

import pytest
from nonebot_plugin_pixiv_novel import render

AVATAR_A = "https://i.pximg.net/a.jpg"
AVATAR_B = "https://i.pximg.net/b.jpg"
DATA_URI_A = "data:image/jpeg;base64,AAAA"


def _rows():
    """订阅行的形状 = store.list_by_group() 的返回（含作者名/头像/订阅时间）。"""
    return [
        {
            "author_id": 111,
            "author_name": "作者甲",
            "author_avatar_url": AVATAR_A,
            "created_at": 1759286400,          # 2025-10-01 08:00 UTC+8 附近，只要稳定就行
            "last_seen": 900,
        },
        {
            "author_id": 222,
            "author_name": "",
            "author_avatar_url": "",
            "created_at": 1759290000,
            "last_seen": 800,
        },
    ]


def test_build_context_maps_rows_to_items():
    ctx = render.build_context(_rows(), group_id=1094538078)
    assert ctx["group_id"] == 1094538078
    assert ctx["count"] == 2
    assert ctx["items"][0]["index"] == 1
    assert ctx["items"][0]["author_id"] == 111
    assert ctx["items"][0]["author_name"] == "作者甲"
    assert ctx["items"][0]["author_url"] == "https://www.pixiv.net/users/111"
    assert ctx["items"][0]["last_seen"] == 900
    assert ctx["truncated"] == 0


def test_build_context_includes_all_four_required_fields():
    """需求：订阅列表要显示 **作者名 / 作者头像 / 订阅时间 / 作者id**，四个都不能少。"""
    item = render.build_context(
        _rows(), group_id=1, avatars={AVATAR_A: DATA_URI_A}
    )["items"][0]
    assert item["author_name"] == "作者甲"
    assert item["avatar_uri"] == DATA_URI_A
    assert item["subscribed_at"]                       # 订阅时间（已格式化）
    assert item["author_id"] == 111


def test_build_context_formats_subscribed_at():
    ctx = render.build_context(_rows(), group_id=1)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", ctx["items"][0]["subscribed_at"])


def test_build_context_avatar_uri_none_when_missing():
    """没头像 → avatar_uri 为 None，模板走首字占位块。"""
    assert render.build_context(_rows(), group_id=1)["items"][1]["avatar_uri"] is None


def test_build_context_avatar_uri_none_when_fetch_failed():
    """URL 有但下载失败（不在 avatars 映射里）→ 也是 None，不能 KeyError。"""
    ctx = render.build_context(_rows(), group_id=1, avatars={})     # 空映射 = 全失败
    assert ctx["items"][0]["avatar_uri"] is None
    assert ctx["items"][0]["author_name"] == "作者甲"                # 其他字段不受影响


def test_build_context_falls_back_to_id_when_no_name():
    item = render.build_context(_rows(), group_id=1)["items"][1]
    assert item["author_name"] == "222"        # 没名字就显示 ID，不显示空白


def test_build_context_caps_items_but_keeps_total():
    rows = [{"author_id": i, "author_name": "", "author_avatar_url": "",
             "created_at": 0, "last_seen": 0} for i in range(200)]
    ctx = render.build_context(rows, group_id=1, max_items=50)
    assert ctx["count"] == 200            # 总数照实报
    assert len(ctx["items"]) == 50        # 只渲染前 50
    assert ctx["truncated"] == 150


def test_build_context_handles_empty():
    ctx = render.build_context([], group_id=1)
    assert ctx["count"] == 0
    assert ctx["items"] == []


@pytest.mark.asyncio
async def test_render_calls_template_to_pic_with_three_positional_args(monkeypatch):
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
async def test_render_passes_avatars_into_template_context(monkeypatch):
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
async def test_render_works_without_avatars_argument(monkeypatch):
    """不传 avatars（= 全部下载失败）也要能渲染，不能抛。"""
    async def fake_template_to_pic(*a, **kw):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr(render, "_load_htmlkit", lambda: fake_template_to_pic)
    assert await render.render_subscription_list(_rows(), group_id=1) is not None


@pytest.mark.asyncio
async def test_render_returns_none_when_htmlkit_import_fails(monkeypatch):
    def boom():
        raise ImportError("no htmlkit here")

    monkeypatch.setattr(render, "_load_htmlkit", boom)
    assert await render.render_subscription_list(_rows(), group_id=1) is None


@pytest.mark.asyncio
async def test_render_returns_none_when_render_raises(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(render, "_load_htmlkit", lambda: boom)
    assert await render.render_subscription_list(_rows(), group_id=1) is None


@pytest.mark.asyncio
async def test_render_skips_empty_rows_without_touching_htmlkit(monkeypatch):
    called = []

    async def spy(*a, **kw):
        called.append(1)
        return b"x"

    monkeypatch.setattr(render, "_load_htmlkit", lambda: spy)
    assert await render.render_subscription_list([], group_id=1) is None
    assert called == []          # 空列表直接返回，交给调用方回退文本
