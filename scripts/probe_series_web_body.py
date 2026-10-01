"""看网页 AJAX `/ajax/novel/series/<id>` 的 body 里到底有什么。

目的：确认它能否**一次提供整张卡片所需的全部字段**（系列级 xRestrict + 标题 +
话数 + 作者 + 首篇 id/标题/封面）。若能，系列分支就只走这一个接口，
不用再「查 App 接口 + 回头找首篇」绕弯。

只读。同时对比 R18 系列与非 R18 系列，确认 xRestrict 真的随内容变。
"""

from __future__ import annotations

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.pixiv.net/",
           "Accept": "application/json"}

R18_SERIES = 15744810        # 4 篇全 R18
SAFE_SERIES = 16486288       # 15 篇全非 R18


def dump(label: str, obj, path="", depth=0, lines=None):
    if lines is None:
        lines = []
    if depth > 3 or len(lines) > 70:
        return lines
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, dict):
                lines.append(f"  {p} = <dict {len(v)} 键>")
                dump(label, v, p, depth + 1, lines)
            elif isinstance(v, list):
                inner = ""
                if v and isinstance(v[0], dict):
                    inner = f" 首项键={sorted(v[0].keys())[:12]}"
                elif v:
                    inner = f" 首项={str(v[0])[:50]!r}"
                lines.append(f"  {p} = <list len={len(v)}>{inner}")
                if v and isinstance(v[0], dict):
                    dump(label, v[0], f"{p}[0]", depth + 1, lines)
            else:
                lines.append(f"  {p} = {type(v).__name__}: {str(v)[:70]!r}")
    return lines


def main() -> int:
    for sid, label in ((R18_SERIES, "R18 系列"), (SAFE_SERIES, "非 R18 系列")):
        print(f"\n{'=' * 70}\n{label} series={sid}  /ajax/novel/series/{sid}\n{'=' * 70}")
        try:
            with httpx.Client(timeout=25.0, follow_redirects=True, headers=HEADERS) as c:
                r = c.get(f"https://www.pixiv.net/ajax/novel/series/{sid}")
            print(f"HTTP {r.status_code}  {len(r.content)} 字节")
            j = r.json()
        except Exception as e:
            print(f"❌ {type(e).__name__}: {str(e)[:120]}")
            continue

        body = j.get("body") or {}
        print(f"\nbody 的顶层键（{len(body)} 个）:")
        for k in sorted(body.keys()):
            v = body[k]
            if isinstance(v, dict):
                print(f"  {k:26s} <dict {len(v)}>")
            elif isinstance(v, list):
                print(f"  {k:26s} <list {len(v)}>")
            else:
                print(f"  {k:26s} {type(v).__name__}: {str(v)[:60]!r}")

        print(f"\n⭐ body.xRestrict = {body.get('xRestrict')!r}")
        print(f"   body.maxXRestrict = {body.get('maxXRestrict')!r}")

        print("\nbody 全部嵌套字段:")
        for line in dump(label, body)[:60]:
            print(line)

        # 首篇作品在哪
        print("\n🔎 找「第一个作品」的线索:")
        for k in ("firstNovelId", "novelId", "firstNovel", "id"):
            if k in body:
                print(f"   body.{k} = {body[k]!r}")
        for k in ("novels", "novelSeries", "series"):
            if k in body:
                v = body[k]
                print(f"   body.{k}: len={len(v) if hasattr(v, '__len__') else '-'}")

        # 封面
        for k in list(body.keys()):
            if "image" in k.lower() or "cover" in k.lower() or "url" in k.lower():
                print(f"   body.{k} = {str(body[k])[:110]!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
