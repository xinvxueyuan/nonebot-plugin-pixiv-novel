"""把 pixiv 作品拼成 OneBot V11 混合消息（文本 + 封面图）。"""

from __future__ import annotations

from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment

_R18_LABEL = {1: "R-18", 2: "R-18G"}


def novel_url(novel_id: int | str) -> str:
    return f"https://www.pixiv.net/novel/show.php?id={novel_id}"


def user_url(user_id: int | str) -> str:
    return f"https://www.pixiv.net/users/{user_id}"


def _tag_names(novel: Any) -> list[str]:
    """兼容 NovelInfo.tags（NovelTag 对象）与 WebviewNovel.tags（纯 str）。"""
    out: list[str] = []
    for t in getattr(novel, "tags", None) or []:
        name = t if isinstance(t, str) else getattr(t, "name", None)
        if name:
            out.append(str(name))
    return out


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

    x_restrict = int(getattr(novel, "x_restrict", 0) or 0)
    if x_restrict in _R18_LABEL:
        label = _R18_LABEL[x_restrict]
        suffix = "（封面已模糊）" if blurred else "（封面未模糊）"
        lines.append(f"🔞 {label}{suffix}")

    lines.append(f"🔗 {novel_url(novel.id)}")

    msg = Message(MessageSegment.text("\n".join(lines)))
    if cover:
        msg += MessageSegment.image(cover)
    return msg
