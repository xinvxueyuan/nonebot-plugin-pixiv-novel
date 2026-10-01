"""系列卡片与「系列 R18 到底在哪个字段」的测试。

**为什么必须有这个文件**：这里钉住的是一条实测结论 —— 系列级 R18 只在
**网页接口** `/ajax/novel/series/N` 的 `body.xRestrict` 里。App 接口
`/v2/novel/series` 的 `novel_series_detail` **没有这个字段**（只有 11 个键）。

如果哪天有人「顺手统一成 App 接口」或「改成查首篇的 x_restrict」，
R18 系列的封面就会**不被模糊**，而且**不报错** —— 这两条测试就是拦这个的。
"""

import json

import pytest
from conftest import load_fixture
from nonebot_plugin_pixiv_novel import message, pixiv_client
from nonebot_plugin_pixiv_novel.pixiv_client import PixivClient, series_cover_url

R18_SERIES = "novel_series_r18.json"
SAFE_SERIES = "novel_series_safe.json"


# ══════════════════════════════════════════════════════════════════
# fixture 本身：录对了吗？
# ══════════════════════════════════════════════════════════════════


def test_r18_fixture_has_series_level_x_restrict():
    """R18 系列的 fixture 里 `xRestrict` 必须是 1（这是整个功能的判据）。"""
    body = load_fixture(R18_SERIES)
    assert body.xRestrict == 1
    assert body.title
    assert body.displaySeriesContentCount == 4
    assert body.userName == "阿百川大鬼"
    assert body.cover.urls["480mw"].startswith("https://i.pximg.net/")


def test_safe_fixture_has_explicit_zero_not_missing_key():
    """非 R18 系列返回的是**显式的 0**，不是缺键。

    这个区别很重要：若 pixiv 改成「非 R18 就省略该字段」，那
    `body.get("xRestrict")` 会变 None。当前实现用 `or 0` 兜住 → 仍是 0 → 行为不变，
    这条测试同时钉住了「兜底后语义仍正确」。
    """
    body = load_fixture(SAFE_SERIES)
    assert "xRestrict" in body                   # 键存在
    assert body.xRestrict == 0                   # 且是 0，不是 None
    assert int(body.get("xRestrict") or 0) == 0


def test_series_fixture_has_no_nested_novel_series_detail():
    """钉住「我们用的是网页接口形状」。

    网页接口是**扁平**的 `body`；App 接口才是
    `{novel_series_detail:…, novel_series_first_novel:…}`。
    如果 fixture 里出现了 App 那套嵌套键，说明有人把接口换回去了。
    """
    body = load_fixture(R18_SERIES)
    assert "novel_series_detail" not in body
    assert "novel_series_first_novel" not in body
    assert body.id == "15744810"


# ══════════════════════════════════════════════════════════════════
# series_cover_url()
# ══════════════════════════════════════════════════════════════════


def test_series_cover_url_prefers_480mw():
    body = load_fixture(R18_SERIES)
    url = series_cover_url(body)
    assert "/c/480x960/" in url                  # 480mw 是首选（群里够看又不慢）


def test_series_cover_url_falls_back_through_candidates():
    """480mw 缺失时要退到下一个候选，而不是直接空手而归。

    断言的是「**选中了哪个候选**」（返回的正是那个键的 URL），
    不是 URL 里含什么字串 —— 后者测的是 pixiv 的 CDN 路径格式，不是本函数的逻辑。
    """
    assert series_cover_url({"cover": {"urls": {"1200x1200": "u1200"}}}) == "u1200"
    assert series_cover_url({"cover": {"urls": {"240mw": "u240"}}}) == "u240"
    assert series_cover_url({"cover": {"urls": {"original": "uorig"}}}) == "uorig"
    assert series_cover_url({"cover": {"urls": {"128x128": "u128"}}}) == "u128"
    # 有多个时按优先级取前面的
    both = {"cover": {"urls": {"original": "uorig", "480mw": "u480", "240mw": "u240"}}}
    assert series_cover_url(both) == "u480"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"cover": None},
        {"cover": {}},
        {"cover": {"urls": None}},
        {"cover": {"urls": {}}},
        {"cover": {"urls": "不是字典"}},
        "不是字典",
    ],
)
def test_series_cover_url_never_raises(body):
    """封面字段怎么缺都不能抛 —— 卡片没图也要能发出去。"""
    assert series_cover_url(body) == ""


# ══════════════════════════════════════════════════════════════════
# build_series_push()
# ══════════════════════════════════════════════════════════════════


def test_series_card_contains_all_parts():
    body = load_fixture(R18_SERIES)
    text = str(message.build_series_push(body, cover=b"", blurred=True))
    assert "爆炒木柜子" in text                              # 系列名
    assert "共 4 话" in text                                 # 话数
    assert "约 11.6 万字" in text                             # 总字数（116293）
    assert "阿百川大鬼" in text                               # 作者名
    assert "https://www.pixiv.net/users/88871942" in text     # 作者链接
    assert "https://www.pixiv.net/novel/series/15744810" in text   # 系列链接


def test_series_card_marks_r18_and_blur_state():
    body = load_fixture(R18_SERIES)
    blurred = str(message.build_series_push(body, cover=b"", blurred=True))
    assert "R-18" in blurred
    assert "已模糊" in blurred

    unblurred = str(message.build_series_push(body, cover=b"", blurred=False))
    assert "R-18" in unblurred
    assert "已模糊" not in unblurred


def test_series_card_has_no_r18_marker_for_safe_series():
    text = str(message.build_series_push(load_fixture(SAFE_SERIES), cover=b"", blurred=False))
    assert "R-18" not in text
    assert "ふたりぼっちの終末" in text
    assert "共 15 话" in text
    assert "约 4.2 万字" in text                              # 41506


def test_series_card_handles_missing_optional_fields():
    """字段缺失时也要出一张**能看**的卡片，不是抛异常 / 满屏 None。"""
    text = str(message.build_series_push({"id": 999, "title": "只有标题"}, cover=b"", blurred=False))
    assert "只有标题" in text
    assert "连载中" in text                   # isConcluded 缺失 → 按连载中
    assert "None" not in text                # 绝不能把 None 印进卡片


def test_series_card_omits_meta_when_counts_absent():
    text = str(message.build_series_push(
        {"id": 1, "title": "T", "publishedTotalCharacterCount": 0}, cover=b"", blurred=False))
    assert "共 0 话" not in text              # 不写「共 0 话」这种噪音
    assert "约 0 字" not in text


def test_series_card_cover_only_when_bytes_present():
    body = load_fixture(R18_SERIES)
    with_cover = message.build_series_push(body, cover=b"\x89PNG", blurred=False)
    without = message.build_series_push(body, cover=b"", blurred=False)
    assert len(with_cover) == len(without) + 1


def test_series_card_accepts_string_tag_list():
    """网页接口的 tags 是**纯字符串列表**（App 那边是对象）—— 两种都要认。"""
    body = {"id": 1, "title": "T", "tags": ["R-18", "オリジナル"]}
    text = str(message.build_series_push(body, cover=b"", blurred=False))
    assert "R-18" in text and "オリジナル" in text


# ══════════════════════════════════════════════════════════════════
# PixivClient.novel_series()：真的在打网页接口
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_novel_series_hits_the_web_endpoint_and_unwraps_body(monkeypatch):
    """必须请求 `/ajax/novel/series/<id>` 并返回**解包后的 body**。"""
    captured = {}
    payload = {"error": False, "message": "", "body": {"id": "15744810", "title": "T", "xRestrict": 1}}

    class FakeResp:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            captured["url"] = url
            return FakeResp()

    monkeypatch.setattr(pixiv_client.httpx, "AsyncClient", FakeClient)
    body = await PixivClient("tok", "http://p:1").novel_series(15744810)

    assert captured["url"] == "https://www.pixiv.net/ajax/novel/series/15744810"
    assert captured["headers"]["Referer"] == "https://www.pixiv.net/"   # 没有会被拒
    assert captured["proxy"] == "http://p:1"
    assert body["xRestrict"] == 1                                       # 解包到 body 层


@pytest.mark.asyncio
async def test_novel_series_raises_on_error_payload(monkeypatch):
    """网页接口报错时是 `{"error": true}` + HTTP 200 —— 必须显式转成异常。

    不转的话 `body` 是 None，卡片会变成「📚 系列 None」而且没人知道为什么。
    """
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"error": True, "message": "not found", "body": None}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(pixiv_client.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ValueError, match="not found"):
        await PixivClient("tok", "").novel_series(1)


@pytest.mark.asyncio
async def test_novel_series_real_fixture_end_to_end(monkeypatch):
    """把真实 fixture 当响应喂进去，端到端拿到系列级 R18 + 封面 URL。"""
    raw = json.loads(
        (pytest.importorskip("conftest").FIXTURES / R18_SERIES).read_text(encoding="utf-8"))

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"error": False, "message": "", "body": raw}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(pixiv_client.httpx, "AsyncClient", FakeClient)
    body = await PixivClient("tok", "").novel_series(15744810)

    assert int(body.get("xRestrict") or 0) == 1               # R18 判定源
    assert series_cover_url(body).startswith("https://i.pximg.net/")
    assert str(body.get("id")) == "15744810"


def test_check_series_shape_warns_when_xrestrict_missing(caplog):
    """缺 `xRestrict` 要**大声告警** —— 否则 R18 系列封面悄悄不模糊。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="nonebot_plugin_pixiv_novel"):
        pixiv_client._check_series_shape({"id": "1", "title": "T"})
    assert "xRestrict" in caplog.text
    assert "不被模糊" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nonebot_plugin_pixiv_novel"):
        pixiv_client._check_series_shape({"id": "1", "title": "T", "xRestrict": 0})
    assert caplog.text == ""                  # xRestrict=0 是合法值，不该告警


def test_plain_tags_helper_handles_objects_and_strings():
    from types import SimpleNamespace

    assert message._plain_tags(["a", "b"]) == ["a", "b"]
    assert message._plain_tags([SimpleNamespace(name="x"), "y"]) == ["x", "y"]
    assert message._plain_tags(None) == []
    assert message._plain_tags([SimpleNamespace(name=None), ""]) == []
