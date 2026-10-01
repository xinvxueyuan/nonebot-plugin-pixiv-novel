"""轮询订阅作者的新作并推送。

核心是 last_seen 高水位：
  - last_seen == 0  → 首次轮询，只播种（把高水位设成当前最新 ID），不推送
  - last_seen >  0  → 推 ID 比高水位大的作品，按 ID 升序（从旧到新），受 max_push 限制
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from nonebot.adapters.onebot.v11 import Message

from . import store
from .config import Config
from .message import build_push
from .pixiv_client import PixivClient

logger = logging.getLogger("nonebot_plugin_pixiv_novel")

SendText = Callable[[int, Message], Awaitable[None]]


def _is_pushable(novel) -> bool:
    """过滤不可见 / 仅 mypixiv 可见的作品。"""
    return bool(getattr(novel, "visible", True)) and not bool(
        getattr(novel, "is_mypixiv_only", False)
    )


async def poll_once(
    client: PixivClient,
    config: Config,
    *,
    send: SendText,
    send_file: Callable[[int, str, str], Awaitable[None]] | None = None,
) -> int:
    """跑一轮。返回本轮推送条数。"""
    pushed = 0

    for author_id in store.all_authors():
        try:
            novels = await client.user_novels(author_id)
        except Exception as e:
            # 单个作者失败不能拖垮整轮
            logger.warning(f"拉取作者 {author_id} 作品列表失败: {type(e).__name__}: {e}")
            continue

        if not novels:
            continue

        newest_id = max(int(n.id) for n in novels)

        for sub in store.list_by_author(author_id):
            group_id = sub["group_id"]
            last_seen = sub["last_seen"]

            # 首次：只播种高水位，不推送历史作品
            if last_seen == 0:
                store.set_last_seen(group_id, author_id, newest_id)
                continue

            fresh = sorted(
                (n for n in novels if int(n.id) > last_seen),
                key=lambda n: int(n.id),
            )
            if not fresh:
                continue

            # 高水位推进策略（**别改**）：
            #   按 ID 升序逐篇处理；
            #   - 不可见 / 仅 mypixiv / 被 R18 开关挡掉的 → **不推，但高水位要跨过去**，
            #     否则它们会被永远重新扫到，还会挡住后面的新作；
            #   - 推出去的 → 记进 landed；
            #   - landed 攒够 max_push 就 break，剩下的**不动高水位**，留给下一轮。
            # 循环结束后高水位 = max(landed)，即「最后一条真正推出去的」。
            landed: list[int] = []
            for novel in fresh:
                novel_id = int(novel.id)

                if not _is_pushable(novel):
                    store.set_last_seen(group_id, author_id, novel_id)
                    continue

                if len(landed) >= config.pixiv_max_push_per_poll:
                    break

                try:
                    detail = await client.novel_detail(novel_id)
                except Exception as e:
                    logger.warning(f"取作品 {novel_id} 详情失败: {type(e).__name__}: {e}")
                    store.set_last_seen(group_id, author_id, novel_id)
                    continue

                x_restrict = int(getattr(detail, "x_restrict", 0) or 0)

                # R18 推送开关（独立于「R18 封面模糊」和「R18 正文全文」）
                # 关闭时：整条不推，也不浪费一次封面下载
                if x_restrict > 0 and not config.pixiv_r18_push_enabled:
                    store.set_last_seen(group_id, author_id, novel_id)
                    continue

                cover: bytes | None = None
                blurred = False
                cover_url = getattr(getattr(detail, "image_urls", None), "large", "") or ""
                if cover_url:
                    should_blur = bool(config.pixiv_blur_r18 and x_restrict > 0)
                    try:
                        cover = await client.download_cover(
                            cover_url,
                            blur=should_blur,
                            radius=config.pixiv_blur_radius,
                        )
                        blurred = should_blur
                    except Exception as e:
                        logger.warning(f"下载封面 {cover_url} 失败: {type(e).__name__}: {e}")

                msg = build_push(detail, cover, blurred=blurred)
                try:
                    await send(group_id, msg)
                    pushed += 1
                except Exception as e:
                    logger.warning(f"推送到群 {group_id} 失败: {type(e).__name__}: {e}")

                # 即使发送失败也记进 landed —— 避免下一轮反复重推同一条刷屏
                landed.append(novel_id)

            if landed:
                store.set_last_seen(group_id, author_id, max(landed))

    return pushed
