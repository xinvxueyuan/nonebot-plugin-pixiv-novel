"""dump pixiv API 的真实响应结构 —— 用来纠正 R18 判定字段的取法。

⚠️ 背景：`novel_detail()` 的返回对象上 `getattr(d,'title','')` 和
`getattr(d,'x_restrict',0)` 都取到了**默认值**，说明真实字段不在顶层。
这直接影响 R18 判定（取错就永远是 0，R18 全文会被当普通作品发出去）。
"""

import json
from pathlib import Path

from pixivpy3 import AppPixivAPI

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
aapi = AppPixivAPI()
aapi.auth(refresh_token=tok["refresh_token"])


def shape(obj, depth=0, max_depth=3, path=""):
    """打印结构的「形状」（键名 + 类型），值只给短预览。"""
    pad = "  " * depth
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                print(f"{pad}{k}: dict")
                if depth < max_depth:
                    shape(v, depth + 1, max_depth, f"{path}.{k}")
            elif isinstance(v, list):
                t = type(v[0]).__name__ if v else "-"
                print(f"{pad}{k}: list[{len(v)}] of {t}")
                if v and isinstance(v[0], dict) and depth < max_depth:
                    shape(v[0], depth + 1, max_depth, f"{path}.{k}[0]")
            else:
                s = repr(v)
                print(f"{pad}{k}: {type(v).__name__} = {s[:60]}")
    else:
        print(f"{pad}{type(obj).__name__}")


# 用 R-18 搜索拿一个确实该是 R18 的作品
r = aapi.search_novel(word="R-18", filter="for_android",
                      search_target="partial_match_for_tags")
novels = r.novels or []
print(f"搜索 R-18：{len(novels)} 篇\n")

target = None
for n in novels:
    print(f"  候选 id={n.id} x_restrict={getattr(n, 'x_restrict', '<无此字段>')} "
          f"tags={[t.tag for t in (getattr(n,'tags',None) or [])][:4]}")
    if target is None:
        target = n

print(f"\n=== 选中 id={target.id} ===")

print("\n" + "=" * 64)
print("【1】search_novel 返回的条目结构（顶层）")
print("=" * 64)
print("  类型:", type(target).__name__)
print("  键:", sorted(target.keys())[:40])
print("  有 x_restrict 吗:", "x_restrict" in target)
print("  x_restrict 值:", repr(target.get("x_restrict", "<无>")))
print("  值预览:", repr(target.get("x_restrict")))

print("\n" + "=" * 64)
print("【2】novel_detail(id) 返回结构")
print("=" * 64)
d = aapi.novel_detail(target.id)
print("  类型:", type(d).__name__)
print("  顶层键:", sorted(d.keys()) if hasattr(d, "keys") else "(无 keys)")
print("\n  顶层形状:")
shape(d, depth=1, max_depth=1)

if "novel" in d:
    inner = d["novel"]
    print("\n  ★ d['novel'] 的键:", sorted(inner.keys())[:40])
    print("  ★ d.novel.x_restrict =", repr(inner.get("x_restrict", "<无>")))
    print("  ★ d.novel.title =", repr(str(inner.get("title", ""))[:50]))
    print("  ★ d.novel.user 存在:", "user" in inner)
    if "user" in inner:
        print("  ★ d.novel.user.id =", inner["user"].get("id"))
        print("  ★ d.novel.user.name =", repr(inner["user"].get("name")))
        print("  ★ 头像 medium =",
              str((inner["user"].get("profile_image_urls") or {}).get("medium", ""))[:70])
    print("  ★ d.novel.image_urls.large =",
          str((inner.get("image_urls") or {}).get("large", ""))[:70])
    print("\n  ★ 属性式访问也对吗（ParsedJson 支持 .x 吗）:")
    print("     d.novel.x_restrict =", repr(getattr(d.novel, "x_restrict", "<取不到>")))
    print("     d.novel.title      =", repr(str(getattr(d.novel, "title", "<取不到>"))[:40]))

print("\n" + "=" * 64)
print("【3】novel_text(id) 返回结构（WebviewNovel）")
print("=" * 64)
tx = aapi.novel_text(target.id)
print("  类型:", type(tx).__name__)
print("  键:", sorted(tx.keys()) if hasattr(tx, "keys") else "(无 keys)")
print("  text 长度:", len(getattr(tx, "text", "") or ""))
print("  有 x_restrict 吗:", "x_restrict" in tx)

print("\n" + "=" * 64)
print("【4】user_novels(uid) 返回结构（author_info 的前提）")
print("=" * 64)
uid = (d.get("novel", {}).get("user") or {}).get("id")
if uid:
    un = aapi.user_novels(uid, filter="for_android")
    print("  用户 id:", uid)
    print("  顶层键:", sorted(un.keys()) if hasattr(un, "keys") else "(无 keys)")
    print("  有 user 吗:", "user" in un)
    if "user" in un:
        u = un["user"]
        print("  user.name =", repr(u.get("name")))
        print("  user.profile_image_urls.medium =",
              str((u.get("profile_image_urls") or {}).get("medium", ""))[:70])
    print("  novels 数量:", len(un.get("novels") or []))
    print("  第一篇 x_restrict:", repr((un.get("novels") or [{}])[0].get("x_restrict", "<无>")))
    print("  第一篇有没有 x_restrict 字段:",
          "x_restrict" in (un.get("novels") or [{}])[0])
