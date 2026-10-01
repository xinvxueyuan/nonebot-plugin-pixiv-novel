"""确认 JsonDict 的行为 + tags 真实结构 + 解包后各字段。"""

import json
from pathlib import Path

from pixivpy3 import AppPixivAPI
from pixivpy3.utils import JsonDict

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
aapi = AppPixivAPI()
aapi.auth(refresh_token=tok["refresh_token"])

print("=== JsonDict 缺属性时的行为 ===")
jd = JsonDict({"a": 1})
for expr, fn in [
    ("jd['nope']", lambda: jd["nope"]),
    ("jd.nope", lambda: jd.nope),
    ("jd.get('nope', '默认')", lambda: jd.get("nope", "默认")),
    ("getattr(jd, 'nope', '默认')", lambda: getattr(jd, "nope", "默认")),
]:
    try:
        print(f"  {expr:30} → {fn()!r}")
    except Exception as e:
        print(f"  {expr:30} → 抛 {type(e).__name__}: {e}")

print("\n=== JsonDict 是 dict 子类吗 / isinstance 检查 ===")
print("  isinstance(jd, dict):", isinstance(jd, dict))
print("  hasattr(jd, 'keys'):", hasattr(jd, "keys"))

# 真实样本
r = aapi.search_novel(word="R-18", filter="for_android",
                      search_target="partial_match_for_tags")
item = r.novels[0]
print(f"\n=== tags 真实结构（id={item.id}）===")
print("  tags 类型:", type(item.tags).__name__, "长度", len(item.tags))
for i, tg in enumerate(item.tags[:3]):
    print(f"  [{i}] 类型={type(tg).__name__} 值={tg!r}")
    print(f"       isinstance dict={isinstance(tg, dict)}  "
          f"isinstance str={isinstance(tg, str)}  "
          f"hasattr .tag={hasattr(tg, 'tag')}")

print("\n=== 解包 novel_detail 后的完整可用字段 ===")
d = aapi.novel_detail(item.id)
nv = d["novel"]
print(f"  nv.title       = {str(nv.get('title'))[:44]!r}")
print(f"  nv.x_restrict  = {nv.get('x_restrict')!r}")
print(f"  nv.id          = {nv.get('id')!r}")
print(f"  nv.visible     = {nv.get('visible')!r}")
print(f"  nv.is_mypixiv_only = {nv.get('is_mypixiv_only')!r}")
print(f"  nv.tags 数量   = {len(nv.get('tags') or [])}")
for i, tg in enumerate((nv.get("tags") or [])[:4]):
    print(f"    tag[{i}] = {tg!r}  (类型 {type(tg).__name__})")
print(f"  nv.user.name   = {nv['user'].get('name')!r}")
print(f"  nv.user.id     = {nv['user'].get('id')!r}")
print(f"  nv.image_urls.large = {str((nv.get('image_urls') or {}).get('large'))[:64]!r}")

print("\n=== 属性式访问（解包后）===")
print(f"  nv.title      = {str(nv.title)[:36]!r}")
print(f"  nv.x_restrict = {nv.x_restrict!r}")
print(f"  nv.image_urls.large = {str(nv.image_urls.large)[:56]!r}")
print(f"  nv.user.name  = {nv.user.name!r}")
print(f"  nv.tags[0].tag = {nv.tags[0].tag!r}" if nv.tags else "  nv.tags 为空")

print("\n=== user_novels 条目（poller 用）是否有 x_restrict ===")
un = aapi.user_novels(nv.user.id, filter="for_android")
n0 = un.novels[0]
print(f"  novels[0].id         = {n0.id!r}")
print(f"  novels[0].x_restrict = {n0.x_restrict!r}")
print(f"  novels[0].title      = {str(n0.title)[:40]!r}")
print(f"  novels[0].user.name  = {n0.user.name!r}")
print(f"  novels[0].image_urls.large = {str(n0.image_urls.large)[:52]!r}")
print(f"  novels[0].tags 首项  = {n0.tags[0]!r}" if n0.tags else "  tags 空")
