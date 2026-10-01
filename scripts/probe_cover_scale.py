"""实测：App 的封面 URL「去掉 /c/ 缩放段」后是不是就是 Web 端那张原图。

上一轮探针显示两者后缀相同（`..._master1200.jpg`），只差 `/c/240x480_80/` 与
`/c/600x600/` 前缀 —— 若真是同一个文件，那**根本不用换接口**，改写 URL 即可。

同时统计一批作品的真实尺寸分布，用来回答「换成原图后消息会变多大」。

用法：uv run python scripts/probe_cover_scale.py
"""

from __future__ import annotations

import asyncio
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


def fetch(url: str) -> tuple[int, int, int, str] | None:
    """→ (w, h, bytes, format)，失败返 None。"""
    if not url:
        return None
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"Referer": REFERER, "User-Agent": UA},
            proxy=PROXY,
        ) as c:
            r = c.get(url)
        if r.status_code != 200:
            return None
        img = Image.open(io.BytesIO(r.content))
        return (img.width, img.height, len(r.content), img.format or "?")
    except Exception:
        return None


async def main() -> None:
    if not TOKEN_FILE.exists():
        print(f"✗ 无 token 文件 {TOKEN_FILE}")
        return
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    refresh_token = tok.get("refresh_token") or tok.get("token") or ""

    import pixivpy3

    api = pixivpy3.AppPixivAPI()
    api.auth(refresh_token=refresh_token)
    api.requests_kwargs = {"proxies": {"http": PROXY, "https": PROXY}}

    # 作者 88871942（订阅的那位），取一页作品
    resp = api.user_novels(88871942, offset=0)
    novels = (resp.get("novels") or [])[:8]
    print(f"拿到 {len(novels)} 个作品\n")

    print("=" * 96)
    print(f"{'作品ID':>10}  {'App large(现状)':>18}  {'改写去 /c/ 后(原图)':>20}  {'倍数':>6}")
    print("=" * 96)

    rows: list[tuple[int, int, int, int, int, int]] = []
    placeholder = 0
    for n in novels:
        nid = n.get("id")
        try:
            det = api.novel_detail(nid)
        except Exception as exc:
            print(f"{nid:>10}  查询失败 {type(exc).__name__}")
            continue
        novel = det.get("novel") if hasattr(det, "get") else None
        if not novel:
            print(f"{nid:>10}  没拿到 novel")
            continue
        large = (novel.get("image_urls") or {}).get("large") or ""
        if "limit_unknown" in large:
            placeholder += 1
            print(f"{nid:>10}  ⚠️ 占位图（App 返回 limit_unknown_100.png）")
            continue
        raw = C_RE.sub("/", large) if C_RE.search(large) else large

        small = fetch(large)
        big = fetch(raw)
        s_txt = f"{small[0]}x{small[1]} {small[2]//1024}KB" if small else "下载失败"
        b_txt = f"{big[0]}x{big[1]} {big[2]//1024}KB" if big else "下载失败"
        ratio = f"{big[2] / small[2]:.1f}x" if (small and big and small[2]) else "-"
        print(f"{nid:>10}  {s_txt:>18}  {b_txt:>20}  {ratio:>6}")
        if small and big:
            rows.append((nid, small[0], small[2], big[0], big[2], 0))

    # ── 与 Web coverUrl 对比同一个作品 ────────────────────────────
    print()
    print("=" * 96)
    print("同一作品的 App vs Web 封面 URL 对比（看是不是同一个文件）")
    print("=" * 96)
    test_id = novels[0].get("id") if novels else None
    if test_id:
        det = api.novel_detail(test_id)
        novel = det.get("novel")
        app_large = (novel.get("image_urls") or {}).get("large") or ""
        app_raw = C_RE.sub("/", app_large)
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"Referer": REFERER, "User-Agent": UA, "Accept": "application/json"},
            proxy=PROXY,
        ) as c:
            r = await c.get(f"https://www.pixiv.net/ajax/novel/{test_id}")
        web = (r.json().get("body") or {}).get("coverUrl") or ""
        web_raw = C_RE.sub("/", web)
        print(f"  作品 {test_id}")
        print(f"    App large        : {app_large}")
        print(f"    Web coverUrl     : {web}")
        print(f"    App 去 /c/ 后     : {app_raw}")
        print(f"    Web 去 /c/ 后     : {web_raw}")
        print(f"    → 两者去缩放后**相同**？ {'✅ 是（同一个文件）' if app_raw == web_raw else '❌ 否'}")

    # ── 统计 ────────────────────────────────────────────────────
    if rows:
        print()
        print("=" * 96)
        print(f"统计（n={len(rows)}）")
        print("=" * 96)
        widths_small = sorted(r[1] for r in rows)
        widths_big = sorted(r[3] for r in rows)
        kb_small = sorted(r[2] // 1024 for r in rows)
        kb_big = sorted(r[4] // 1024 for r in rows)
        med = lambda xs: xs[len(xs) // 2]  # noqa: E731
        print(f"  现状(App large)  宽中位数 {med(widths_small)}px   "
              f"字节中位数 {med(kb_small)}KB   最大 {max(kb_small)}KB")
        print(f"  原图(去 /c/ 段)  宽中位数 {med(widths_big)}px   "
              f"字节中位数 {med(kb_big)}KB   最大 {max(kb_big)}KB")
        print(f"  → 体积放大约 {med(kb_big) / max(med(kb_small), 1):.1f} 倍")
        if placeholder:
            print(f"\n  ⚠️ 占位图作品数: {placeholder}（App 接口对这些返回 limit_unknown_100.png，"
                  f"不抛异常 → 会把 100x100 的占位图当封面发出去）")


if __name__ == "__main__":
    asyncio.run(main())
