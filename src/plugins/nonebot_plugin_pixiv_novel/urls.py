"""从消息文本里认出 pixiv **小说**链接（被动 hook 用）。

只做纯文本解析，不碰网络、不碰 NoneBot —— 这样才可能把 URL 的各种变体
（语言前缀 / 多参数 / 锚点 / 短链）穷举着测一遍。

⚠️ 这里**刻意不用** `handlers.extract_id()`。那个函数取「第一个 3 位以上的数字」，
对 `pixiv.net/novel/series/16486288` 会返回 `16486288` 并当成**单篇作品 ID** 去查
—— 变成「拿系列 ID 去查一个不存在的作品」，静默失败。所以系列必须在这里就分流。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

# 链接种类：单篇作品 / 系列
Kind = Literal["novel", "series"]

# pixiv 会给链接加语言前缀，如 /en/novel/... 或 /zh-tw/novel/...
_LANG = r"(?:[a-z]{2}(?:-[a-zA-Z]{2,4})?/)?"

_NOVEL_RE = re.compile(
    rf"pixiv\.net/{_LANG}novel/show\.php\?(?:[^\s#]*?&)?id=(\d+)",
    re.IGNORECASE,
)
_SERIES_RE = re.compile(
    rf"pixiv\.net/{_LANG}novel/series/(\d+)",
    re.IGNORECASE,
)

_PATTERNS: tuple[tuple[Kind, re.Pattern[str]], ...] = (
    ("novel", _NOVEL_RE),
    ("series", _SERIES_RE),
)


def parse(text: str) -> tuple[Kind, int] | None:
    """返回消息里**最靠前**的那个 pixiv 小说链接 `(kind, id)`；没有则 `None`。

    为什么只取一个：一条消息里贴三四个链接时，连发三四张卡片会刷屏。
    取「最靠前」而不是「先匹配 novel 再看 series」—— 后者在
    「先说系列再说单篇」的消息里会答错顺序。

    为什么带 `[^\\s#]*?&`：`show.php?foo=1&id=900` 的 `id` 不在第一个参数位。
    `#` 排除掉锚点（`?id=900#1` 是第几页）。
    """
    if not text:
        return None
    best: tuple[int, Kind, int] | None = None
    for kind, rx in _PATTERNS:
        m = rx.search(text)
        if m is not None:
            hit = (m.start(), kind, int(m.group(1)))
            if best is None or hit[0] < best[0]:
                best = hit
    if best is None:
        return None
    return best[1], best[2]


def candidates(message: Any) -> str:
    """把**整条消息**拼成一段可搜文本，用于认链接。

    为什么不只用 `event.get_plaintext()`：QQ 会把用户粘贴的链接转成
    **分享卡片**（一段 `json` 消息段），这时纯文本里**一个字都没有**，
    链接只存在于 json 段的数据里 —— 只用纯文本会漏掉这大半数情况。

    非 text 段用 `json.dumps` 序列化：Python 默认**不转义 `/`**，
    所以 URL 保持原样可匹配（CQ 码那种 `\\/` 转义形式反而会失配）。

    接受任何「可迭代 + 每项有 .type/.data」的对象（NoneBot 的 Message 就是），
    也接受纯字符串，便于单测。
    """
    if isinstance(message, str):
        return message
    parts: list[str] = []
    for seg in message or []:
        seg_type = getattr(seg, "type", "")
        data = getattr(seg, "data", None) or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        else:
            try:
                parts.append(json.dumps(data, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                continue
    return "\n".join(parts)


def parse_all(text: str) -> list[tuple[Kind, int]]:
    """消息里**全部**链接，按出现顺序、已去重。给日志与「只回第一条」的提示用。"""
    if not text:
        return []
    hits: list[tuple[int, Kind, int]] = []
    for kind, rx in _PATTERNS:
        hits.extend((m.start(), kind, int(m.group(1))) for m in rx.finditer(text))
    hits.sort()

    out: list[tuple[Kind, int]] = []
    for _, kind, target_id in hits:
        if (kind, target_id) not in out:
            out.append((kind, target_id))
    return out
