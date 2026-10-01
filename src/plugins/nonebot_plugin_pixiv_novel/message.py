"""把 pixiv 作品拼成 OneBot V11 混合消息（文本 + 封面图）。"""

from __future__ import annotations

from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment

_R18_LABEL = {1: "R-18", 2: "R-18G"}


def novel_url(novel_id: int | str) -> str:
    return f"https://www.pixiv.net/novel/show.php?id={novel_id}"


def series_url(series_id: Any) -> str:
    return f"https://www.pixiv.net/novel/series/{series_id}"


def user_url(user_id: int | str) -> str:
    return f"https://www.pixiv.net/users/{user_id}"


def _scale(total_chars: int) -> str:
    """总字数 → 「约 4.2 万字」。空/未知返回空串（不写「约 0 字」这种噪音）。"""
    if total_chars <= 0:
        return ""
    if total_chars >= 10000:
        return f"约 {total_chars / 10000:.1f} 万字"
    return f"约 {total_chars} 字"


def _tag_names(novel: Any) -> list[str]:
    """兼容 NovelInfo.tags（NovelTag 对象）与 WebviewNovel.tags（纯 str）。"""
    out: list[str] = []
    for t in getattr(novel, "tags", None) or []:
        name = t if isinstance(t, str) else getattr(t, "name", None)
        if name:
            out.append(str(name))
    return out


def _plain_tags(tags: Any) -> list[str]:
    """网页接口的 `tags` 是**纯字符串列表**（App 接口那边是 NovelTag 对象）。

    两种都兼容：字符串直接用，对象取 `.name`。
    """
    out: list[str] = []
    for t in tags or []:
        name = t if isinstance(t, str) else getattr(t, "name", None)
        if name:
            out.append(str(name))
    return out


def _r18_line(x_restrict: Any, blurred: bool) -> str | None:
    """R18 标记行；非 R18 返回 None。卡片与推送共用同一套文案。"""
    xr = int(x_restrict or 0)
    if xr not in _R18_LABEL:
        return None
    suffix = "（封面已模糊）" if blurred else "（封面未模糊）"
    return f"🔞 {_R18_LABEL[xr]}{suffix}"


def build_push(novel: Any, cover: bytes | None, *, blurred: bool) -> Message:
    """组装推送消息。

    Args:
        novel: models.NovelInfo
        cover: 封面图片字节（None/空 = 不附图）
        blurred: 封面是否已做过模糊（仅影响文案提示）
    """
    lines = [f"📖 {novel.title}"]
    lines.append(f"✍️ {novel.user.name} · {user_url(novel.user.id)}")

    tags = _tag_names(novel)
    if tags:
        lines.append("🏷 " + " ".join(tags))

    r18 = _r18_line(getattr(novel, "x_restrict", 0), blurred)
    if r18:
        lines.append(r18)

    lines.append(f"🔗 {novel_url(novel.id)}")

    msg = Message(MessageSegment.text("\n".join(lines)))
    if cover:
        msg += MessageSegment.image(cover)
    return msg


def build_series_push(series: Any, cover: bytes | None, *, blurred: bool) -> Message:
    """组装**系列**卡片（被动 hook 用）。

    Args:
        series: pixiv **网页接口** `/ajax/novel/series/N` 返回的 `body`
                （见 pixiv_client.novel_series 的注释：App 接口**没有**系列级 R18 字段）
        cover: 系列封面字节（None/空 = 不附图）
        blurred: 封面是否已模糊（仅影响文案）

    R18 判定用的是**系列自己的** `xRestrict`（实测：R18 系列=1、非 R18 系列=0，
    非 R18 时是显式的 0 而非缺键）。不要改成「查首篇的 x_restrict」——
    那对「首篇非 R18、但系列里含 R18」的混合系列会判错，而且白白多一次请求。
    """
    get = series.get if hasattr(series, "get") else (lambda k, d=None: d)

    title = get("title") or f"系列 {get('id')}"

    count = get("displaySeriesContentCount") or get("publishedContentCount") or get("total") or 0
    meta: list[str] = []
    if count:
        meta.append(f"共 {int(count)} 话")
    chars = _scale(int(get("publishedTotalCharacterCount") or 0))
    if chars:
        meta.append(chars)
    meta.append("已完结" if get("isConcluded") else "连载中")

    lines = [f"📚 {title}", "📊 " + " · ".join(meta)]

    author_name = get("userName") or ""
    author_id = get("userId") or ""
    if author_name and author_id:
        lines.append(f"✍️ {author_name} · {user_url(author_id)}")

    tags = _plain_tags(get("tags"))
    if tags:
        lines.append("🏷 " + " ".join(tags))

    r18 = _r18_line(get("xRestrict"), blurred)
    if r18:
        lines.append(r18)

    lines.append(f"🔗 {series_url(get('id'))}")

    msg = Message(MessageSegment.text("\n".join(lines)))
    if cover:
        msg += MessageSegment.image(cover)
    return msg

