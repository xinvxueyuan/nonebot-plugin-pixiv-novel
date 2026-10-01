"""端到端真机验证：被动 URL hook 走真实 pixiv，出真卡片。

要证明的东西（每条都对应一个「猜错也不报错」的点）：
  1. 单篇链接 → novel_detail 解包成功 → 卡片有标题/作者/标签/链接/封面
  2. 系列链接 → 网页接口拿到**系列级 xRestrict** → R18 系列被正确识别
  3. R18 封面**真的被模糊了**（不是「参数传了就算」）—— 用像素方差对比
  4. 非 R18 系列封面**不被模糊**
  5. 卡片文案里不出现字面 `None`

用真实 token（只从本地 gppt token 文件读，不进命令行）。

用法：uv run python scripts/smoke_url_hook.py
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

import nonebot
from nonebot.adapters.onebot.v11 import Adapter
from PIL import Image, ImageFilter, ImageStat

nonebot.init(driver="~none", log_level="WARNING")
nonebot.get_driver().register_adapter(Adapter)

from nonebot_plugin_pixiv_novel import message, pixiv_client  # noqa: E402
from nonebot_plugin_pixiv_novel.pixiv_client import PixivClient  # noqa: E402

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"

R18_SERIES = 15744810
SAFE_SERIES = 16486288
R18_NOVEL = 29277050

ok = 0
fail = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {label}" + (f" — {extra}" if extra else ""))
    else:
        fail += 1
        print(f"  ❌ {label}" + (f" — {extra}" if extra else ""))


def text_msg(msg) -> str:
    """只取消息里的**文本段**（消息尾巴挂着 base64 图片，直接 str 会淹掉输出）。"""
    parts = [seg.data.get("text", "") for seg in msg if seg.type == "text"]
    return "".join(parts) or str(msg)


def sharpness(data: bytes, box: tuple[int, int] = (200, 400)) -> float:
    """清晰度度量：**裁到统一尺寸后**取拉普拉斯方差。

    为什么不用全局灰度标准差：它**随图片尺寸变化** —— 同样半径 9 的高斯模糊，
    480×960 的系列封面标准差只掉 37%，而 240×480 的作品封面掉 68%。
    拿固定比例当阈值就会误报（实测踩过）。拉普拉斯方差是图像清晰度的标准度量，
    对模糊极其敏感，且裁到同一尺寸后可比。
    """
    img = Image.open(io.BytesIO(data)).convert("L")
    w, h = img.size
    bw, bh = min(box[0], w), min(box[1], h)
    img = img.crop(((w - bw) // 2, (h - bh) // 2, (w + bw) // 2, (h + bh) // 2))
    lap = img.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=0))
    return ImageStat.Stat(lap).var[0]


async def main() -> int:
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    client = PixivClient(tok["refresh_token"], "")          # 本机 TUN 直连

    # ── 1) 单篇作品卡片 ─────────────────────────────────────────
    print("\n【1】单篇作品（R18 作品）")
    detail = await client.novel_detail(R18_NOVEL)
    xr = int(getattr(detail, "x_restrict", 0) or 0)
    cover_url = getattr(getattr(detail, "image_urls", None), "large", "") or ""
    check("解包后有 x_restrict", xr in (0, 1, 2), f"x_restrict={xr}")
    check("解包后有标题", bool(getattr(detail, "title", None)), str(getattr(detail, "title", ""))[:30])
    check("解包后有封面 URL", cover_url.startswith("https://i.pximg.net/"), cover_url[:60])

    raw_cover = await client.fetch_image(cover_url)
    sharp_raw = sharpness(raw_cover)
    cover_blur = await client.download_cover(cover_url, blur=True, radius=9)
    sharp_blur = sharpness(cover_blur)
    check("封面能下载（带 Referer）", len(raw_cover) > 1000, f"{len(raw_cover)} 字节")
    check("模糊真的生效", sharp_blur < sharp_raw * 0.6,
          f"清晰度 {sharp_raw:.1f} → {sharp_blur:.1f}")

    msg = message.build_push(detail, cover_blur, blurred=True)
    text = str(msg)
    check("卡片含作品链接", "pixiv.net/novel/show.php?id=" in text)
    check("卡片含作者链接", "pixiv.net/users/" in text)
    check("卡片无字面 None", "None" not in text)
    print("  ─── 卡片文案 ───")
    print(text_msg(msg))

    # ── 2) R18 系列卡片 ────────────────────────────────────────
    print(f"\n【2】R18 系列 {R18_SERIES}")
    body = await client.novel_series(R18_SERIES)
    series_xr = int(body.get("xRestrict") or 0)
    check("网页接口有系列级 xRestrict", series_xr == 1, f"xRestrict={series_xr}")
    check("有系列标题", bool(body.get("title")), str(body.get("title"))[:24])
    check("有话数/字数", bool(body.get("displaySeriesContentCount") or body.get("publishedTotalCharacterCount")),
          f"count={body.get('displaySeriesContentCount')} chars={body.get('publishedTotalCharacterCount')}")
    check("作者名/ID 在顶层", bool(body.get("userName")) and bool(body.get("userId")),
          f"{body.get('userName')}({body.get('userId')})")

    scover = pixiv_client.series_cover_url(body)
    check("有系列封面 URL", scover.startswith("https://i.pximg.net/"), scover[:60])
    s_raw = await client.fetch_image(scover)
    s_blur = await client.download_cover(scover, blur=True, radius=9)
    check("系列封面模糊生效", sharpness(s_blur) < sharpness(s_raw) * 0.6,
          f"清晰度 {sharpness(s_raw):.1f} → {sharpness(s_blur):.1f}")

    smsg = message.build_series_push(body, s_blur, blurred=True)
    stext = str(smsg)
    check("系列卡片含 R-18 标记", "R-18" in stext)
    check("系列卡片含系列链接", f"pixiv.net/novel/series/{R18_SERIES}" in stext)
    check("系列卡片无字面 None", "None" not in stext)
    print("  ─── 卡片文案 ───")
    print("\n".join(f"      {ln}" for ln in stext.split("\n")[:9]))

    # ── 3) 非 R18 系列：不该被模糊 ─────────────────────────────
    print(f"\n【3】非 R18 系列 {SAFE_SERIES}")
    safe = await client.novel_series(SAFE_SERIES)
    safe_xr = int(safe.get("xRestrict") or 0)
    check("系列级 xRestrict=0", safe_xr == 0, f"xRestrict={safe_xr}")
    check("xRestrict 是显式 0 而非缺键", "xRestrict" in safe)
    safe_url = pixiv_client.series_cover_url(safe)
    safe_blurred = safe_xr in (1, 2)          # 插件里就是这么算的
    safe_cover = await client.download_cover(safe_url, blur=safe_blurred, radius=9)
    check("非 R18 系列封面未模糊", safe_blurred is False, f"{len(safe_cover)} 字节")
    check("非 R18 系列封面与原始一致",
          safe_cover == await client.fetch_image(safe_url))
    safe_msg = message.build_series_push(safe, safe_cover, blurred=safe_blurred)
    safe_text = str(safe_msg)
    check("非 R18 系列卡片无 R-18 标记", "R-18" not in safe_text)
    check("非 R18 系列卡片无字面 None", "None" not in safe_text)
    print("  ─── 卡片文案 ───")
    print("\n".join(f"      {ln}" for ln in safe_text.split("\n")[:9]))

    # ── 4) 坏 ID 要抛异常（而不是「系列 None」卡片）─────────────
    print("\n【4】不存在的系列 ID（应为异常，不是空卡片）")
    try:
        await client.novel_series(999999999999)
        check("坏 ID 抛异常", False, "居然没抛")
    except Exception as e:
        check("坏 ID 抛异常", True, f"{type(e).__name__}: {str(e)[:70]}")

    print(f"\n{'=' * 60}\n通过 {ok} / 失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
