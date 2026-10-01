"""补测：单篇 Web 接口 `/ajax/novel/<id>` 的封面字段（键名是 `coverUrl`，不是 cover）。

上一版探针猜了 cover/imageUrls/urls 三个键名全落空 —— 这正是「猜键名会静默降级」
的老毛病。这次先把顶层键全列出来，再挑。

另外测：
  · 头像（profile_image_urls）的真实尺寸 —— 订阅列表卡片也用它
  · 「占位图」现象：不存在的 / 受限作品返回的 limit_unknown_100.png

用法：uv run python scripts/probe_cover_web.py
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

import httpx
from PIL import Image

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
REFERER = "https://www.pixiv.net/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
PROXY = "http://127.0.0.1:7900"

NOVELS = [29277050, 28401246]
MISSING_NOVEL = 29000000


def probe_url(url: str) -> str:
    if not url:
        return "(空)"
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"Referer": REFERER, "User-Agent": UA},
            proxy=PROXY,
        ) as c:
            r = c.get(url)
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        img = Image.open(io.BytesIO(r.content))
        return f"{img.width}x{img.height}  ({len(r.content):,} 字节, {img.format})"
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:60]}"


async def main() -> None:
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={"Referer": REFERER, "User-Agent": UA, "Accept": "application/json"},
        proxy=PROXY,
    ) as c:
        # ── 1) 单篇 Web 顶层键里跟图有关的 ──────────────────────────
        print("=" * 72)
        print("【1】单篇 Web `/ajax/novel/<id>` 里所有「像图片 URL」的字段")
        print("=" * 72)
        for nid in [*NOVELS, MISSING_NOVEL]:
            r = await c.get(f"https://www.pixiv.net/ajax/novel/{nid}")
            if r.status_code != 200:
                print(f"\n  作品 {nid}: HTTP {r.status_code}")
                continue
            payload = r.json()
            if payload.get("error"):
                print(f"\n  作品 {nid}: error=true  message={payload.get('message')}")
                continue
            body = payload.get("body") or {}
            print(f"\n  作品 {nid} 「{body.get('title')}」xRestrict={body.get('xRestrict')}")
            found = 0
            for k, v in sorted(body.items()):
                if isinstance(v, str) and v.startswith("http") and "pximg" in v:
                    found += 1
                    print(f"    {k:22s} → {probe_url(v)}")
                    print(f"      {v}")
                elif isinstance(v, dict):
                    for k2, v2 in sorted(v.items()):
                        if isinstance(v2, str) and "pximg" in v2:
                            found += 1
                            print(f"    {k}.{k2:18s} → {probe_url(v2)}")
            if not found:
                print("    (没找到 pximg 图片字段)")

        # ── 2) 头像 ────────────────────────────────────────────────
        print()
        print("=" * 72)
        print("【2】头像 profile_image_urls（订阅列表卡片用）")
        print("=" * 72)
        uid = 88871942  # 阿百川大鬼
        r = await c.get(f"https://www.pixiv.net/ajax/user/{uid}?full=1")
        if r.status_code == 200:
            b = r.json().get("body") or {}
            piu = b.get("profile_image_urls") or {}
            print(f"  user {uid} 的键: {sorted(piu.keys())}")
            for k in sorted(piu.keys()):
                v = piu[k]
                print(f"    {k:12s} → {probe_url(v) if isinstance(v, str) else v!r}")
                if isinstance(v, str):
                    print(f"      {v}")
        else:
            print(f"  HTTP {r.status_code}")

        # ── 3) 单篇 Web 的 coverUrl 能否拿更大 ──────────────────────
        print()
        print("=" * 72)
        print("【3】单篇 coverUrl 的 URL 改写")
        print("=" * 72)
        r = await c.get(f"https://www.pixiv.net/ajax/novel/{NOVELS[0]}")
        body = r.json().get("body") or {}
        cu = body.get("coverUrl") or ""
        print(f"  coverUrl: {cu}")
        if cu:
            import re

            print(f"    {'原始':22s} → {probe_url(cu)}")
            noscale = re.sub(r"/c/\d+x\d+(?:_\d+)?/", "/", cu)
            print(f"    {'去掉 /c/ 缩放段':22s} → {probe_url(noscale)}")
            print(f"      {noscale}")
            for spec in ("1200x1200", "600x600", "480x960"):
                u = re.sub(r"/c/\d+x\d+(?:_\d+)?/", f"/c/{spec}/", cu)
                print(f"    {f'改 /c/{spec}/':22s} → {probe_url(u)}")


if __name__ == "__main__":
    asyncio.run(main())
