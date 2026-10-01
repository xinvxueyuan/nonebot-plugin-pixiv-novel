"""pixivpy3 的异步包装。

pixivpy3 是同步库（基于 requests），在异步机器人里直接调用会**阻塞整个事件循环**
（表现为机器人卡住不响应）。所以每个 API 调用都用 asyncio.to_thread() 丢进线程池。
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from collections.abc import Callable
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


# ── 封面原图 / 占位图 / 尺寸 ────────────────────────────────────────
#
# CDN 的缩放段：`/c/240x480_80/`、`/c/600x600/` 这种。**去掉它就是原图**。
# 实测（2026-10-02，真实下载后量像素）：
#   App `image_urls.large` = …/c/240x480_80/novel-cover-master/…_master1200.jpg → 240x347  24KB
#   Web `coverUrl`         = …/c/600x600/novel-cover-master/…_master1200.jpg   → 414x600 192KB
#   两者去掉 /c/ 段后**是同一个文件**                          → 828x1200 662KB（原图）
# 所以「App 给的是小图」不是因为接口差，而是 CDN 缩放档位；**不需要换接口**。
_COVER_SCALE_RE = re.compile(r"/c/\d+x\d+(?:_\d+)?/")

# pixiv 用这些图代替「不可见 / 受限 / 不存在」的封面。
# ⚠️ App 接口对这类作品**不抛异常**，而是返回占位图 URL（实测作品 29000000），
# 不检查就会把 100x100 的 limit_unknown 图当封面发进群里。
_PLACEHOLDER_URL_MARKERS = ("limit_unknown", "/common/images/", "no_profile")

# 真实小说封面最小也有 640x900；占位图是 100x100。160 是两者之间的安全阈值。
_PLACEHOLDER_MAX_SIDE = 160


def original_cover_url(url: str) -> str:
    """把 CDN 缩略封面 URL 换成**原图** URL。

    ⚠️ 只对 `_master1200` 结尾的图合适：`_square1200` 是**裁切过的方图**，
    去掉 /c/ 段会拿到 1200x1200 方图（不是封面比例）→ 这种原样返回。
    头像 URL 没有 /c/ 段（`…/xxx_170.jpg`），套用无副作用。
    """
    if not url:
        return ""
    if "_square" in url:
        return url
    return _COVER_SCALE_RE.sub("/", url)


def is_placeholder_url(url: str) -> bool:
    """URL 层面就能判出占位图 —— 省掉一次网络请求。"""
    if not url:
        return True
    low = url.lower()
    return any(m in low for m in _PLACEHOLDER_URL_MARKERS)


def image_size(data: bytes) -> tuple[int, int] | None:
    """读图片尺寸（PIL 懒加载，只读头部，不解码像素）。读不出来返 None。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:
        return None


def is_placeholder_image(data: bytes) -> bool:
    """字节层面兜底：尺寸过小就当占位图（防住 URL 特征没覆盖到的占位图）。"""
    size = image_size(data)
    if size is None:
        return False  # 解不开的图交给后面的流程报错，别在这儿吞掉
    return min(size) <= _PLACEHOLDER_MAX_SIDE


def process_cover(data: bytes, *, blur: bool, radius: int, max_width: int = 0) -> bytes:
    """封面后处理：先按需缩放，再按需模糊。统一以 JPEG 输出。

    ⚠️ 顺序是**先缩放、后模糊**：配置的半径是「固定像素」（用户明确要求 6–12px），
    必须作用在**最终发出的那张图**上；先模糊再缩放会把半径一起缩掉，
    「固定 9px」的语义就没了。

    `max_width<=0` 表示不缩放（发原图）。既没缩放也没模糊时**原样返回字节**，
    不重编码（省 CPU、也不掉一次质量）。
    """
    img = Image.open(io.BytesIO(data))
    if img.mode != "RGB":
        img = img.convert("RGB")

    resized = False
    if max_width > 0 and img.width > max_width:
        height = max(1, round(img.height * max_width / img.width))
        img = img.resize((max_width, height), Image.Resampling.LANCZOS)
        resized = True

    if blur:
        img = img.filter(ImageFilter.GaussianBlur(radius=radius))

    if not resized and not blur:
        return data

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# 解包后我们**依赖**的字段。缺任何一个都会让功能静默降级，
# 所以解包后要显式体检一遍并告警（见 `_check_novel_shape`）。
_REQUIRED_NOVEL_FIELDS = ("id", "title", "x_restrict", "image_urls", "user")


def unwrap_novel_shape(resp: Any) -> Any:
    """把 `/v2/novel/detail` 的响应解包到「作品本体」。

    ⚠️⚠️ 实测（2026-10-02，真实响应已录成 tests/fixtures/novel_detail_r18.json）：
    该接口的**顶层是 `{"novel": {...}}`，所有字段都在下一层**。
    直接返回顶层对象会静默造成三处功能失效，因为 pixivpy3 的 `JsonDict`
    对**缺失的键返回 None 而不是抛异常** —— `getattr(d, "x_restrict", 0)` 这种
    带默认值的写法**拿不到默认值**，只会拿到 None：

      · `detail.x_restrict` → None → R18 判定恒为 0
        → R18 全文会被当普通作品投递，**违反「R18 正文缺省不发群聊」的硬要求**
        → `PIXIV_R18_PUSH_ENABLED` 开关也彻底失效
      · `detail.image_urls` → None → 封面永远拿不到（需求要的封面图没了）
      · `detail.title` → None → 推送文案变成「📖 None」

    这三条都**不会报错**，只会在群里表现为「功能莫名其妙不生效」。
    """
    return resp["novel"] if isinstance(resp, dict) and "novel" in resp else resp


def _check_novel_shape(novel: Any) -> None:
    """解包后体检：字段缺失就大声告警，别让结构变动静默降级。"""
    missing = [f for f in _REQUIRED_NOVEL_FIELDS if novel.get(f) is None]
    if missing:
        logger.warning(
            f"novel_detail 解包后缺少字段 {missing} —— pixiv 响应结构可能变了。"
            f"现有键={sorted(novel.keys())[:20] if hasattr(novel, 'keys') else novel!r}. "
            f"依赖这些字段的功能（R18 判定/封面/标题）会降级。"
        )


# ── 小说系列：走**网页版** AJAX 接口（不是 App 接口）──────────────────
#
# ⚠️ 为什么必须是网页接口（2026-10-02 实测两套接口对比，脚本 scripts/probe_series_r18.py
#    与 scripts/probe_series_web_body.py）：
#
#   App `/v2/novel/series`     → 顶层 {novel_series_detail, novel_series_first_novel, …}
#                                `novel_series_detail` **只有 11 个键，没有任何 R18 字段**
#                                （拿一个 4 篇全 R18 的系列验过，排除「false 被省略」的假象）
#   Web `/ajax/novel/series/N` → `body.xRestrict` **就是系列级 R18**（R18 系列=1，非 R18=0，
#                                非 R18 时是**显式的 0 而不是缺键**）
#
# 走 App 接口就只能「回头去查首篇的 x_restrict」来猜系列 R18 —— 那既多一次请求，
# 又对「首篇非 R18 但系列含 R18」的混合系列判错。网页接口一次调用直接给全部字段：
# xRestrict / title / caption / userName / userId / profileImageUrl /
# publishedContentCount / publishedTotalCharacterCount / isConcluded / tags / cover.urls。
SERIES_WEB_API = "https://www.pixiv.net/ajax/novel/series/{series_id}"

# 系列封面的候选键，**大的优先**（群里看得清）。
# ⚠️ 这里曾经把 480mw 排在最前，理由是「太大拖慢经代理的下载」—— 实测推翻：
#   同一张图 original=1280x1856(345KB) / 1200x1200=828x1200(678KB) / 480mw=480x696(240KB)
#   original 不但更大、字节还比 1200x1200 更小（PNG 压缩得好），排后面纯属白白发小图。
_SERIES_COVER_KEYS = ("original", "1200x1200", "480mw", "240mw", "128x128")


def series_cover_url(body: Any) -> str:
    """从网页接口的 `body.cover.urls` 里挑一个封面 URL；没有则空串。"""
    cover = (body.get("cover") or {}) if hasattr(body, "get") else {}
    urls = cover.get("urls") or {}
    if not isinstance(urls, dict):
        return ""
    for k in _SERIES_COVER_KEYS:
        v = urls.get(k)
        if v:
            return str(v)
    return ""


def _check_series_shape(body: Any) -> None:
    """系列响应体检：缺 `xRestrict` 要**大声告警**。

    缺了它 R18 系列会被当普通系列（封面不模糊）而且**不报错** ——
    这正是本项目反复踩的那类静默降级。
    """
    if not hasattr(body, "get"):
        logger.warning(f"novel_series 响应不是 dict（{type(body).__name__}），系列卡片会降级")
        return
    missing = [k for k in ("id", "title", "xRestrict") if body.get(k) is None]
    if missing:
        logger.warning(
            f"小说系列响应缺少字段 {missing} —— pixiv 网页接口结构可能变了。"
            f"现有键={sorted(body.keys())[:24]}. "
            f"缺 xRestrict 会让 R18 系列的封面**不被模糊**。"
        )


def _retry_sync(fn: Callable[[], Any], tries: int = 3, delay: float = 2.0) -> Any:
    """同步重试 —— pixiv 经代理偶发 SSL EOF / 连接重置（实测 10 分钟内遇到两次）。

    没有这层重试时，一次网络抖动就让**整轮轮询**白跑：所有订阅作者都拉不到，
    这一轮的新作被跳过（下次轮询时它们已不是「比高水位更新」，会被永久漏掉）。
    """
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt + 1 < tries:
                logger.debug(f"pixiv 调用第 {attempt + 1}/{tries} 次失败，重试：{e}")
                time.sleep(delay)
    assert last is not None
    raise last


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
        return await asyncio.to_thread(
            lambda: _retry_sync(lambda: self._get_api().user_novels(author_id).novels)
        )

    async def author_info(self, author_id: int) -> tuple[str, str]:
        """取作者的 `(名字, 头像URL)`。取不到时返回 `("", "")`，**不抛异常**。

        订阅时顺手存下来，供订阅列表显示（§2.5）。

        ⚠️ 用的是 `user_novels()` 响应里的 `user` 字段 —— 实测该字段确实存在
        （tests/fixtures/user_novels.json 里录了真实结构）。
        """
        try:
            result = await asyncio.to_thread(
                lambda: _retry_sync(lambda: self._get_api().user_novels(author_id))
            )
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
        """作品详情 → **已解包的**作品本体（`x_restrict` / `image_urls` / `tags` / `title`）。

        ⚠️ 解包是必须的：接口顶层是 `{"novel": {...}}`，字段在下一层。
        不解包会让 `detail.x_restrict` 恒为 None（→ R18 判定失效）、
        `detail.image_urls` 恒为 None（→ 封面拿不到）。详见 `unwrap_novel_shape`。
        """

        def _call() -> Any:
            novel = unwrap_novel_shape(_retry_sync(lambda: self._get_api().novel_detail(novel_id)))
            _check_novel_shape(novel)
            return novel

        return await asyncio.to_thread(_call)

    async def novel_text(self, novel_id: int) -> str:
        """作品全文 → `text` 字段。

        ⚠️ 这个接口返回的对象**没有 `x_restrict`**（实测确认），
        所以 R18 判定必须来自 `novel_detail`，不能图省事在这里判。
        """

        def _call() -> str:
            nv = _retry_sync(lambda: self._get_api().novel_text(novel_id))
            return getattr(nv, "text", "") or ""

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

    async def novel_series(self, series_id: int) -> Any:
        """小说系列 → 网页接口的 `body`（**含系列级 `xRestrict`**）。

        ⚠️ 走的是网页版 `/ajax/novel/series/N` 而不是 App 的 `/v2/novel/series`：
        App 接口的系列详情里**根本没有 R18 字段**（实测，见模块顶部注释），
        而网页接口的 `body.xRestrict` 就是系列级 R18。

        用 httpx 而不是 pixivpy3：这个接口是给网页前端用的 JSON，不在 pixivpy3
        的封装里。它**不需要登录 cookie**（实测无 cookie 也能拿到 R18 系列的数据）。
        """
        headers = {"Referer": COVER_REFERER, "User-Agent": _UA, "Accept": "application/json"}
        kwargs: dict[str, Any] = {}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        url = SERIES_WEB_API.format(series_id=series_id)
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers=headers, **kwargs
        ) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            payload = resp.json()
        # 网页接口统一是 {"error": bool, "message": str, "body": {...}}
        if isinstance(payload, dict) and payload.get("error"):
            raise ValueError(f"pixiv 网页接口返回错误：{payload.get('message')}")
        body = payload.get("body") if isinstance(payload, dict) else None
        if not isinstance(body, dict):
            keys = sorted(payload)[:9] if isinstance(payload, dict) else type(payload).__name__
            raise ValueError(f"novel_series 响应缺少 body（顶层键={keys}）")
        _check_series_shape(body)
        return body

    async def download_cover(
        self, url: str, *, blur: bool, radius: int, max_width: int = 0
    ) -> bytes:
        """下载封面 → 按需缩放到 `max_width` → 按需高斯模糊（`radius` 像素）。

        封面**不可用**时返回**空字节**（而不是抛异常），调用方按「没封面」处理：
          · URL 是 pixiv 占位图（作品不可见/受限，App 接口不抛异常只换 URL）
          · 下到的图尺寸过小（字节层面兜底）
        这两种情况打 warning，不静默。
        """
        if not url:
            return b""
        if is_placeholder_url(url):
            logger.warning(f"封面是 pixiv 占位图（作品不可见/受限），本次不带图：{url}")
            return b""
        data = await self.fetch_image(url)
        if is_placeholder_image(data):
            logger.warning(f"封面下到占位图 {image_size(data)}（作品不可见/受限），本次不带图：{url}")
            return b""
        return await asyncio.to_thread(
            process_cover, data, blur=blur, radius=radius, max_width=max_width
        )
