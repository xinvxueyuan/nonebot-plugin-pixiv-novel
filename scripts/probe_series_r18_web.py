"""实测：系列 R18 标记到底在哪个接口里。

已确认（probe_series_r18.py）：App 接口 `/v2/novel/series` 的 `novel_series_detail`
只有 11 个键，**没有**任何 restrict/r18 字段（用一个全 R18 系列验的，排除
「false 被省略」的假象）。

那「系列有 R18 属性」如果成立，只可能在**别的接口**：
  1. 网页版 AJAX：`https://www.pixiv.net/ajax/novel/series/<id>`
  2. App 接口的其他路径：`/v1/novel/series`、`/v2/novel/series/{id}` 等
  3. 网页版作品页内嵌 JSON

这里逐个打，看哪个真带 R18 字段。只读。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
SERIES_ID = 15744810          # 全 R18 系列（4 篇全 x_restrict=1）
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
REFERER = "https://www.pixiv.net/"


def _hunt(obj, path="", depth=0, out=None):
    """递归找键名或字符串值里带 restrict/r18 的字段。"""
    if out is None:
        out = []
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if "restrict" in kl or "r18" in kl or "r-18" in kl:
                out.append((f"{path}.{k}", repr(v)[:60]))
            _hunt(v, f"{path}.{k}", depth + 1, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            _hunt(v, f"{path}[{i}]", depth + 1, out)
    elif isinstance(obj, str):
        sl = obj.lower()
        if len(obj) < 200 and ("r-18" in sl or '"xrestrict"' in sl):
            out.append((path, repr(obj)[:60]))
    return out


def probe(label: str, url: str, headers: dict, api=None, method="novel_series") -> None:
    print(f"\n── {label}\n   {url}")
    try:
        if api is not None:
            r = getattr(api, method)(SERIES_ID)
            data = r if isinstance(r, dict) else json.loads(json.dumps(r, default=str))
            print(f"   ✅ 成功，顶层键: {sorted(data.keys())[:12]}")
        else:
            with httpx.Client(timeout=25.0, follow_redirects=True, headers=headers) as c:
                resp = c.get(url)
            print(f"   HTTP {resp.status_code}  {len(resp.content)} 字节")
            if resp.status_code != 200:
                print(f"   前 160 字: {resp.text[:160]!r}")
                return
            data = resp.json()
    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {str(e)[:120]}")
        return

    hits = _hunt(data)
    if hits:
        print(f"   🔎 找到 {len(hits)} 处 restrict/r18 相关字段：")
        for p, v in hits[:15]:
            print(f"      {p} = {v}")
    else:
        print("   🔎 **没有**任何 restrict/r18 字段")

    # 顶层键（若是 dict）
    if isinstance(data, dict):
        print(f"   顶层键: {sorted(data.keys())[:14]}")


def main() -> int:
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))

    # 1) 网页 AJAX
    js = {"User-Agent": UA, "Referer": REFERER, "Accept": "application/json"}
    probe("网页 AJAX /ajax/novel/series/<id>（无 cookie）",
          f"https://www.pixiv.net/ajax/novel/series/{SERIES_ID}", js)

    # 2) 带 token 的网页 AJAX（有 cookie 时更可能返回完整内容）
    js_auth = {**js, "Authorization": f"Bearer {tok['access_token']}"} if "access_token" in tok else js
    if "access_token" in tok:
        probe("网页 AJAX + Bearer token",
              f"https://www.pixiv.net/ajax/novel/series/{SERIES_ID}", js_auth)

    # 3) App 接口别名
    probe("App /v1/novel/series/<id>",
          f"https://app-api.pixiv.net/v1/novel/series/{SERIES_ID}", js_auth)

    # 4) pixivpy3 的 novel_series（已知结果，做对照）
    from pixivpy3 import AppPixivAPI
    api = AppPixivAPI()
    api.auth(refresh_token=tok["refresh_token"])
    probe("pixivpy3 AppPixivAPI.novel_series（对照）",
          "app-api.pixiv.net/v2/novel/series", js_auth, api=api)

    # 5) 系列页面 HTML 里内嵌的 JSON
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True, headers=js) as c:
            html = c.get(f"https://www.pixiv.net/novel/series/{SERIES_ID}").text
        print(f"\n── 系列页 HTML: {len(html)} 字符")
        for needle in ('"xRestrict"', '"x_restrict"', "R-18", '"isOriginal"'):
            print(f"   {needle!r} 出现 {html.count(needle)} 次")
        idx = html.find('"xRestrict"')
        if idx > 0:
            print(f"   xRestrict 上下文: {html[max(0, idx - 90):idx + 60]!r}")
    except Exception as e:
        print(f"\n── 系列页 HTML ❌ {type(e).__name__}: {str(e)[:100]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
