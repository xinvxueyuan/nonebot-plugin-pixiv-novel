"""实测：头像（profile_image_urls）各键的真实尺寸。

订阅列表卡片里头像显示尺寸只有 76px，但先测清楚再决定要不要动。
顺便验证 `_square1200` 结尾的方形图**不能**套用「去 /c/ 段」那招
（去掉后会拿到 1200x1200 方图，不是封面比例）。

用法：uv run python scripts/probe_avatar_sizes.py
"""

from __future__ import annotations

import io
import json
import re
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
C_RE = re.compile(r"/c/\d+x\d+(?:_\d+)?/")

USER_ID = 88871942


def fetch(url: str) -> str:
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


def main() -> None:
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    import pixivpy3

    api = pixivpy3.AppPixivAPI()
    api.auth(refresh_token=tok.get("refresh_token") or tok.get("token") or "")

    print("=" * 74)
    print(f"【1】App user_detail({USER_ID}).profile_image_urls 各键")
    print("=" * 74)
    try:
        det = api.user_detail(USER_ID)
        user = det.get("user") if hasattr(det, "get") else None
        piu = (user or {}).get("profile_image_urls") or {}
        print(f"  键: {sorted(piu.keys())}")
        for k in sorted(piu.keys()):
            v = piu[k]
            print(f"    {k:12s} → {fetch(v) if isinstance(v, str) else v!r}")
            if isinstance(v, str):
                print(f"      {v}")
    except Exception as exc:
        print(f"  ✗ {type(exc).__name__}: {str(exc)[:100]}")

    print()
    print("=" * 74)
    print("【2】验证：方形图(_square1200) 套用「去 /c/ 段」会拿到什么")
    print("=" * 74)
    square = (
        "https://i.pximg.net/c/128x128/novel-cover-master/img/2026/04/14/16/26/17/"
        "sci15744810_da0194f312f9c61cd416f5cd3cc61812_square1200.jpg"
    )
    print(f"  原始  128x128 声明     → {fetch(square)}")
    print(f"  去掉 /c/ 段            → {fetch(C_RE.sub('/', square))}")
    print("  （若是 1200x1200 方图，说明这招只对 _master1200 结尾的图安全）")


if __name__ == "__main__":
    main()
