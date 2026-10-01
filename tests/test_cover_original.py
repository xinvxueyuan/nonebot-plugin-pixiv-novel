"""封面「取原图 + 占位图防护 + 缩放」的测试。

背景（实测 2026-10-02，把图下下来量像素）：
  · App `image_urls.large` = CDN 缩略 `…/c/240x480_80/…_master1200.jpg` → 实际 240x347
  · 去掉 `/c/…/` 缩放段 → 同一个文件的**原图** 828x1200（体积约 37 倍）
  · App 与 Web 的封面**是同一个文件**，只差缩放前缀 → 不需要换接口
  · App 对「不可见/受限」作品**不抛异常**，而是给 `limit_unknown_100.png`（100x100）

这里钉住这四条结论，以及「用户要求发原图」这个决定。
"""

from __future__ import annotations

import io

import pytest
from nonebot_plugin_pixiv_novel import pixiv_client
from nonebot_plugin_pixiv_novel.pixiv_client import (
    PixivClient,
    image_size,
    is_placeholder_image,
    is_placeholder_url,
    original_cover_url,
    process_cover,
)
from PIL import Image, ImageFilter

# 真实的 App 缩略 URL 与它的原图 URL（同一文件，实测）
SCALED = (
    "https://i.pximg.net/c/240x480_80/novel-cover-master/img/2026/04/14/16/26/17/"
    "sci15744810_da0194f312f9c61cd416f5cd3cc61812_master1200.jpg"
)
ORIGINAL = (
    "https://i.pximg.net/novel-cover-master/img/2026/04/14/16/26/17/"
    "sci15744810_da0194f312f9c61cd416f5cd3cc61812_master1200.jpg"
)
SQUARE = (
    "https://i.pximg.net/c/128x128/novel-cover-master/img/2026/04/14/16/26/17/"
    "sci15744810_da0194f312f9c61cd416f5cd3cc61812_square1200.jpg"
)


def _png(size=(640, 900), color=(200, 60, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════
# original_cover_url()：把缩略 URL 换成原图 URL
# ══════════════════════════════════════════════════════════════════


def test_original_cover_url_strips_cdn_scale_segment():
    """去掉 `/c/240x480_80/` 就是原图 —— 这是「App 只给小图」的正解。"""
    assert original_cover_url(SCALED) == ORIGINAL


def test_original_cover_url_handles_every_scale_form():
    """CDN 缩放段有 `_80` 质量后缀、也有没有后缀的，两种都要认。"""
    for spec in ("/c/240x480_80/", "/c/600x600/", "/c/128x128/", "/c/1200x1200/"):
        assert original_cover_url(SCALED.replace("/c/240x480_80/", spec)).startswith(
            "https://i.pximg.net/novel-cover-master/"
        )


def test_original_cover_url_leaves_square_images_alone():
    """⚠️ `_square1200` 是**裁切过的方图**，去掉 /c/ 段会变成 1200x1200 方图。

    实测过：`…_square1200.jpg` 去掉缩放段拿到的是 1200x1200 的方图，
    不是封面比例 —— 所以这类必须原样返回，别去动它。
    """
    assert original_cover_url(SQUARE) == SQUARE
    assert "/c/128x128/" in original_cover_url(SQUARE)


def test_original_cover_url_is_idempotent():
    """已经去过的再调一次还是原样（poller 与 hook 两条链路都可能调）。"""
    once = original_cover_url(SCALED)
    assert original_cover_url(once) == once


def test_original_cover_url_handles_empty_and_odd_input():
    assert original_cover_url("") == ""
    assert original_cover_url("https://example.com/a.jpg") == "https://example.com/a.jpg"
    # 头像 URL 没有 /c/ 段，套用无副作用
    avatar = "https://i.pximg.net/user-profile/img/2026/03/09/23/11/38/28616698_x_170.jpg"
    assert original_cover_url(avatar) == avatar


# ══════════════════════════════════════════════════════════════════
# 占位图防护（这个缺陷在真机上撞到过）
# ══════════════════════════════════════════════════════════════════


def test_placeholder_url_is_detected():
    """App 对不可见/受限作品给的是这些占位图 URL（实测作品 29000000）。"""
    assert is_placeholder_url("https://s.pximg.net/common/images/limit_unknown_100.png")
    assert is_placeholder_url("https://s.pximg.net/common/images/no_profile.png")
    assert is_placeholder_url("")                       # 空 = 没有封面
    assert not is_placeholder_url(ORIGINAL)


def test_placeholder_image_is_detected_by_size():
    """字节层面兜底：100x100 的占位图必须被认出来（URL 特征可能漏）。"""
    assert is_placeholder_image(_png(size=(100, 100)))
    assert is_placeholder_image(_png(size=(160, 160)))      # 阈值边界内
    assert not is_placeholder_image(_png(size=(161, 161)))  # 刚过阈值
    assert not is_placeholder_image(_png(size=(640, 900)))  # 真实封面


def test_undecodable_bytes_are_not_treated_as_placeholder():
    """解不开的图**不能**当占位图吞掉 —— 那会把真实的下载损坏伪装成「没封面」。"""
    assert not is_placeholder_image(b"not an image at all")


def test_image_size_reads_header_only():
    assert image_size(_png(size=(300, 400))) == (300, 400)
    assert image_size(b"garbage") is None


# ══════════════════════════════════════════════════════════════════
# process_cover()：先缩放、后模糊
# ══════════════════════════════════════════════════════════════════


def test_process_cover_returns_bytes_untouched_when_noop():
    """max_width=0 且不模糊 → 原样返回（不重编码，省 CPU 也不掉质量）。"""
    src = _png()
    assert process_cover(src, blur=False, radius=9, max_width=0) == src


def test_process_cover_resizes_to_max_width_keeping_ratio():
    out = process_cover(_png(size=(901, 1200)), blur=False, radius=9, max_width=800)
    assert image_size(out) == (800, round(1200 * 800 / 901))


def test_process_cover_does_not_upscale_small_images():
    """比 max_width 还小的图**不放大** —— 放大只会糊，不会变清晰。"""
    src = _png(size=(400, 600))
    assert process_cover(src, blur=False, radius=9, max_width=800) == src


def test_process_cover_blurs_and_reencodes():
    out = process_cover(_png(size=(640, 900)), blur=True, radius=9, max_width=0)
    assert out.startswith(b"\xff\xd8")               # JPEG
    assert abs(image_size(out)[0] - 640) <= 1        # 尺寸没变


def test_process_cover_scales_before_blurring_not_after():
    """⚠️ 顺序断言：**先缩到 800、再模糊 9px**。

    用户的半径语义是「固定像素」（6–12px），必须作用在**最终发出的那张图**上。
    反过来（先模糊、再缩到 800）会把半径一起缩掉，「固定 9px」就名不副实了。

    做法：拿本函数的输出和手工「先缩后模糊」的结果逐像素比 —— 顺序写反了这里必红。
    """
    src = _png(size=(901, 1200), color=(180, 80, 40))

    out = process_cover(src, blur=True, radius=9, max_width=800)

    img = Image.open(io.BytesIO(src)).convert("RGB")
    resized = img.resize((800, round(1200 * 800 / 901)), Image.Resampling.LANCZOS)
    manual = resized.filter(ImageFilter.GaussianBlur(radius=9))
    buf = io.BytesIO()
    manual.save(buf, format="JPEG", quality=88)

    assert out == buf.getvalue()


def test_process_cover_converts_palette_images_before_jpeg():
    """调色板/带 alpha 的 PNG 直接存 JPEG 会炸 —— 必须先转 RGB。"""
    buf = io.BytesIO()
    Image.new("P", (640, 900)).save(buf, format="PNG")
    out = process_cover(buf.getvalue(), blur=True, radius=9, max_width=0)
    assert out.startswith(b"\xff\xd8")


# ══════════════════════════════════════════════════════════════════
# download_cover()：占位图不发起下载、返回空字节
# ══════════════════════════════════════════════════════════════════


class _FakeHttp:
    def __init__(self, content: bytes, *, counter: list | None = None):
        self._content = content
        self._counter = counter

    def install(self, monkeypatch):
        content, counter = self._content, self._counter

        class _C:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                if counter is not None:
                    counter.append(url)

                class R:
                    pass

                r = R()
                r.content = content
                r.raise_for_status = lambda: None
                return r

        monkeypatch.setattr(pixiv_client.httpx, "AsyncClient", _C)


@pytest.mark.asyncio
async def test_download_cover_skips_network_for_placeholder_url(monkeypatch):
    """占位图 URL **不该发起下载** —— 省一次无用的网络请求（经代理很贵）。"""
    calls: list = []
    _FakeHttp(_png(size=(640, 900)), counter=calls).install(monkeypatch)

    out = await PixivClient("tok", "").download_cover(
        "https://s.pximg.net/common/images/limit_unknown_100.png", blur=False, radius=9
    )
    assert out == b""
    assert calls == []          # 一个请求都没发


@pytest.mark.asyncio
async def test_download_cover_drops_downloaded_placeholder(monkeypatch):
    """URL 没特征、但下到的图是 100x100 → 也得丢掉（字节层面兜底）。"""
    _FakeHttp(_png(size=(100, 100))).install(monkeypatch)

    out = await PixivClient("tok", "").download_cover(
        "https://i.pximg.net/c/600x600/novel-cover-master/img/x_master1200.jpg",
        blur=False,
        radius=9,
    )
    assert out == b""


@pytest.mark.asyncio
async def test_download_cover_passes_max_width_down(monkeypatch):
    _FakeHttp(_png(size=(901, 1200))).install(monkeypatch)

    out = await PixivClient("tok", "").download_cover(
        "https://i.pximg.net/x_master1200.jpg", blur=False, radius=9, max_width=600
    )
    assert image_size(out) == (600, round(1200 * 600 / 901))


@pytest.mark.asyncio
async def test_download_cover_empty_url_returns_empty_bytes():
    """空 URL 直接返回空，**不抛异常** —— 调用方按「没封面」处理。"""
    assert await PixivClient("tok", "").download_cover("", blur=True, radius=9) == b""
