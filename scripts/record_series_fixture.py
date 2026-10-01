"""录 `novel_series` 的真实响应 fixture —— **网页接口**版本。

⚠️ 为什么录网页接口而不是 App 接口：App `/v2/novel/series` 的系列详情
**没有任何 R18 字段**（实测；见 pixiv_client.py 顶部注释），而插件出系列卡片要的
`xRestrict` 只在网页 `/ajax/novel/series/N` 里有。fixture 必须录**实际使用**的那个接口，
否则测试测的是另一套形状，等于没测。

同时录 **R18 系列**与**非 R18 系列**两份：只看 R18 的那份无法证明
「非 R18 系列返回的是显式 0、不是缺键」，而这个区别正是被测代码依赖的。

与 record_fixtures.py 同样的纪律：只保留断言需要的字段、结构照实保留、不含 token。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

OUT = Path(__file__).parent.parent / "tests" / "fixtures"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.pixiv.net/", "Accept": "application/json"}

CASES = {
    "novel_series_r18.json": 15744810,       # 4 篇全 R18 → xRestrict=1
    "novel_series_safe.json": 16486288,      # 15 篇全非 R18 → xRestrict=0
}

KEEP = [
    "id", "title", "caption", "xRestrict", "isOriginal", "isConcluded",
    "publishedContentCount", "displaySeriesContentCount", "publishedTotalCharacterCount",
    "total", "userName", "userId", "profileImageUrl", "tags", "firstNovelId",
]


def _retry(fn, tries=5, delay=3.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"    ⚠️ 第 {i + 1}/{tries} 次失败：{type(e).__name__}: {str(e)[:80]}")
            if i + 1 < tries:
                time.sleep(delay)
    raise last


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sid in CASES.items():
        url = f"https://www.pixiv.net/ajax/novel/series/{sid}"

        def fetch(_url: str = url) -> dict:
            with httpx.Client(timeout=25.0, follow_redirects=True, headers=HEADERS) as c:
                resp = c.get(_url)
            resp.raise_for_status()
            return resp.json()

        payload = _retry(fetch)
        body = payload["body"]
        fixture = {k: body.get(k) for k in KEEP if k in body}
        fixture["cover"] = {"urls": (body.get("cover") or {}).get("urls") or {}}

        path = OUT / name
        path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {name}  {path.stat().st_size} 字节")
        print(f"  {fixture['title']!r}  共 {fixture.get('displaySeriesContentCount')} 话  "
              f"xRestrict={fixture.get('xRestrict')}  完结={fixture.get('isConcluded')}")
        print(f"  作者={fixture.get('userName')!r}({fixture.get('userId')})  "
              f"封面键={sorted((fixture['cover']['urls'] or {}).keys())}")
        print(f"  标签={fixture.get('tags')}")
        assert "xRestrict" in fixture, "缺 xRestrict —— 断言依据就没了"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
