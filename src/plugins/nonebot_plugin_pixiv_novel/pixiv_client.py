"""pixivpy3 的异步包装。

pixivpy3 是同步库（基于 requests），在异步机器人里直接调用会**阻塞整个事件循环**
（表现为机器人卡住不响应）。所以每个 API 调用都用 asyncio.to_thread() 丢进线程池。
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import httpx
from PIL import Image, ImageFilter
from pixivpy3 import AppPixivAPI

logger = logging.getLogger("nonebot_plugin_pixiv_novel")

# i.pximg.net 会校验 Referer，不带就 403（已实测）
COVER_REFERER = "https://www.pixiv.net/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def build_api(refresh_token: str, proxy: str) -> AppPixivAPI:
    """构造已鉴权的 AppPixivAPI。

    ⚠️ requests 的参数名是 `proxies`（dict），不是 `proxy`；写成 proxy= 会静默失效。
    """
    kwargs: dict[str, Any] = {}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    api = AppPixivAPI(**kwargs)
    api.auth(refresh_token=refresh_token)
    return api


def blur_image(data: bytes, radius: int) -> bytes:
    """高斯模糊。**半径是固定像素值**（配置项 PIXIV_BLUR_RADIUS，6–12px）。

    不按图片尺寸缩放 —— 用户明确要求固定 px 半径。
    统一转 RGB 并以 JPEG 输出（封面是照片类内容，JPEG 体积小）。
    """
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = img.filter(ImageFilter.GaussianBlur(radius=radius))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


class PixivClient:
    """持有单个 AppPixivAPI 实例，惰性构造并复用。"""

    def __init__(self, refresh_token: str, proxy: str = "") -> None:
        self.refresh_token = refresh_token
        self.proxy = proxy
        self._api: AppPixivAPI | None = None

    def _get_api(self) -> AppPixivAPI:
        if self._api is None:
            self._api = build_api(self.refresh_token, self.proxy)
        return self._api

    async def user_novels(self, author_id: int) -> list[Any]:
        """作者的作品列表（新版在前）。"""
        return await asyncio.to_thread(lambda: self._get_api().user_novels(author_id).novels)

    async def author_info(self, author_id: int) -> tuple[str, str]:
        """取作者的 `(名字, 头像URL)`。取不到时返回 `("", "")`，**不抛异常**。

        订阅时顺手存下来，供订阅列表显示（§2.5）。
        """
        try:
            result = await asyncio.to_thread(lambda: self._get_api().user_novels(author_id))
            user = getattr(result, "user", None)
            if user is None:
                return "", ""
            name = str(getattr(user, "name", "") or "")
            avatar = str(getattr(getattr(user, "profile_image_urls", None), "medium", "") or "")
            return name, avatar
        except Exception as e:
            logger.warning(f"取作者 {author_id} 信息失败: {type(e).__name__}: {e}")
            return "", ""

    async def novel_detail(self, novel_id: int) -> Any:
        """作品详情 → models.NovelInfo（这里有 x_restrict / image_urls / tags）。"""
        return await asyncio.to_thread(lambda: self._get_api().novel_detail(novel_id))

    async def novel_text(self, novel_id: int) -> str:
        """作品全文 → models.WebviewNovel.text。"""
        def _call() -> str:
            nv = self._get_api().novel_text(novel_id)
            return nv.text or ""

        return await asyncio.to_thread(_call)

    async def fetch_image(self, url: str) -> bytes:
        """下载图片（封面/头像通用）。**必须带 Referer**，否则 i.pximg.net 返回 403。"""
        headers = {"Referer": COVER_REFERER, "User-Agent": _UA}
        kwargs: dict[str, Any] = {}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers=headers, **kwargs
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def download_cover(self, url: str, *, blur: bool, radius: int) -> bytes:
        """下载封面，可选高斯模糊（`blur=True` 时用 `radius` 像素作半径）。"""
        data = await self.fetch_image(url)
        if blur:
            # 纯 CPU 操作，丢线程池避免阻塞事件循环
            data = await asyncio.to_thread(blur_image, data, radius)
        return data
