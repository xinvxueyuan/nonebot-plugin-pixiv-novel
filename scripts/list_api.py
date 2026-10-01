"""列出 pixivpy3 真实的方法签名 —— 不猜 API。"""

import inspect

from pixivpy3 import AppPixivAPI

print("=== 与 novel/user/ranking/search 相关的方法签名 ===")
for name, fn in sorted(inspect.getmembers(AppPixivAPI)):
    if name.startswith("_") or not callable(fn):
        continue
    if any(k in name for k in ("novel", "user", "ranking", "search", "ugoira", "trend")):
        try:
            print(f"  {name}{inspect.signature(fn)}")
        except (ValueError, TypeError):
            print(f"  {name}(...)")

print("\n=== 是否存在 novel_ranking / illust_ranking ===")
for n in ("novel_ranking", "illust_ranking", "novel_new", "novel_detail",
          "novel_text", "user_detail", "user_novels", "search_novel"):
    print(f"  {n:20} {'✅ 有' if hasattr(AppPixivAPI, n) else '❌ 无'}")
