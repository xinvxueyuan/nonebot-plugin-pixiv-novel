"""命令的业务逻辑。与 NoneBot 解耦，方便单测。"""

from __future__ import annotations

import re
from typing import Any

from . import store
from .message import user_url

_ID_RE = re.compile(r"(\d{3,})")

# 文件名禁用字符：路径分隔符（防目录穿越）、Windows 非法字符、控制字符（含换行）。
# 换行尤其要防 —— 它会原样进文件路径，让 `upload_group_file` 行为异常。
_FILENAME_BAD_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def extract_id(text: str | None) -> int | None:
    """从参数里取出 ID：支持纯数字，也支持直接粘 pixiv 链接。"""
    if not text:
        return None
    m = _ID_RE.search(text)
    return int(m.group(1)) if m else None


def safe_filename(novel_id: int, title: str, limit: int = 20) -> str:
    """把作品标题清洗成安全的 txt 文件名。

    标题是**用户可自定**的内容，直接拼进路径有两个风险：
      - 含 `/`、`\\` → 路径分隔符，能跳出缓存目录
      - 含换行/控制字符 → 文件路径里带换行，上传 API 行为异常

    清洗后为空（标题全是非法字符）时退回 `untitled`，保证文件名始终可用。
    """
    cleaned = _FILENAME_BAD_RE.sub("_", title[:limit]).strip(" ._")
    return f"{novel_id}_{cleaned or 'untitled'}.txt"


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
    author_fetch_failed: bool = False,
) -> str:
    """订阅。`author_name` / `author_avatar_url` **允许为空**（API 失败时不阻塞订阅）。

    名字和头像会被存进表里，供订阅列表显示（§2.5）。

    `author_fetch_failed=True`（有 token 但拉取作者信息失败）时**必须**在回复里警告 ——
    否则用户把作者 ID 打错也会看到「✅ 已订阅」，然后永远收不到任何推送、还找不到原因。
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

    warn = ""
    if author_fetch_failed:
        warn = (
            f"\n⚠️ 但**没能从 pixiv 拉到该作者的信息** —— 作者 ID 可能有误（或网络/代理异常）。\n"
            f"订阅已记录，但如果是无效 ID，之后不会有任何推送。建议发 `退订 {author_id}` 后重新确认 ID。"
        )
    return (
        f"✅ 已订阅作者 {label}\n{user_url(author_id)}\n"
        f"从现在开始推送新作（订阅前的历史作品不推送）{warn}"
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
