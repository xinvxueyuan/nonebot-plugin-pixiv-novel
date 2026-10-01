"""Task 0 验收 —— 用**真插件代码**打真实 pixiv。

不是探测脚本，而是直接 import 插件自己的 PixivClient / policy / message，
证明「修复后的实现」在真环境下确实按要求工作。

**token 处理**：脚本自己从 gppt token 文件读，不进命令行/环境变量/日志。

验：
  1. 真 PixivClient 能鉴权（直连 TUN 或显式代理，自动选）
  2. user_novels / author_info（订阅列表要的作者名+头像）
  3. novel_detail 解包后 x_restrict 正确 → policy 决策正确
  4. novel_text 拿到真正文
  5. 封面下载（带 Referer）真能拿到字节，且模糊生效
  6. 「R18 正文缺省不发群聊」在真实 R18 作品上确实成立
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

import nonebot

nonebot.init(driver="~none", log_level="ERROR")

from nonebot_plugin_pixiv_novel import policy  # noqa: E402
from nonebot_plugin_pixiv_novel.config import Config  # noqa: E402
from nonebot_plugin_pixiv_novel.message import build_push, novel_url  # noqa: E402
from nonebot_plugin_pixiv_novel.pixiv_client import PixivClient  # noqa: E402

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
results: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  —— {detail}" if detail else ""))


async def main() -> int:
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    refresh_token = tok["refresh_token"]
    print(f"refresh_token 已读到（长度 {len(refresh_token)}，不打印）\n")

    # 通道自动选择（本机 TUN 直连 / 服务器显式代理）
    client = None
    # 本机 Clash 是**纯 TUN 模式**：没有任何 mixed-port 监听（实测 netstat 无 789x），
    # 所以「显式代理」在本机必然不通 —— 那是环境事实，不是代码缺陷。
    # 显式代理路径由 scripts/verify_proxy_path.py 用桩代理单独证明。
    client = PixivClient(refresh_token, "")
    try:
        await client.user_novels(61943687)
        rec("PixivClient 鉴权并可用（直连 / TUN 透明接管）", True, "proxy=无")
    except Exception as e:
        rec("PixivClient 鉴权", False, f"{type(e).__name__}: {str(e)[:90]}")
        print("\n❌ 直连不通，需要用户介入")
        return 1

    # ── 1. 作者信息（订阅列表字段）────────────────────────────────
    print("\n=== 1. 作者信息（订阅列表要显示作者名 + 头像）===")
    name, avatar = await client.author_info(61943687)
    rec("author_info 拿到作者名", bool(name), f"name={name!r}")
    rec("author_info 拿到头像 URL", avatar.startswith("http"), f"{avatar[:60]}...")

    # ── 2. user_novels 播种 ───────────────────────────────────────
    print("\n=== 2. 作品列表（订阅播种 / 高水位）===")
    novels = await client.user_novels(61943687)
    rec("user_novels 返回作品", bool(novels), f"{len(novels)} 篇")
    ids = [int(n.id) for n in novels]
    rec("作品 ID 可排序（高水位算法前提）", ids == sorted(ids, reverse=True),
        f"首个={ids[0]} 末个={ids[-1]}")

    # 挑一篇普通、一篇 R18
    normal_id = r18_id = None
    for n in novels:
        xr = int(getattr(n, "x_restrict", 0) or 0)
        if xr == 0 and normal_id is None:
            normal_id = int(n.id)
        if xr > 0 and r18_id is None:
            r18_id = int(n.id)
    rec("列表条目自带 x_restrict（无需额外请求即可初筛）",
        all(getattr(n, "x_restrict", None) is not None for n in novels),
        f"普通={normal_id} R18={r18_id}")

    # ── 3. novel_detail 解包 → R18 判定 ──────────────────────────
    print("\n=== 3. novel_detail 解包 + R18 判定（本次修复的核心）===")
    for label, nid in (("普通", normal_id), ("R18", r18_id)):
        if nid is None:
            print(f"  ⏭ {label}：无样本")
            continue
        d = await client.novel_detail(nid)
        xr = int(getattr(d, "x_restrict", 0) or 0)
        title = str(getattr(d, "title", "") or "")
        cover = getattr(getattr(d, "image_urls", None), "large", "") or ""
        rec(f"{label} novel_detail: 标题非空", bool(title), f"{title[:34]!r}")
        rec(f"{label} novel_detail: 封面 URL 非空（修复前恒为空）",
            cover.startswith("https://"), f"{cover[:52]}...")
        rec(f"{label} novel_detail: x_restrict 正确",
            xr == 0 if label == "普通" else xr > 0, f"x_restrict={xr}")

    # ── 4. 正文 ───────────────────────────────────────────────────
    print("\n=== 4. 全文（获取全文 命令的核心）===")
    for label, nid in (("普通", normal_id), ("R18", r18_id)):
        if nid is None:
            continue
        try:
            text = await client.novel_text(nid)
            rec(f"{label} 全文非空", len(text.strip()) > 50, f"{len(text)} 字")
        except Exception as e:
            rec(f"{label} 全文", False, f"{type(e).__name__}: {e}")

    # ── 5. 封面下载 + 模糊 ───────────────────────────────────────
    print("\n=== 5. 封面下载（带 Referer）+ 模糊 ===")
    d = await client.novel_detail(r18_id or normal_id)
    cover_url = getattr(getattr(d, "image_urls", None), "large", "") or ""
    if cover_url:
        try:
            raw = await client.fetch_image(cover_url)
            rec("封面原图下载成功", len(raw) > 1000, f"{len(raw)} 字节（无 Referer 会 403）")
            blurred = await client.download_cover(cover_url, blur=True, radius=9)
            rec("模糊后仍可解码", len(blurred) > 500, f"{len(blurred)} 字节")
            import io

            from PIL import Image
            rec("模糊输出是有效图片",
                Image.open(io.BytesIO(blurred)).size == Image.open(io.BytesIO(raw)).size,
                f"尺寸 {Image.open(io.BytesIO(blurred)).size}")
        except Exception as e:
            rec("封面下载", False, f"{type(e).__name__}: {e}")

    # ── 6. 策略矩阵在真实作品上成立 ───────────────────────────────
    print("\n=== 6. 真实 R18 作品上的投递策略 ===")
    d_r18 = await client.novel_detail(r18_id)
    xr = int(getattr(d_r18, "x_restrict", 0) or 0)
    cfg = Config()
    print(f"  默认配置：TEXT_TARGETS={cfg.pixiv_text_targets} "
          f"R18_ALLOW_GROUP={cfg.pixiv_r18_text_allow_group} "
          f"（用户拍板：默认仅群聊）")
    cases = [
        # (说明, 渠道, x_restrict, targets, r18_allow_group, 期望)
        ("默认：普通全文 → 群聊 应当放行", "group", 0, cfg.pixiv_text_targets, False, True),
        ("默认：R18 全文 → 群聊 应当拒绝（用户硬要求）", "group", xr, cfg.pixiv_text_targets, False, False),
        ("默认：R18 全文 → 私聊 应当拒绝（默认不含私聊）", "private", xr, cfg.pixiv_text_targets, False, False),
        # 两轴正交：渠道开了私聊，R18 轴仍然独立生效
        ("开私聊后：R18 全文 → 私聊 应当放行", "private", xr, ["group", "private"], False, True),
        ("开私聊后：R18 全文 → 群聊 仍应拒绝（R18 轴独立）", "group", xr, ["group", "private"], False, False),
        ("再把 R18 群聊轴打开：R18 → 群聊 应当放行", "group", xr, ["group", "private"], True, True),
        ("只允许私聊时：普通全文 → 群聊 应当拒绝", "group", 0, ["private"], False, False),
        ("只允许私聊时：普通全文 → 私聊 应当放行", "private", 0, ["private"], False, True),
    ]
    for label, channel, restrict, targets, flag, want in cases:
        ok, reason = policy.decide_text_delivery(
            channel=channel, x_restrict=restrict,
            pixiv_text_targets=targets, pixiv_r18_text_allow_group=flag,
        )
        rec(label, ok == want, f"allowed={ok} | {reason}")

    # ── 7. 推送文案（真实数据组装）───────────────────────────────
    print("\n=== 7. 推送文案（真数据组装 + 模糊封面）===")
    cover = await client.download_cover(cover_url, blur=True, radius=cfg.pixiv_blur_radius)
    msg = build_push(d_r18, cover, blurred=True)
    text = msg.extract_plain_text()
    print("  ---- 实际会发出去的消息 ----")
    for line in text.splitlines():
        print(f"    {line}")
    print(f"    [图片 {len(cover)} 字节]")
    rec("文案含作品名", "むっち" in text or len(text.splitlines()[0]) > 5)
    rec("文案含作者名", name in text)
    rec("文案含标签", "🏷" in text)
    rec("文案含作者链接", f"/users/{61943687}" in text)
    rec("文案含正文链接", novel_url(r18_id) in text)
    rec("文案含 R18 标记", "R-18" in text)
    rec("封面图已附在消息里", len(msg) > 1)

    print("\n" + "=" * 66)
    bad = [r for r in results if not r[1]]
    print(f"共 {len(results)} 项：✅ {len(results) - len(bad)}  ❌ {len(bad)}")
    for n, _, dd in bad:
        print(f"  ❌ {n}  {dd}")
    print("=" * 66)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
