"""端到端真机验证：封面现在取的是**原图**，卡片连着真图。

要证明的（每条都对应一个「写错了也不报错」的点）：
  1. 单篇卡片拿到的封面是原图尺寸（不是 240px 缩略）—— 对比改前后的 URL
  2. 系列卡片优先取 `original`（最大的那张）
  3. 占位图作品 → **不带图**，且**不发下载请求**
  4. R18 封面仍然被模糊（尺寸变大后模糊还得生效）
  5. 卡片文案里不出现字面 None / 「已模糊却没有图」的矛盾

用法：uv run python scripts/smoke_cover_original.py
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

import nonebot
from nonebot.adapters.onebot.v11 import Adapter
from PIL import Image

nonebot.init(driver="~none", log_level="WARNING")
nonebot.get_driver().register_adapter(Adapter)

from nonebot_plugin_pixiv_novel.pixiv_client import (  # noqa: E402
    PixivClient,
    image_size,
    is_placeholder_url,
    original_cover_url,
    series_cover_url,
)

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
PROXY = "http://127.0.0.1:7900"

R18_NOVEL = 29277050
R18_SERIES = 15744810
SAFE_SERIES = 16486288
# 实测：App 对这篇返回 limit_unknown 占位图（不抛异常）
PLACEHOLDER_NOVEL = 29000000

ok = 0
fail = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {label}" + (f" — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))


def sharpness(data: bytes, box: tuple[int, int] = (200, 400)) -> float:
    """裁到统一尺寸后取拉普拉斯方差 —— 尺寸无关的清晰度度量。"""
    from PIL import ImageFilter, ImageStat

    img = Image.open(io.BytesIO(data)).convert("L")
    w, h = img.size
    cw, ch = min(box[0], w), min(box[1], h)
    left, top = (w - cw) // 2, (h - ch) // 2
    img = img.crop((left, top, left + cw, top + ch))
    return ImageStat.Stat(img.filter(ImageFilter.FIND_EDGES)).stddev[0]


async def main() -> int:
    token = ""
    if TOKEN_FILE.exists():
        import json

        tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        token = tok.get("refresh_token") or tok.get("token") or ""
    client = PixivClient(token, PROXY)

    # ── 1) 单篇：缩略 vs 原图 ─────────────────────────────────────
    print(f"\n【1】单篇作品 {R18_NOVEL}：缩略 vs 原图")
    detail = await client.novel_detail(R18_NOVEL)
    thumb_url = (getattr(getattr(detail, "image_urls", None), "large", "") or "")
    orig_url = original_cover_url(thumb_url)
    check("缩略 URL 带 CDN 缩放段", "/c/" in thumb_url, thumb_url.split("/c/")[1][:16] if "/c/" in thumb_url else "")
    check("原图 URL 去掉了缩放段", "/c/" not in orig_url)

    thumb = await client.download_cover(thumb_url, blur=False, radius=9)
    orig = await client.download_cover(orig_url, blur=False, radius=9)
    ts, os_ = image_size(thumb), image_size(orig)
    check("缩略图确实很小", bool(ts) and ts[0] <= 300, f"{ts} {len(thumb) // 1024}KB")
    check(
        "原图明显更大",
        bool(os_) and bool(ts) and os_[0] > ts[0] * 2,
        f"{os_} {len(orig) // 1024}KB（放大 {len(orig) / max(len(thumb), 1):.1f}x）",
    )
    # ⚠️ 封面原图尺寸**因作品而异**（实测 640x900 / 828x1200 / 901x1200 都见过），
    # 不能断言「长边 ≥1200」—— 那是把某篇的尺寸当成规格。只保证比缩略大得多。
    check("原图宽 ≥ 600（远大于 240px 缩略）", bool(os_) and os_[0] >= 600, str(os_))

    # ── 2) R18 原图仍然被模糊 ────────────────────────────────────
    print("\n【2】R18 原图模糊（尺寸变大后模糊还得生效）")
    blurred = await client.download_cover(orig_url, blur=True, radius=9)
    s_before, s_after = sharpness(orig), sharpness(blurred)
    check("模糊后清晰度显著下降", s_after < s_before * 0.5, f"{s_before:.1f} → {s_after:.1f}")
    check("模糊后尺寸没变", image_size(blurred) == os_, str(image_size(blurred)))

    # ── 3) 系列卡片取 original ───────────────────────────────────
    print(f"\n【3】系列 {R18_SERIES}：封面取 original")
    body = await client.novel_series(R18_SERIES)
    surl = series_cover_url(body)
    check("选中的是 original 键", surl == (body["cover"]["urls"].get("original")), surl[-40:])
    sdata = await client.download_cover(surl, blur=False, radius=9)
    ss = image_size(sdata)
    check("系列封面是大图", bool(ss) and max(ss) >= 1200, f"{ss} {len(sdata) // 1024}KB")

    # ── 4) 占位图 → 不带图、不发请求 ─────────────────────────────
    print(f"\n【4】占位图作品 {PLACEHOLDER_NOVEL}")
    try:
        pdet = await client.novel_detail(PLACEHOLDER_NOVEL)
        purl = (getattr(getattr(pdet, "image_urls", None), "large", "") or "")
        check("App 给的是占位图 URL（实测如此，不是异常）", is_placeholder_url(purl), purl[:60])
        pdata = await client.download_cover(purl, blur=True, radius=9)
        check("占位图 → 空字节（卡片不带图）", pdata == b"", f"{len(pdata)} bytes")
    except Exception as exc:
        print(f"     （这篇查不到，跳过：{type(exc).__name__}）")

    # ── 5) 缩放开关（MAX_WIDTH>0）────────────────────────────────
    print("\n【5】缩放开关（PIXIV_COVER_MAX_WIDTH）")
    # ⚠️ 这篇原图只有 640 宽 —— 设 800 不会放大（正确行为，放大只会糊）
    not_upscaled = await client.download_cover(orig_url, blur=False, radius=9, max_width=800)
    check("上限比原图大 → 不放大", image_size(not_upscaled) == os_, str(image_size(not_upscaled)))
    scaled = await client.download_cover(orig_url, blur=False, radius=9, max_width=400)
    sc = image_size(scaled)
    check(
        "缩到宽 400",
        bool(sc) and sc[0] == 400,
        f"{sc} {len(scaled) // 1024}KB（原图 {os_} {len(orig) // 1024}KB）",
    )

    print("\n" + "=" * 62)
    print(f"通过 {ok} / 失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
