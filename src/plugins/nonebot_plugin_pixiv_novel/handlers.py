"""命令的业务逻辑。与 NoneBot 解耦，方便单测。"""

from __future__ import annotations

import re
from typing import Any

from . import store
from .message import user_url

_ID_RE = re.compile(r"(\d{3,})")


def extract_id(text: str | None) -> int | None:
    """从参数里取出 ID：支持纯数字，也支持直接粘 pixiv 链接。"""
    if not text:
        return None
    m = _ID_RE.search(text)
    return int(m.group(1)) if m else None


def _author_label(row_or_id: Any, name: str = "") -> str:
    """作者显示名：显式传入的名字 → 行里的 `author_name` → 退回 ID。

    ⚠️ 行是 `sqlite3.Row`，**不是 dict** —— 只判 `isinstance(..., dict)` 会让它掉到
    `str(row)`，回复里就出现 `<sqlite3.Row object at 0x...>`。用 `keys()` 判「像不像行」。
    """
    explicit = (name or "").strip()
    if explicit:
        return explicit

    if hasattr(row_or_id, "keys"):
        row_name = str(row_or_id["author_name"] or "").strip()
        return row_name or str(row_or_id["author_id"])

    return str(row_or_id)


def reply_subscribe(
    group_id: int,
    author_id: int,
    baseline: int,
    author_name: str = "",
    author_avatar_url: str = "",
) -> str:
    """订阅。`author_name` / `author_avatar_url` **允许为空**（API 失败时不阻塞订阅）。

    名字和头像会被存进表里，供订阅列表显示（§2.5）。
    """
    created = store.subscribe(
        group_id,
        author_id,
        baseline=baseline,
        author_name=author_name,
        author_avatar_url=author_avatar_url,
    )
    label = _author_label(author_id, author_name)
    if not created:
        return f"本群已经订阅过作者 {label} 了\n{user_url(author_id)}"
    return (
        f"✅ 已订阅作者 {label}\n{user_url(author_id)}\n"
        f"从现在开始推送新作（订阅前的历史作品不推送）"
    )


def reply_unsubscribe(group_id: int, author_id: int) -> str:
    if store.unsubscribe(group_id, author_id):
        return f"✅ 已退订作者 {author_id}"
    return f"本群没有订阅作者 {author_id}"


def reply_list(group_id: int) -> str:
    """订阅列表的**纯文本**版本。

    这是 Task 7.5 图片渲染失败时的回退，所以必须始终可用（不依赖 htmlkit / 网络）。
    """
    rows = store.list_by_group(group_id)
    if not rows:
        return "本群还没有订阅任何作者"
    lines = ["本群订阅的作者："]
    lines += [
        f"{i + 1}. {_author_label(r)}  {user_url(r['author_id'])}"
        for i, r in enumerate(rows)
    ]
    return "\n".join(lines)
