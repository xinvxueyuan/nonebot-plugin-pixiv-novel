"""实测：`novel_series_detail` 到底有没有 R18 标记字段。

背景：之前用 series 16486288（**非 R18**）探过结构，没看到 x_restrict，
就下了「系列详情没有 R18 字段」的结论 —— 但 pixiv 对 false/0 字段常常**直接省略**，
所以那个结论可能是假象。这里改用**用户给的系列 15744810**，并把
`novel_series_detail` 的**全部键**（不截断）打出来，再对比探。

只读，不改任何东西。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pixivpy3 import AppPixivAPI

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"

# 用户给的系列 + 之前探过的非 R18 系列，两个一起对比
SERIES_IDS = [15744810, 16486288]


def _retry(fn, tries=4, delay=3.0):
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
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    api = AppPixivAPI()
    api.auth(refresh_token=tok["refresh_token"])

    for sid in SERIES_IDS:
        print(f"\n{'=' * 68}\n系列 {sid}\n{'=' * 68}")
        try:
            ser = _retry(lambda _sid=sid: api.novel_series(_sid))
        except Exception as e:
            print(f"  拉取失败: {type(e).__name__}: {e}")
            continue

        print("顶层键:", sorted(ser.keys()))
        d = ser["novel_series_detail"]
        print("\nnovel_series_detail 的**全部键**（不截断）:")
        for k in sorted(d.keys()):
            v = d[k]
            if isinstance(v, dict):
                print(f"  {k:28s} = <dict> keys={sorted(v.keys())}")
            elif isinstance(v, list):
                print(f"  {k:28s} = <list len={len(v)}>")
            else:
                print(f"  {k:28s} = {type(v).__name__}: {str(v)[:70]!r}")

        # 全量搜一遍：任何键/字符串值里含 r18 / restrict / 18 的
        print("\n  🔎 含 r18/restrict 的键:", [k for k in d if "restrict" in k.lower() or "r18" in k.lower()])
        print("  🔎 含 x_restrict 的键:", [k for k in d if k == "x_restrict"])
        for k in d:
            sv = str(d[k]).lower()
            if "r-18" in sv or "r18" in sv:
                print(f"  🔎 值里出现 r18 的键: {k} = {str(d[k])[:80]!r}")

        # 系列里各作品的 x_restrict 分布（系列列表自带）
        novels = ser.novels or []
        dist = {}
        for n in novels:
            xr = getattr(n, "x_restrict", None)
            dist[xr] = dist.get(xr, 0) + 1
        print(f"\n  novels({len(novels)} 篇) 的 x_restrict 分布: {dist}")
        print(f"  首篇 x_restrict={getattr(ser['novel_series_first_novel'], 'x_restrict', None)}"
              f"  最新篇 x_restrict={getattr(ser['novel_series_latest_novel'], 'x_restrict', None)}")

        # 标签里能不能看出 R18
        tags = [t.get("name") for t in (getattr(ser["novel_series_first_novel"], "tags", None) or [])]
        print(f"  首篇标签: {tags}")

        # 系列详情有没有「相似/相关」之类的嵌套字段
        print(f"\n  系列详情键数={len(d.keys())}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
