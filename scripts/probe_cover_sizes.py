"""实测：App 端 vs Web 端的封面图**到底多小**，以及能不能拿到原图。

要回答 3 个问题（每条都用「下载后的真实像素」说话，不看 URL 字面）：
  1. App `novel_detail.image_urls` 的各键分别是什么尺寸？
  2. 单篇是否有 Web 接口（`/ajax/novel/<id>`）？它给的 cover 尺寸如何？
  3. **URL 改写**能不能拿原图？—— 去掉 `/c/<W>x<H>[_q]/` 缩放段试试。

输出只打 尺寸/字节/状态，绝不打 base64。

用法：uv run python scripts/probe_cover_sizes.py
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

R18_SERIES = 15744810
SAFE_SERIES = 16486288
# 用两个封面风格不同的作品：一个是常规封面，一个是 R18
NOVELS = [29277050, 29000000]

C_RE = re.compile(r"/c/\d+x\d+(?:_\d+)?/")


def probe_url(url: str, *, proxy: str = PROXY) -> str:
    """下载并报告真实像素尺寸。返回 'WxH (bytes)' 或错误说明。"""
    if not url:
        return "(空 URL)"
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"Referer": REFERER, "User-Agent": UA},
            proxy=proxy or None,
        ) as c:
            r = c.get(url)
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        img = Image.open(io.BytesIO(r.content))
        return f"{img.width}x{img.height}  ({len(r.content):,} 字节, {img.format})"
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:70]}"


def candidates(url: str) -> dict[str, str]:
    """同一张封面图的各种「加大」写法，用来测 CDN 是否认。"""
    out = {"原始URL": url}
    if C_RE.search(url):
        out["去掉 /c/ 缩放段"] = C_RE.sub("/", url)
        for spec in ("1200x1200", "1200x2400", "2400x2400"):
            out[f"改 /c/{spec}/"] = C_RE.sub(f"/c/{spec}/", url)
    # 结尾 _master1200 → 试着去掉后缀变体
    if "_master1200" in url:
        out["_master1200→_master"] = url.replace("_master1200", "_master")
        out["_master1200→original"] = url.replace("_master1200", "")
    return out


async def web_novel(sid: int) -> dict | None:
    url = f"https://www.pixiv.net/ajax/novel/{sid}"
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={"Referer": REFERER, "User-Agent": UA, "Accept": "application/json"},
        proxy=PROXY,
    ) as c:
        r = await c.get(url)
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        return None
    payload = r.json()
    if payload.get("error"):
        print(f"  error=true message={payload.get('message')}")
        return None
    return payload.get("body")


async def main() -> None:
    # ── 1) App 端 novel_detail 的 image_urls ───────────────────────
    print("=" * 70)
    print("【1】App 端 novel_detail.image_urls 各键")
    print("=" * 70)
    if not TOKEN_FILE.exists():
        print(f"  ✗ 没有 token 文件 {TOKEN_FILE}")
        return
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    refresh_token = tok.get("refresh_token") or tok.get("token") or ""
    import pixivpy3

    api = pixivpy3.AppPixivAPI()
    if PROXY:
        api.set_api_proxy = None
    api.auth(refresh_token=refresh_token)
    # pixivpy3 走系统代理；TUN 模式下直连即可
    for nid in NOVELS:
        try:
            resp = api.novel_detail(nid)
        except Exception as exc:
            print(f"  ✗ 作品 {nid}: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        novel = resp.get("novel") if hasattr(resp, "get") else None
        if not novel:
            print(f"  ✗ 作品 {nid}: 没拿到 novel")
            continue
        iu = novel.get("image_urls") or {}
        print(f"\n  作品 {nid} 「{novel.get('title')}」x_restrict={novel.get('x_restrict')}")
        print(f"    image_urls 的键: {sorted(iu.keys())}")
        for k in sorted(iu.keys()):
            v = iu[k]
            if not isinstance(v, str):
                print(f"      {k:10s} = {v!r}")
                continue
            print(f"      {k:10s} → {probe_url(v)}")
            print(f"                 {v}")

    # ── 2) 单篇作品的 Web 接口 ─────────────────────────────────────
    print()
    print("=" * 70)
    print("【2】单篇 Web 接口 /ajax/novel/<id> 是否存在、给什么")
    print("=" * 70)
    body = await web_novel(NOVELS[0])
    if body:
        print(f"  顶层键: {sorted(body.keys())[:25]}")
        for key in ("cover", "imageUrls", "image_urls", "urls"):
            v = body.get(key)
            if isinstance(v, dict):
                print(f"  body['{key}'] 的键: {sorted(v.keys())}")
                for k, u in v.items():
                    if isinstance(u, str) and u.startswith("http"):
                        print(f"    {k:12s} → {probe_url(u)}")

    # ── 3) 系列 Web 接口（已在用）──────────────────────────────────
    print()
    print("=" * 70)
    print("【3】系列 Web 接口 cover.urls 各键的真实尺寸")
    print("=" * 70)
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={"Referer": REFERER, "User-Agent": UA, "Accept": "application/json"},
        proxy=PROXY,
    ) as c:
        for sid in (R18_SERIES, SAFE_SERIES):
            r = await c.get(f"https://www.pixiv.net/ajax/novel/series/{sid}")
            b = r.json().get("body") or {}
            cover = b.get("cover") or {}
            urls = cover.get("urls") or {}
            print(f"\n  系列 {sid} xRestrict={b.get('xRestrict')} cover 键={sorted(urls.keys())}")
            for k in sorted(urls.keys()):
                print(f"    {k:12s} → {probe_url(urls[k])}")

    # ── 4) URL 改写能否拿原图 ──────────────────────────────────────
    print()
    print("=" * 70)
    print("【4】URL 改写尝试（能否拿比 /c/ 缩略更大的图）")
    print("=" * 70)
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={"Referer": REFERER, "User-Agent": UA, "Accept": "application/json"},
        proxy=PROXY,
    ) as c:
        r = await c.get(f"https://www.pixiv.net/ajax/novel/series/{R18_SERIES}")
        base = ((r.json().get("body") or {}).get("cover") or {}).get("urls") or {}
    # 拿一个最小的当改写基底
    smallest = base.get("128x128") or base.get("240mw") or ""
    if smallest:
        print(f"\n  基底（最小）: {smallest}")
        for label, u in candidates(smallest).items():
            print(f"    {label:18s} → {probe_url(u)}")
            print(f"      {u}")
    # 也用 480mw 试一次
    mid = base.get("480mw") or ""
    if mid:
        print(f"\n  基底（480mw）: {mid}")
        for label, u in candidates(mid).items():
            print(f"    {label:18s} → {probe_url(u)}")


if __name__ == "__main__":
    asyncio.run(main())
