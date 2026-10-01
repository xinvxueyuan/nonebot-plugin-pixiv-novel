"""pixiv 小说订阅推送插件。

命令（裸词触发，qbot 的 COMMAND_START=[""]）：

    订阅 <作者id>        群聊。本群订阅该作者的新作
    退订 <作者id>        群聊
    订阅列表             群聊。htmlkit 渲染成图片
    获取全文 <作品id>    群聊/私聊，由 PIXIV_TEXT_TARGETS 决定

**所有命令默认仅管理员可用**（PIXIV_ADMIN_ONLY=true）。
订阅类命令是「按群」的，所以只在群聊受理；「获取全文」群聊私聊都受理（受配置约束）。

三条硬约束（用户明确要求）：
  - **不引用**：回复一律用 `finish()` 直接发，**不使用引用回复**（不传 reply 消息段）。
  - **不共享**：订阅数据按 `(group_id, author_id)` 存，**各群之间不共享**。
  - **只发 release**：本项目只通过 GitHub Release 分发（见 Task 9.8），不发布到 PyPI。
"""

from __future__ import annotations

import logging
import time

from nonebot import get_bot, get_driver, get_plugin_config, on_command, on_message, require
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.log import LoguruHandler
from nonebot.params import CommandArg

logger = logging.getLogger("nonebot_plugin_pixiv_novel")

# NoneBot 把 stdlib `logging` 的 root logger 钉在 WARNING，且 root **没有任何 handler**
# （实测 `root level: 30` / `root handlers: []`），所以本插件的 `logger.info(...)`
# **一条都不会出现在 journal 里** —— 只有 warning 靠 logging 的 lastResort 落到 stderr。
# 后果是启动行「已启动：代理=… 间隔=…」和推送通知全都看不见，出事时无从判断。
#
# 修法：挂上 NoneBot 自带的 `LoguruHandler`（stdlib logging → loguru 的官方桥），
# 并把本 logger 的级别降到 INFO。6 个模块用的都是**同一个** logger 名字，
# 所以这两行一次覆盖全部。
#   · 加了 handler 之后 warning 不会重复（有 handler 就不会走 lastResort）
#   · debug 仍按 INFO 级别过滤掉
# 实测见 scripts/verify_log_visibility.py。
if not any(isinstance(h, LoguruHandler) for h in logger.handlers):
    logger.addHandler(LoguruHandler())
logger.setLevel(logging.INFO)

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_localstore")

# ⚠️ htmlkit 是**可选**依赖，必须用 try 包住。
#
# `require()` 内部会 `load_plugin()` 并把异常**原样抛出**，所以一旦 htmlkit 在某台机器上
# 加载不了（C++ 扩展缺 .so / glibc 太老 / 没装），这一行会让**整个 pixiv 插件**加载失败 ——
# 4 个命令全部消失，连「不依赖 htmlkit 的」订阅/退订/获取全文 都跟着没法用。
# 实测确认过这个失败模式（scripts/verify_htmlkit_resilience.py）。
#
# 正确行为：htmlkit 不可用 → 插件照常加载 → 只有「订阅列表」降级成纯文本。
# render.py 里的 `_load_htmlkit()` 是惰性 import，`render_subscription_list()`
# 会捕获异常返回 None，由调用方回退 `handlers.reply_list()`。
HTMLKIT_AVAILABLE = True
try:
    require("nonebot_plugin_htmlkit")   # htmlkit 要求先 require 再 import
except Exception as e:
    HTMLKIT_AVAILABLE = False
    logger.warning(
        f"nonebot_plugin_htmlkit 加载失败，订阅列表将回退为纯文本列表："
        f"{type(e).__name__}: {e}"
    )

from nonebot_plugin_apscheduler import scheduler

from . import avatars, handlers, message, policy, render, store, urls
from .config import Config
from .message import novel_url
from .pixiv_client import PixivClient, original_cover_url
from .poller import poll_once

plugin_config = get_plugin_config(Config)

DENIED_MSG = "此指令仅管理员可用"

# ── 全局客户端（refresh_token / 代理来自配置）──────────────────────
client = PixivClient(
    plugin_config.pixiv_refresh_token,
    plugin_config.pixiv_proxy,
)

driver = get_driver()


def _is_admin(event: MessageEvent) -> bool:
    """管理员闸门：这个用户**能不能用**命令。逻辑在 policy.is_admin（纯函数，已单测）。

    这里**不**用 `permission=` 参数，而是放在 handler 内部：
    因为 NoneBot 的 permission 失败是**静默忽略**，用户会以为机器人坏了。
    放内部就能明确回一句「此指令仅管理员可用」。
    """
    return policy.is_admin(
        event.get_user_id(),
        admin_only=plugin_config.pixiv_admin_only,
        admin_ids=plugin_config.pixiv_admin_ids,
        superusers=list(get_driver().config.superusers),
    )


def _is_admin_identity(event: MessageEvent) -> bool:
    """**纯身份**：这个人是不是管理员本人（不看 admin_only 开关）。

    专供「绕过全文两轴」用。**别拿 `_is_admin()` 代替** ——
    `pixiv_admin_only=False`（现在的默认值）时它对所有人返回 True，
    于是每个普通群友都会被当成管理员，两轴就白设了。
    详见 policy.is_admin_identity 的 docstring。
    """
    return policy.is_admin_identity(
        event.get_user_id(),
        admin_ids=plugin_config.pixiv_admin_ids,
        superusers=list(get_driver().config.superusers),
    )


async def _send(group_id: int, message: Message) -> None:
    """主动推送到群（走 OneBot V11 的 send_group_msg）。"""
    await get_bot().send_group_msg(group_id=group_id, message=message)


async def _send_file(event: MessageEvent, filename: str, content: str) -> bool:
    """把长正文作为 txt 文件发出。**群聊/私聊走不同 API**，返回是否成功。

    失败**不抛异常**：调用方会改成回一句带链接的提示，避免命令看起来「没反应」。
    """
    import nonebot_plugin_localstore as localstore

    path = localstore.get_plugin_cache_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    bot = get_bot()
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api(
                "upload_group_file",
                group_id=event.group_id,
                file=str(path),
                name=filename,
            )
        else:
            await bot.call_api(
                "upload_private_file",
                user_id=event.user_id,
                file=str(path),
                name=filename,
            )
        return True
    except Exception as e:
        logger.warning(f"上传文件失败: {type(e).__name__}: {e}")
        return False


async def _poll_job() -> None:
    if not plugin_config.pixiv_refresh_token:
        logger.warning("未配置 PIXIV_REFRESH_TOKEN，跳过轮询")
        return
    try:
        n = await poll_once(client, plugin_config, send=_send, send_file=None)
        if n:
            logger.info(f"本轮推送 {n} 条新作")
    except Exception as e:
        logger.exception(f"轮询异常: {type(e).__name__}: {e}")


@driver.on_startup
async def _startup() -> None:
    store.init(store.default_db_path())
    # 头像缓存放 localstore 的 cache 目录（重启不丢，不用重新下载）
    import nonebot_plugin_localstore as localstore

    avatars.init(localstore.get_plugin_cache_dir() / "avatars")
    logger.info(
        f"pixiv-novel 已启动：代理={plugin_config.pixiv_proxy or '直连'} "
        f"间隔={plugin_config.pixiv_poll_interval}s 订阅作者数={len(store.all_authors())} "
        f"仅管理员={plugin_config.pixiv_admin_only} "
        f"全文本可投递渠道={plugin_config.pixiv_text_targets} "
        f"R18全文允许发群={plugin_config.pixiv_r18_text_allow_group} "
        f"R18推送={plugin_config.pixiv_r18_push_enabled} "
        f"R18封面模糊={plugin_config.pixiv_blur_r18}(radius={plugin_config.pixiv_blur_radius}) "
        f"封面={plugin_config.pixiv_cover_max_width or '原图'} "
        f"URL被动卡片={plugin_config.pixiv_url_hook_enabled}"
        f"(去重={plugin_config.pixiv_url_hook_cooldown}s)"
    )
    if not plugin_config.pixiv_text_targets:
        logger.warning("PIXIV_TEXT_TARGETS 为空列表：「获取全文」将对任何人都拒绝")
    if plugin_config.pixiv_admin_only and not plugin_config.pixiv_admin_ids:
        logger.info("PIXIV_ADMIN_IDS 未配置：管理员回退为 SUPERUSERS=%s", list(driver.config.superusers))
    if plugin_config.pixiv_refresh_token:
        scheduler.add_job(
            _poll_job,
            "interval",
            seconds=plugin_config.pixiv_poll_interval,
            id="pixiv_novel_poll",
            replace_existing=True,
        )
    else:
        logger.warning("PIXIV_REFRESH_TOKEN 为空：只注册命令，不轮询")


# ── 订阅（群聊限定：订阅是「按群」的）────────────────────────────
subscribe_cmd = on_command("订阅", priority=10, block=True)


@subscribe_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    if not _is_admin(event):
        await subscribe_cmd.finish(DENIED_MSG)

    # `finish()` 声明为 NoReturn（会抛 FinishedException），但 Pyright 不认
    # `await` 形式的 NoReturn 收窄，所以补一个显式 return 让控制流和类型都明确。
    author_id = handlers.extract_id(args.extract_plain_text())
    if author_id is None:
        await subscribe_cmd.finish("用法：订阅 <作者id>（也可直接粘作者主页链接）")
        return

    # 播种：取作者当前最新作品 ID，避免把历史作品一次性推出来
    baseline = 0
    author_name = ""
    author_avatar_url = ""
    author_fetch_failed = False
    if plugin_config.pixiv_refresh_token:
        try:
            novels = await client.user_novels(author_id)
            if novels:
                baseline = max(int(n.id) for n in novels)

            # 顺手把作者名/头像存下来，供订阅列表显示（§2.5）。
            # author_info 内部已容错：取不到就返回 ("", "")，不影响订阅本身。
            author_name, author_avatar_url = await client.author_info(author_id)
        except Exception as e:
            # 拉不到 = 作者 ID 很可能不存在（或代理挂了）。
            # 必须把这个事实带回回复里，否则用户看到「✅ 已订阅」却永远收不到推送。
            author_fetch_failed = True
            logger.warning(f"订阅时取作者 {author_id} 作品列表失败: {type(e).__name__}: {e}")

    await subscribe_cmd.finish(
        handlers.reply_subscribe(
            event.group_id,
            author_id,
            baseline,
            author_name=author_name,
            author_avatar_url=author_avatar_url,
            author_fetch_failed=author_fetch_failed,
        )
    )


# ── 退订（群聊限定）─────────────────────────────────────────────
unsubscribe_cmd = on_command("退订", priority=10, block=True)


@unsubscribe_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    if not _is_admin(event):
        await unsubscribe_cmd.finish(DENIED_MSG)

    author_id = handlers.extract_id(args.extract_plain_text())
    if author_id is None:
        await unsubscribe_cmd.finish("用法：退订 <作者id>")
        return
    await unsubscribe_cmd.finish(handlers.reply_unsubscribe(event.group_id, author_id))


# ── 订阅列表（群聊限定；htmlkit 渲染成图片，失败回退纯文本）──────────
list_cmd = on_command("订阅列表", priority=10, block=True)


@list_cmd.handle()
async def _(event: GroupMessageEvent):
    if not _is_admin(event):
        await list_cmd.finish(DENIED_MSG)

    rows = store.list_by_group(event.group_id)
    if not rows:
        await list_cmd.finish(handlers.reply_list(event.group_id))   # 「还没有订阅」
        return

    # 先把头像下成 data URI（带 Referer + 磁盘缓存）。
    # 失败的头像不会进映射 → 模板显示首字占位块，**不影响整张图渲染**。
    try:
        avatar_map = await avatars.for_rows(rows, fetch=client.fetch_image)
    except Exception as e:
        logger.warning(f"批量取头像失败（改为无头像渲染）: {type(e).__name__}: {e}")
        avatar_map = {}

    img = await render.render_subscription_list(
        rows, group_id=event.group_id, avatars=avatar_map
    )
    if img is None:
        # 渲染不可用/失败 → 回退纯文本（render.py 里已记 warning）
        await list_cmd.finish(handlers.reply_list(event.group_id))
        return
    await list_cmd.finish(MessageSegment.image(img))


# ── 被动 URL hook：消息里出现 pixiv 小说链接就回卡片 ──────────────
#
# 几个刻意的选择（都不是随手写的）：
#
# · `priority=20` + 命令是 `priority=10, block=True` → 命令先匹配并**阻断**传播，
#   所以 `获取全文 <链接>` 只会走命令那一条路，**不会**再被这里回一次卡片。
#   不靠「文本里排除命令关键字」这种脆弱判断（COMMAND_START=[""] 时尤其容易漏）。
# · `block=False`：本 hook 是**旁观者**，绝不吞掉别人的消息 —— 别的插件
#   （如合并转发、词库）该收到还得收到。
# · 机器人自己的消息直接跳过，否则卡片里的 pixiv 链接会让它**自我触发**（无限循环）。
# · 不去查数据库、不看订阅关系：这个功能是「谁贴链接就答谁」，与订阅无关。
url_hook = on_message(priority=20, block=False)

# 去重窗口：键 = (会话, 种类, 目标ID) → 上次回复的 monotonic 时间
_seen: dict[tuple[str, str, int], float] = {}


def _session_key(event: MessageEvent) -> str:
    """会话标识：**按会话隔离**去重（群与群之间、群与私聊之间互不影响）。"""
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    return f"private:{event.user_id}"


def _should_skip(key: tuple[str, str, int]) -> bool:
    """同一会话里刚回过的同一个作品就跳过。返回 True 表示应跳过。"""
    window = plugin_config.pixiv_url_hook_cooldown
    if window <= 0:
        return False
    now = time.monotonic()
    # 顺手清理过期项，避免长期运行下字典无限增长
    for k in [k for k, t in _seen.items() if now - t >= window]:
        _seen.pop(k, None)
    last = _seen.get(key)
    if last is not None and now - last < window:
        return True
    _seen[key] = now
    return False


@url_hook.handle()
async def _(event: MessageEvent):
    if not plugin_config.pixiv_url_hook_enabled:
        return

    # 机器人自己的消息（含它刚发出的卡片）—— 不跳过就会自我触发
    if str(event.user_id) == str(event.self_id):
        return

    hit = urls.parse(urls.candidates(event.get_message()))
    if hit is None:
        return

    kind, target_id = hit
    key = (_session_key(event), kind, target_id)
    if _should_skip(key):
        logger.info(f"URL hook：{key} 在去重窗口内，跳过")
        return

    logger.info(f"URL hook 命中：{kind}={target_id} 会话={key[0]}")

    try:
        if kind == "novel":
            msg = await _build_novel_card(target_id)
        else:
            msg = await _build_series_card(target_id)
    except Exception as e:
        logger.warning(f"URL hook 取 {kind}={target_id} 失败: {type(e).__name__}: {e}")
        await url_hook.finish(f"取 {kind} {target_id} 失败，确认链接是否有效")
        return

    await url_hook.finish(msg)


async def _fetch_cover(url: str, x_restrict: int) -> tuple[bytes, bool]:
    """下载封面（**取原图**）并按 R18 规则决定是否模糊。返回 `(字节, 是否已模糊)`。

    封面拿不到**不算失败**：返回空字节，卡片照样发（只是没图）。
    图没下来时 `blurred` 一定是 False —— 否则文案会说「封面已模糊」而根本没有图。
    """
    if not url:
        return b"", False
    blurred = plugin_config.pixiv_blur_r18 and x_restrict in (1, 2)
    try:
        data = await client.download_cover(
            original_cover_url(url),
            blur=blurred,
            radius=plugin_config.pixiv_blur_radius,
            max_width=plugin_config.pixiv_cover_max_width,
        )
        return data, blurred and bool(data)
    except Exception as e:
        logger.warning(f"URL hook 下载封面失败（卡片改为无图）: {type(e).__name__}: {e}")
        return b"", False


async def _build_novel_card(novel_id: int) -> Message:
    detail = await client.novel_detail(novel_id)
    x_restrict = int(getattr(detail, "x_restrict", 0) or 0)
    cover_url = (getattr(getattr(detail, "image_urls", None), "large", "") or "")
    cover, blurred = await _fetch_cover(cover_url, x_restrict)
    return message.build_push(detail, cover, blurred=blurred)


async def _build_series_card(series_id: int) -> Message:
    from . import pixiv_client

    body = await client.novel_series(series_id)
    # R18 用**系列自己**的 xRestrict（网页接口专有；App 接口没有这个字段）
    x_restrict = int(body.get("xRestrict") or 0)
    cover, blurred = await _fetch_cover(pixiv_client.series_cover_url(body), x_restrict)
    return message.build_series_push(body, cover, blurred=blurred)



text_cmd = on_command("获取全文", priority=10, block=True)


@text_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not _is_admin(event):
        await text_cmd.finish(DENIED_MSG)

    channel = "group" if isinstance(event, GroupMessageEvent) else "private"

    novel_id = handlers.extract_id(args.extract_plain_text())
    if novel_id is None:
        await text_cmd.finish("用法：获取全文 <作品id>（也可直接粘小说链接）")
        return

    # 先取详情：① 校验 ID ② 拿 x_restrict 给投递决策用
    try:
        detail = await client.novel_detail(novel_id)
    except Exception as e:
        logger.warning(f"取作品 {novel_id} 详情失败: {e}")
        await text_cmd.finish(f"取作品 {novel_id} 失败，确认 ID 是否正确")
        return

    # ⚠️ R18 判定必须来自 novel_detail（novel_text 返回的 WebviewNovel 没有 x_restrict）
    x_restrict = int(getattr(detail, "x_restrict", 0) or 0)

    allowed, reason = policy.decide_text_delivery(
        channel=channel,
        x_restrict=x_restrict,
        pixiv_text_targets=plugin_config.pixiv_text_targets,
        pixiv_r18_text_allow_group=plugin_config.pixiv_r18_text_allow_group,
        # 管理员绕过两根轴（2026-10-02 用户要求）。
        # ⚠️ 这里用的是 `_is_admin_identity`（纯身份），**不是** `_is_admin`
        # （闸门）。因为 `pixiv_admin_only` 默认已改成 False，闸门对所有人放行；
        # 若拿闸门结果当绕过判据，每个普通群友都会被当成管理员，两轴就白设了。
        is_admin=_is_admin_identity(event),
    )
    if not allowed:
        await text_cmd.finish(reason)

    try:
        text = await client.novel_text(novel_id)
    except Exception as e:
        logger.warning(f"取作品 {novel_id} 全文失败: {e}")
        await text_cmd.finish(f"取全文失败：{novel_url(novel_id)}")
        return

    text = text.strip()
    if not text:
        await text_cmd.finish(f"这篇作品没有正文内容：{novel_url(novel_id)}")
        return

    filename = handlers.safe_filename(novel_id, detail.title)

    # 交付：内联 / 文件 / 链接 三级降级（逻辑在 handlers 里，可单测）
    used = await handlers.deliver_novel_text(
        text=text,
        title=detail.title,
        novel_id=novel_id,
        filename=filename,
        max_chars=plugin_config.pixiv_text_max_chars,
        send_text=lambda msg: text_cmd.send(msg),
        send_file=lambda fn, body: _send_file(event, fn, body),
    )
    logger.info(f"作品 {novel_id} 正文交付方式: {used}")
    await text_cmd.finish()
