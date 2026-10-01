"""实测：把原图缩到各档位后，实际字节数是多少（决定消息体积）。

场景：封面原图 901x1200 / 661KB~1007KB。当前是 bytes → NoneBot 自动 base64，
所以**消息体积 ≈ 字节数 × 1.37**。

本脚本对同一张真实封面测：
  · CDN 现成档位（/c/600x600/、/c/480x960/）
  · 服务端自己缩到 宽 600 / 800 / 1000 × JPEG 质量 80 / 85 / 90
  · 模糊后（R18）再缩的体积（模糊会掉细节 → JPEG 更小）

用法：uv run python scripts/probe_cover_target_size.py
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

import httpx
from PIL import Image, ImageFilter

REFERER = "https://www.pixiv.net/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
PROXY = "http://127.0.0.1:7900"
C_RE = re.compile(r"/c/\d+x\d+(?:_\d+)?/")

# 阿百川大鬼的一篇（901x1200 原图）
COVER = (
    "https://i.pximg.net/c/240x480_80/novel-cover-master/img/2026/04/14/16/26/17/"
    "sci15744810_da0194f312f9c61cd416f5cd3cc61812_master1200.jpg"
)


def get(url: str) -> bytes | None:
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"Referer": REFERER, "User-Agent": UA},
            proxy=PROXY,
        ) as c:
            r = c.get(url)
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def kb(n: int) -> str:
    return f"{n / 1024:.0f}KB"


def b64_kb(n: int) -> str:
    return f"{n * 1.37 / 1024:.0f}KB"


def main() -> None:
    raw_url = C_RE.sub("/", COVER)
    raw = get(raw_url)
    if not raw:
        print("✗ 原图下载失败")
        return
    img = Image.open(io.BytesIO(raw))
    print(f"原图: {img.width}x{img.height}  {kb(len(raw))}  → base64 后 {b64_kb(len(raw))}\n")

    print("=" * 78)
    print(f"{'方案':<34}{'尺寸':>14}{'字节':>10}{'base64后':>11}")
    print("=" * 78)

    # ── CDN 现成档位 ────────────────────────────────────────────
    for spec in ("600x600", "480x960"):
        u = C_RE.sub(f"/c/{spec}/", COVER)
        d = get(u)
        if d:
            im = Image.open(io.BytesIO(d))
            print(f"{f'CDN /c/{spec}/':<34}{f'{im.width}x{im.height}':>14}{kb(len(d)):>10}{b64_kb(len(d)):>11}")

    print("-" * 78)

    # ── 服务端自己缩 ────────────────────────────────────────────
    for width in (600, 800, 1000):
        for q in (80, 85, 90):
            w, h = img.size
            nh = round(h * width / w)
            resized = img.convert("RGB").resize((width, nh), Image.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, "JPEG", quality=q, optimize=True)
            d = buf.getvalue()
            print(f"{f'服务端缩到宽 {width} · q{q}':<34}{f'{width}x{nh}':>14}{kb(len(d)):>10}{b64_kb(len(d)):>11}")

    print("-" * 78)

    # ── 模糊后（R18）＋ 缩放：模糊会掉细节，字节更小 ──────────────
    for width in (600, 800):
        blurred = img.convert("RGB").filter(ImageFilter.GaussianBlur(9))
        w, h = blurred.size
        nh = round(h * width / w)
        resized = blurred.resize((width, nh), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, "JPEG", quality=85, optimize=True)
        d = buf.getvalue()
        print(
            f"{f'R18 模糊9px 后缩到宽 {width}':<34}{f'{width}x{nh}':>14}{kb(len(d)):>10}{b64_kb(len(d)):>11}"
        )

    print("-" * 78)

    # ── 模糊后再重新压缩：注意模糊本身也会让文件变大（要重编码）────
    bbuf = io.BytesIO()
    img.convert("RGB").filter(ImageFilter.GaussianBlur(9)).save(bbuf, "JPEG", quality=95)
    print(
        f"{'R18 模糊9px 不缩放 (q95)':<34}{f'{img.width}x{img.height}':>14}"
        f"{kb(len(bbuf.getvalue())):>10}{b64_kb(len(bbuf.getvalue())):>11}"
    )


if __name__ == "__main__":
    main()
