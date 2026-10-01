"""把真实 pixiv 响应的**形状**录成测试 fixture。

为什么不手写假数据：我最初单测里的 FakeNovel 是**凭空编的**（字段全在顶层），
所以 102 个测试全绿也照样漏掉「novel_detail 响应顶层是 {"novel": {...}}」这个真 bug。
录真实响应 + 让 fixture 复刻 JsonDict 语义（缺键返 None 而不抛），
才能让这类结构性错误在单测里就暴露出来。

只保留测试断言需要的字段，**不含 token**。
"""

import json
from pathlib import Path

from pixivpy3 import AppPixivAPI


def _retry(fn, tries=5, delay=3.0):
    """pixiv 经代理会偶发 SSL EOF / 连接重置 —— 实测几分钟内遇到两次。
    录制 fixture 必须能扛住，否则白跑。"""
    import time
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"    ⚠️ 第 {i+1}/{tries} 次失败：{type(e).__name__}: {str(e)[:80]}")
            if i + 1 < tries:
                time.sleep(delay)
    raise last


TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
OUT = Path(__file__).parent.parent / "tests" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)

tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
aapi = AppPixivAPI()
aapi.auth(refresh_token=tok["refresh_token"])   # auth 内部自己重试

r = _retry(lambda: aapi.search_novel(
    word="R-18", filter="for_android", search_target="partial_match_for_tags"))
r18_item = r.novels[0]
r18_id = int(r18_item.id)



def pick(d, keys):
    return {k: d.get(k) for k in keys if k in d}


# ── fixture 1: novel_detail 的**真实外层形状** {"novel": {...}} ──
NV_KEYS = ["id", "title", "x_restrict", "visible", "is_mypixiv_only", "tags",
           "user", "image_urls", "create_date", "text_length", "total_view",
           "total_bookmarks", "series", "caption", "novel_ai_type"]
detail = _retry(lambda: aapi.novel_detail(r18_id))
inner = detail["novel"]
fixture_detail = {"novel": pick(inner, NV_KEYS)}
fixture_detail["novel"]["user"] = pick(inner["user"], ["id", "name", "account", "profile_image_urls"])
fixture_detail["novel"]["image_urls"] = pick(inner["image_urls"], ["large", "medium", "square_medium"])
fixture_detail["novel"]["tags"] = [pick(t, ["name", "translated_name"]) for t in inner.tags[:5]]

(OUT / "novel_detail_r18.json").write_text(
    json.dumps(fixture_detail, ensure_ascii=False, indent=2), encoding="utf-8")

# ── fixture 2: user_novels（poller 播种用）──
uid = int(inner["user"]["id"])
un = _retry(lambda: aapi.user_novels(uid, filter="for_android"))
fixture_un = {
    "user": pick(un["user"], ["id", "name", "account", "profile_image_urls"]),
    "novels": [{
        **pick(n, ["id", "title", "x_restrict", "visible", "is_mypixiv_only", "create_date"]),
        "user": pick(n["user"], ["id", "name"]),
        "image_urls": pick(n["image_urls"], ["large", "medium"]),
        "tags": [pick(t, ["name"]) for t in (n.tags or [])[:4]],
    } for n in un.novels[:3]],
}
fixture_un["user"]["profile_image_urls"] = pick(
    un["user"].get("profile_image_urls") or {}, ["large", "medium"])
(OUT / "user_novels.json").write_text(
    json.dumps(fixture_un, ensure_ascii=False, indent=2), encoding="utf-8")

# ── fixture 3: novel_text（全文）──
tx = _retry(lambda: aapi.novel_text(r18_id))
fixture_tx = {"text": (tx.text or "")[:400], "title": tx.get("title"),
              "id": tx.get("id"), "userId": tx.get("userId")}
(OUT / "novel_text.json").write_text(
    json.dumps(fixture_tx, ensure_ascii=False, indent=2), encoding="utf-8")

print("已写入 fixture：")
for f in sorted(OUT.glob("*.json")):
    print(f"  {f.name}  {f.stat().st_size} 字节")
print("\nnovel_detail 外层键:", sorted(fixture_detail.keys()), "← 关键：字段在 .novel 里")
print("x_restrict:", fixture_detail["novel"]["x_restrict"])
print("tags 首项键:", sorted(fixture_detail["novel"]["tags"][0].keys()), "← 是 name，不是 tag")
