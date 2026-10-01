import io

import pytest
from nonebot_plugin_pixiv_novel import pixiv_client
from nonebot_plugin_pixiv_novel.pixiv_client import PixivClient, blur_image, build_api
from PIL import Image


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
async def test_novel_detail_and_novel_text_delegate(monkeypatch):
    """detail 返回对象本身；text 抽 `.text` 并把 None 归一成空串。"""
    from types import SimpleNamespace

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def novel_detail(self, novel_id):
            return SimpleNamespace(id=novel_id, x_restrict=1)

        def novel_text(self, novel_id):
            return SimpleNamespace(text=None)     # pixiv 可能返回 null

    monkeypatch.setattr(pixiv_client, "AppPixivAPI", FakeAPI)
    c = PixivClient("tok", "")
    assert (await c.novel_detail(555)).x_restrict == 1
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
    class FakeResp:
        content = _png_bytes()

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
    assert out == _png_bytes()
