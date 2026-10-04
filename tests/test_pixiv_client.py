import io

import pytest
from conftest import load_fixture, wrap_json
from nonebot_plugin_pixiv_novel import pixiv_client
from nonebot_plugin_pixiv_novel.pixiv_client import (
    PixivClient,
    blur_image,
    build_api,
    unwrap_novel_shape,
)
from PIL import Image
from pixivpy3 import PixivError


def _png_bytes(size=(40, 40), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_build_api_uses_proxies_kwarg(monkeypatch):
    """回归：requests/pixivpy3 的参数名是 `proxies`（dict），不是 `proxy`。

    写成 `proxy=` **不报错但静默失效** —— 那样所有 pixiv 请求都会走直连，
    而 pixiv 在国内直连不通，表现为「插件完全没反应」。
    """
    captured = {}

    class FakeAPI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def auth(self, refresh_token):
            captured["_token"] = refresh_token

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    build_api("tok", "http://127.0.0.1:1081")

    assert captured["proxies"] == {
        "http": "http://127.0.0.1:1081",
        "https": "http://127.0.0.1:1081",
    }
    assert "proxy" not in captured          # 不能是单数写法
    assert captured["_token"] == "tok"


def test_build_api_omits_proxies_when_empty(monkeypatch):
    """proxy 为空 = 直连，这时**不能**传空的 proxies dict。"""
    captured = {}

    class FakeAPI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def auth(self, refresh_token):
            pass

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    build_api("tok", "")
    assert "proxies" not in captured


@pytest.mark.asyncio
async def test_api_is_built_lazily_and_reused(monkeypatch):
    """构造 PixivClient 时不该建 API（那时可能还没配好代理），用时才建、且只建一次。"""
    from types import SimpleNamespace

    built = []

    class FakeAPI:
        def __init__(self, **kwargs):
            built.append(kwargs)

        def auth(self, refresh_token):
            pass

        def user_novels(self, author_id):
            return SimpleNamespace(novels=["n1"])

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    c = PixivClient("tok", "")
    assert c._api is None                     # 构造时还没建
    assert await c.user_novels(1) == ["n1"]
    assert await c.user_novels(2) == ["n1"]
    assert len(built) == 1                    # 只构造一次


@pytest.mark.asyncio
async def test_novel_detail_unwraps_and_novel_text_delegates(monkeypatch):
    """detail **必须解包** `{"novel": {...}}`；text 抽 `.text` 并把 None 归一成空串。

    ⚠️ 这个测试原来用 `SimpleNamespace(id=..., x_restrict=1)` —— 字段摆在**顶层**，
    是凭空编的假数据。真实的 pixiv 响应字段在 `.novel` 里，所以旧写法
    让 102 个测试全绿却漏掉了真 bug。现在改成用录下来的真实形状。
    """
    from types import SimpleNamespace

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def novel_detail(self, novel_id):
            # 真实形状：顶层只有 novel 一个键
            return wrap_json({"novel": {"id": novel_id, "x_restrict": 1,
                                        "title": "标题", "image_urls": {"large": "u"},
                                        "user": {"name": "作者"}}})

        def novel_text(self, novel_id):
            return SimpleNamespace(text=None)     # pixiv 可能返回 null

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    c = PixivClient("tok", "")
    detail = await c.novel_detail(555)
    assert detail.x_restrict == 1                  # 解包后取得到
    assert detail.title == "标题"
    assert await c.novel_text(555) == ""           # None → ""


@pytest.mark.asyncio
async def test_author_info_reads_name_and_avatar(monkeypatch):
    """作者名/头像从 user_novels().user 里取（订阅列表要显示它们）。"""
    class FakeProfile:
        medium = "https://i.pximg.net/avatar.jpg"

    class FakeUser:
        name = "作者甲"
        profile_image_urls = FakeProfile()

    class FakeResult:
        user = FakeUser()

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def user_novels(self, author_id):
            return FakeResult()

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    c = PixivClient("tok", "")
    assert await c.author_info(1) == ("作者甲", "https://i.pximg.net/avatar.jpg")


@pytest.mark.asyncio
async def test_author_info_returns_empty_on_failure(monkeypatch):
    """取作者信息失败**不能**让订阅流程挂掉。"""
    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def user_novels(self, author_id):
            raise RuntimeError("api down")

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    c = PixivClient("tok", "")
    assert await c.author_info(1) == ("", "")


@pytest.mark.asyncio
async def test_author_info_tolerates_missing_profile_image_urls(monkeypatch):
    """头像字段可能是 None（老作品/被封号），不能因此抛 AttributeError。"""
    class FakeUser:
        name = "作者乙"
        profile_image_urls = None

    class FakeResult:
        user = FakeUser()

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def user_novels(self, author_id):
            return FakeResult()

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    c = PixivClient("tok", "")
    assert await c.author_info(1) == ("作者乙", "")


@pytest.mark.asyncio
async def test_fetch_image_sends_referer(monkeypatch):
    """封面/头像都必须带 Referer，否则 i.pximg.net 返回 403。"""
    captured = {}

    class FakeResp:
        content = _png_bytes()

        def raise_for_status(self):
            pass

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
    c = PixivClient("tok", "http://p:1")
    out = await c.fetch_image("https://i.pximg.net/x.jpg")
    assert captured["headers"]["Referer"] == "https://www.pixiv.net/"
    assert captured["proxy"] == "http://p:1"
    assert out == _png_bytes()          # fetch_image 本身不模糊，原样返回


def test_blur_image_uses_given_radius_verbatim(monkeypatch):
    """需求：模糊半径是**固定像素**（6–12px），**不随图片尺寸缩放**。"""
    from PIL import ImageFilter

    captured = []
    real_gaussian = ImageFilter.GaussianBlur

    def spy(radius=0):
        captured.append(radius)
        return real_gaussian(radius)

    monkeypatch.setattr(pixiv_client.ImageFilter, "GaussianBlur", spy)
    # 同一个 radius 作用在尺寸悬殊的两张图上，传进 Pillow 的值必须完全一样
    blur_image(_png_bytes(size=(100, 200)), radius=9)
    blur_image(_png_bytes(size=(640, 1216)), radius=9)
    assert captured == [9, 9]


def test_blur_radius_bounds_are_passed_through(monkeypatch):
    """需求边界 6 与 12 必须原样传给 Pillow（不被缩放/四舍五入）。"""
    from PIL import ImageFilter

    captured = []
    real_gaussian = ImageFilter.GaussianBlur

    def spy(radius=0):
        captured.append(radius)
        return real_gaussian(radius)

    monkeypatch.setattr(pixiv_client.ImageFilter, "GaussianBlur", spy)
    blur_image(_png_bytes(), radius=6)
    blur_image(_png_bytes(), radius=12)
    assert captured == [6, 12]


def test_blur_image_changes_pixels_and_stays_decodable():
    out = blur_image(_png_bytes(), radius=12)
    assert out != _png_bytes()
    assert Image.open(io.BytesIO(out)).size == (40, 40)


@pytest.mark.asyncio
async def test_download_cover_applies_blur(monkeypatch):
    class FakeResp:
        content = _png_bytes(size=(640, 1216))

        def raise_for_status(self):
            pass

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
    c = PixivClient("tok", "")
    out = await c.download_cover("https://i.pximg.net/x.jpg", blur=True, radius=9)
    assert out != _png_bytes(size=(640, 1216))     # 模糊生效
    assert Image.open(io.BytesIO(out)).size == (640, 1216)   # 尺寸不变


@pytest.mark.asyncio
async def test_download_cover_can_skip_blur(monkeypatch):
    # ⚠️ 图必须**够大**（> 160px）：小图会被「占位图检测」当成 pixiv 的
    # limit_unknown 占位图丢掉（返回空字节），这条测试就没意义了。
    big = _png_bytes(size=(640, 900))

    class FakeResp:
        content = big

        def raise_for_status(self):
            pass

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
    c = PixivClient("tok", "")
    out = await c.download_cover("https://i.pximg.net/x.jpg", blur=False, radius=9)
    # 既不模糊也不缩放 → **原样返回**，不重编码（省 CPU、不掉一次质量）
    assert out == big


# ══════════════════════════════════════════════════════════════════
# 回归：novel_detail 的响应顶层是 {"novel": {...}}（真实结构，实测于 2026-10-02）
#
# 这一组测试是为了防止下面这个**静默**故障再次发生：
#   pixivpy3 的 JsonDict 对缺失的键返回 None 而**不抛异常**，所以
#   `getattr(detail, "x_restrict", 0)` 拿不到默认值 0，只会拿到 None。
#   结果：R18 判定恒为 0（R18 全文会被当普通作品发进群，违反硬要求）、
#   封面 URL 恒为空（需求要的封面图没了）、标题变成 "📖 None"。
#   三处都**不报错**，只表现为「功能莫名不生效」。
# ══════════════════════════════════════════════════════════════════


def test_fixture_reproduces_real_nesting():
    """fixture 本身要是真实形状：顶层只有 novel（这行挂了说明录错了）。"""
    raw = load_fixture("novel_detail_r18.json")
    assert sorted(raw.keys()) == ["novel"]
    assert raw.novel.x_restrict == 1
    assert raw.novel.tags[0].name == "R-18"      # tags 里是 name 字段，不是 tag


def test_raw_response_loses_all_fields_without_unwrapping():
    """**核心回归**：不解包时字段全取不到 —— 这就是那个真 bug 的样子。

    注意 `getattr` 的默认值 0 **不起作用**（JsonDict 让键「存在但为 None」）。
    """
    raw = load_fixture("novel_detail_r18.json")
    assert raw.x_restrict is None                       # 不是 0！
    assert getattr(raw, "x_restrict", 0) is None        # 默认值也救不了
    assert raw.title is None
    assert raw.image_urls is None
    assert raw.user is None
    assert int(raw.x_restrict or 0) == 0                # → R18 判定会退化成 0


def test_unwrap_exposes_every_field_the_plugin_needs():
    """解包后，插件依赖的字段全都要拿得到。"""
    novel = unwrap_novel_shape(load_fixture("novel_detail_r18.json"))

    assert int(novel.id) == 29277050                 # 推送去重/高水位
    assert novel.x_restrict == 1                     # R18 判定
    assert "むっちむち" in novel.title                # 推送文案标题
    assert novel.image_urls.large.startswith("https://i.pximg.net/")   # 封面
    assert novel.user.name                           # 作者名
    assert int(novel.user.id) > 0                    # 作者链接
    assert [t.name for t in novel.tags][:1] == ["R-18"]               # 标签


def test_unwrap_is_idempotent_for_already_unwrapped_input():
    """喂已解包的对象也要原样返回（别的调用点可能传进来过）。"""
    inner = {"id": 1, "title": "x"}
    assert unwrap_novel_shape(inner) is inner
    assert unwrap_novel_shape("别的类型") == "别的类型"


@pytest.mark.asyncio
async def test_novel_detail_from_real_fixture_end_to_end(monkeypatch):
    """端到端：把真实 fixture 当 API 响应喂进去，插件拿到的是解包后的对象。"""
    raw = load_fixture("novel_detail_r18.json")

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def novel_detail(self, novel_id):
            return raw

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    d = await PixivClient("tok", "").novel_detail(29277050)

    assert d.x_restrict == 1
    assert d.image_urls.large.startswith("https://i.pximg.net/")
    assert d.user.name == "さむしんぐ"
    assert d.user.id == 61943687
    # 这些正是 poller / handlers 真正会读的取值方式
    assert int(getattr(d, "x_restrict", 0) or 0) == 1
    assert (getattr(getattr(d, "image_urls", None), "large", "") or "") != ""


@pytest.mark.asyncio
async def test_real_user_novels_fixture_has_x_restrict_per_item(monkeypatch):
    """poller 播种/高水位依赖 user_novels 条目上的 x_restrict —— 实测确实有。"""
    raw = load_fixture("user_novels.json")

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def user_novels(self, author_id):
            return raw

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    c = PixivClient("tok", "")
    novels = await c.user_novels(61943687)
    assert novels and int(novels[0].id) > 0
    assert novels[0].x_restrict is not None        # 条目自带，无需额外请求
    # author_info 走的也是这个响应
    assert await c.author_info(61943687) == (raw.user.name, raw.user.profile_image_urls.medium)


@pytest.mark.asyncio
async def test_real_novel_text_fixture_has_no_x_restrict(monkeypatch):
    """novel_text 的响应**没有** x_restrict —— 所以 R18 判定不能图省事在这接口做。"""
    raw = load_fixture("novel_text.json")
    assert raw.x_restrict is None
    assert raw.get("x_restrict") is None
    assert len(raw.text) > 100

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def novel_text(self, novel_id):
            return raw

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    text = await PixivClient("tok", "").novel_text(29277050)
    assert len(text) > 100


def test_check_novel_shape_warns_when_fields_missing(caplog):
    """结构变了要**大声告警** —— 否则又是静默降级，群里只看到功能莫名失效。"""
    import logging

    from nonebot_plugin_pixiv_novel.pixiv_client import _check_novel_shape

    with caplog.at_level(logging.WARNING, logger="nonebot_plugin_pixiv_novel"):
        _check_novel_shape(wrap_json({"novel": {"id": 1}}))        # 还没解包
    assert "缺少字段" in caplog.text
    for f in ("title", "x_restrict", "image_urls", "user"):
        assert f in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nonebot_plugin_pixiv_novel"):
        _check_novel_shape(unwrap_novel_shape(load_fixture("novel_detail_r18.json")))
    assert caplog.text == ""                       # 正常形状不该有告警


def test_check_novel_shape_accepts_x_restrict_zero(caplog):
    """x_restrict=0（普通作品）是**合法值**，不能被当成「缺字段」。"""
    import logging

    from nonebot_plugin_pixiv_novel.pixiv_client import _check_novel_shape

    novel = wrap_json({"id": 1, "title": "t", "x_restrict": 0,
                       "image_urls": {"large": "u"}, "user": {"name": "a"}})
    with caplog.at_level(logging.WARNING, logger="nonebot_plugin_pixiv_novel"):
        _check_novel_shape(novel)
    assert caplog.text == ""


# ── 网络重试（实测 pixiv 经代理会偶发 SSL EOF）──────────────────


def test_retry_sync_retries_then_succeeds(monkeypatch):
    """瞬时 SSL EOF 不该让整个订阅作者被跳过一轮。"""
    from nonebot_plugin_pixiv_novel.pixiv_client import _retry_sync

    monkeypatch.setattr(pixiv_client.time, "sleep", lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("SSL: UNEXPECTED_EOF_WHILE_READING")
        return "ok"

    assert _retry_sync(flaky, tries=3) == "ok"
    assert len(calls) == 3


def test_retry_sync_reraises_after_exhausting(monkeypatch):
    """一直失败就把真实异常抛出去（调用方要记日志），不能吞掉。"""
    from nonebot_plugin_pixiv_novel.pixiv_client import _retry_sync

    monkeypatch.setattr(pixiv_client.time, "sleep", lambda s: None)

    def always_fail():
        raise OSError("boom")

    with pytest.raises(OSError, match="boom"):
        _retry_sync(always_fail, tries=2)


def test_retry_sync_success_costs_no_sleep(monkeypatch):
    """正常路径不能有 sleep —— 否则每次调用都白等。"""
    from nonebot_plugin_pixiv_novel.pixiv_client import _retry_sync

    slept = []
    monkeypatch.setattr(pixiv_client.time, "sleep", lambda s: slept.append(s))
    assert _retry_sync(lambda: 42, tries=3) == 42
    assert slept == []


# ── 鉴权过期自愈 ────────────────────────────────────────────────────
#
# 生产故障（2026-10-04）的日志签名：
#
#   [WARNING] novel_detail 解包后缺少字段 ['id','title','x_restrict','image_urls','user']
#             —— pixiv 响应结构可能变了。现有键=['error'].
#   [message_sent] 取 novel 29275792 失败，确认链接是否有效
#   [WARNING] URL hook 处理 novel=29275792 结束于异常: AttributeError: 'NoneType' ...'name'
#
# 根因：pixiv 的 access_token 只有 **1 小时**寿命；pixivpy3 的 `require_auth()`
# 只判 `is None`、不判过期，也**没有 401 重试**；而 bearer 失效时 pixiv 返回的是
# **HTTP 200 + 错误 JSON**，`parse_result()` 只做 `json.loads`、不抛异常。
# 于是「缓存实例 + 只捕异常的重试」= 一过期就**永久失败到进程重启**，
# 且**进程越老越失败**（生产里同一篇小说：新进程能取到、跑两天的进程报 error）。
#
# 修法两层：`_get_api()` 按 TTL 主动重建 + `_call()` 把错误载荷当失败。

# 生产日志里那条真实错误体（HTTP 200，键只有 error）
_PROD_AUTH_ERROR = {
    "error": {
        "user_message": "",
        "message": (
            "Error occurred at the OAuth process. Please check your Access Token"
            " to fix this. Error Message: invalid_grant"
        ),
        "reason": "",
        "user_message_details": {},
    }
}


def test_is_error_payload_recognizes_the_real_production_error():
    """必须认出生产那条「HTTP 200 + error 体」—— 这是修这个 bug 的前提。"""
    assert pixiv_client.is_error_payload(wrap_json(_PROD_AUTH_ERROR))


@pytest.mark.parametrize(
    "fixture", ["novel_detail_r18.json", "user_novels.json", "novel_text.json"]
)
def test_is_error_payload_does_not_misfire_on_real_success(fixture):
    """真实成功响应不能被误判成错误 —— 误判会让正常作品全部「取失败」。"""
    assert not pixiv_client.is_error_payload(load_fixture(fixture))


@pytest.mark.parametrize("value", [None, "", "error", [], 0])
def test_is_error_payload_ignores_non_dict(value):
    assert not pixiv_client.is_error_payload(value)


def test_auth_ttl_sits_below_the_one_hour_token_lifetime():
    """TTL 必须 < 1 小时（留余量），也不能过短（否则每次调用都白换一次 token）。"""
    assert 10 * 60 <= pixiv_client._AUTH_TTL_SECONDS < 60 * 60


def test_get_api_rebuilds_only_after_ttl(monkeypatch):
    """TTL 到期**主动**重建鉴权；TTL 内复用同一实例（不白换 token）。"""
    monkeypatch.setattr(pixiv_client, "_AUTH_TTL_SECONDS", 100.0)
    clock = {"t": 1000.0}
    monkeypatch.setattr(pixiv_client.time, "monotonic", lambda: clock["t"])
    made = []

    class FakeAPI:
        def __init__(self, **kwargs):
            made.append(self)

        def auth(self, refresh_token):
            pass

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    client = PixivClient("tok", "")

    client._get_api()
    client._get_api()
    assert len(made) == 1, "TTL 内应复用，不该重复鉴权"

    clock["t"] += 99
    client._get_api()
    assert len(made) == 1, "还没到 TTL 就不该重建"

    clock["t"] += 2  # 累计 101s > 100s
    client._get_api()
    assert len(made) == 2, "过了 TTL 必须重建鉴权（这正是原 bug 的正面修复）"


@pytest.mark.asyncio
async def test_novel_detail_reauths_and_recovers_from_expired_token(monkeypatch):
    """★ 核心回归：静默 error 载荷 ⇒ 丢弃旧实例、重新鉴权、取回作品。

    修复前这里会**静默返回** `{'error': ...}`：`x_restrict`/`user` 全是 None，
    于是 R18 判定失效 + URL hook 里 `user.name` 直接 AttributeError。
    """
    monkeypatch.setattr(pixiv_client.time, "sleep", lambda _s: None)
    made = []

    class FakeAPI:
        def __init__(self, **kwargs):
            made.append(self)
            self.auth_calls = 0

        def auth(self, refresh_token):
            self.auth_calls += 1

        def novel_detail(self, novel_id):
            if len(made) == 1:  # 第 1 个实例 = 已经过期的那一个
                return wrap_json(_PROD_AUTH_ERROR)
            return load_fixture("novel_detail_r18.json")

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    client = PixivClient("tok", "")

    novel = await client.novel_detail(29275792)

    assert len(made) == 2, "必须丢弃过期实例并重建鉴权"
    assert made[1].auth_calls == 1, "新实例要真的做过 auth()"
    assert novel["x_restrict"] == 1, "重新鉴权后必须真的取到作品（R18 判定可用）"
    assert novel["user"]["name"], "user 不能再是 None（那是 AttributeError 的来源）"


@pytest.mark.asyncio
async def test_novel_detail_raises_when_error_payload_persists(monkeypatch):
    """一直失败就要**抛异常**，不能把错误当数据用（调用方才能报错而非发卡片）。"""
    monkeypatch.setattr(pixiv_client.time, "sleep", lambda _s: None)

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def novel_detail(self, novel_id):
            return wrap_json(_PROD_AUTH_ERROR)

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    client = PixivClient("tok", "")

    with pytest.raises(PixivError, match="invalid_grant"):
        await client.novel_detail(29275792)


@pytest.mark.asyncio
async def test_novel_text_reauths_after_pixiv_error(monkeypatch):
    """`novel_text` 的失效表现与 detail **不同**（它是抛 PixivError），同样要自愈。"""
    monkeypatch.setattr(pixiv_client.time, "sleep", lambda _s: None)
    made = []

    class FakeAPI:
        def __init__(self, **kwargs):
            made.append(self)

        def auth(self, refresh_token):
            pass

        def novel_text(self, novel_id):
            if len(made) == 1:
                raise PixivError(
                    "Extract novel content error: 'NoneType' object has no attribute 'groups'"
                )
            return load_fixture("novel_text.json")

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    client = PixivClient("tok", "")

    text = await client.novel_text(29275792)

    assert len(made) == 2, "抛异常时也要换掉实例再试"
    assert text, "重新鉴权后要拿到正文"


@pytest.mark.asyncio
async def test_user_novels_reauths_on_error_payload(monkeypatch):
    """订阅轮询也走这条出口 —— 否则鉴权一过期就整个轮询白跑。"""
    monkeypatch.setattr(pixiv_client.time, "sleep", lambda _s: None)
    made = []

    class FakeAPI:
        def __init__(self, **kwargs):
            made.append(self)

        def auth(self, refresh_token):
            pass

        def user_novels(self, user_id):
            if len(made) == 1:
                return wrap_json(_PROD_AUTH_ERROR)
            return load_fixture("user_novels.json")

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    client = PixivClient("tok", "")

    novels = await client.user_novels(12345)

    assert len(made) == 2
    assert len(novels) == 3, "要拿到真实作品列表"


@pytest.mark.asyncio
async def test_user_novels_raises_instead_of_silently_returning_empty(monkeypatch):
    """不能「静默返回空列表」—— 那会让轮询把一轮新作永久跳过。"""
    monkeypatch.setattr(pixiv_client.time, "sleep", lambda _s: None)

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def user_novels(self, user_id):
            return wrap_json(_PROD_AUTH_ERROR)

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    client = PixivClient("tok", "")

    with pytest.raises(PixivError):
        await client.user_novels(12345)
