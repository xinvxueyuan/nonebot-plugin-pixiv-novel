"""命令的业务逻辑。与 NoneBot 解耦，方便单测。"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from . import store
from .message import novel_url, user_url

logger = logging.getLogger("nonebot_plugin_pixiv_novel")

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


async def deliver_novel_text(
    *,
    text: str,
    title: str,
    novel_id: int,
    filename: str,
    max_chars: int,
    send_text: Callable[[str], Awaitable[Any]],
    send_file: Callable[[str, str], Awaitable[bool]],
) -> str:
    """把正文交付给用户，三级降级。返回实际走的分支名。

    分支：
      · `inline`              长度 ≤ max_chars，内联发成功
      · `file`                长度超限，发 txt 文件成功
      · `file_after_inline`   内联发失败（多半是超平台单条上限），改发文件成功
      · `link`                文件和/或内联都失败 → 至少把链接给出去
      · `empty`               正文为空（理论上调用方已拦）

    ⚠️⚠️ **为什么这里不在 `finish()` 外面套 `try/except Exception`**：
    NoneBot 的 `Matcher.finish()` 正常结束时会抛 `FinishedException`，
    而它是 **`Exception` 的子类**（MRO: FinishedException → MatcherException →
    NoneBotException → **Exception**）。所以

        try:
            await matcher.finish(msg)      # 发送成功
        except Exception:                  # ← 会把「成功」也抓进来
            ...降级发文件...

    会让**每次内联发送成功后又多发一个文件**。

    这里用 `send_text`（只发消息，**不带控制流**）而不是 `finish`，
    结构上就不会踩到这个坑；`send_text` 抛出的才是真的发送失败
    （平台拒收 / 超长 / 限流）。
    """
    if not text.strip():
        return "empty"

    body = f"📖 {title}\n{novel_url(novel_id)}\n\n{text}"

    if len(text) <= max_chars:
        try:
            await send_text(body)
            return "inline"
        except Exception as e:
            logger.warning(
                f"内联发送正文失败（{len(text)} 字），改发文件: {type(e).__name__}: {e}"
            )
            try:
                if await send_file(filename, text):
                    await send_text(
                        f"📖 {title}\n正文内联发送失败（{len(text)} 字），已作为 txt 文件发送"
                    )
                    return "file_after_inline"
            except Exception as e2:
                logger.warning(f"降级发文件也失败: {type(e2).__name__}: {e2}")
            await send_text(f"📖 {title}\n正文暂时发不出来，请点链接阅读：{novel_url(novel_id)}")
            return "link"

    # 超过阈值 → 直接发 txt 文件
    try:
        if await send_file(filename, text):
            await send_text(f"📖 {title}\n正文过长（{len(text)} 字），已作为 txt 文件发送")
            return "file"
    except Exception as e:
        logger.warning(f"发送 txt 文件失败: {type(e).__name__}: {e}")

    await send_text(
        f"📖 {title}\n正文过长（{len(text)} 字），上传文件失败，请点链接阅读：{novel_url(novel_id)}"
    )
    return "link"


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
