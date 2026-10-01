"""订阅列表渲染的**真实**端到端验证。

补的缺口：之前的 htmlkit 冒烟用的是**人造**头像（本地画的色块），
真实场景是「带 Referer 下 pixiv 头像 → 转 base64 data URI → htmlkit 渲染」。
而 fixture 里那个作者的头像是 `no_profile.png` 占位图，不能代表真实头像。

这里：找几个**真有自定义头像**的作者 → 走完整链路 → 输出图片 → 交给 OCR 验文字。

token 由脚本自己从 gppt 文件读。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

import nonebot

nonebot.init(driver="~none", log_level="ERROR")

from nonebot_plugin_pixiv_novel import avatars, render, store  # noqa: E402
from nonebot_plugin_pixiv_novel.pixiv_client import PixivClient  # noqa: E402

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
OUT = Path(__file__).parent.parent / "out"
OUT.mkdir(exist_ok=True)


async def main() -> int:
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    client = PixivClient(tok["refresh_token"], "")

    # 找几个真有头像的作者：从 R-18 搜索结果里挑不同作者的 id
    from pixivpy3 import AppPixivAPI
    api = AppPixivAPI()
    api.auth(refresh_token=tok["refresh_token"])
    r = api.search_novel(word="R-18", filter="for_android",
                         search_target="partial_match_for_tags")

    seen: list[int] = []
    for n in r.novels:
        uid = int(n.user.id)
        if uid not in seen:
            seen.append(uid)
        if len(seen) >= 8:
            break
    print(f"候选作者：{seen}\n")

    found: list[tuple[int, str, str]] = []      # (uid, name, avatar_url)
    for uid in seen:
        try:
            name, url = await client.author_info(uid)
        except Exception as e:
            print(f"  {uid}: 取信息失败 {type(e).__name__}")
            continue
        is_placeholder = "no_profile" in url
        print(f"  {uid}: name={name!r} 头像={'占位图' if is_placeholder else '真头像'}")
        if name and not is_placeholder:
            found.append((uid, name, url))
        if len(found) >= 3:
            break

    if not found:
        print("\n❌ 没找到带头像的作者（都用的占位图）—— 无法验证真实头像链路")
        return 1

    print(f"\n用这 {len(found)} 位作者验证真实头像：")
    for uid, name, url in found:
        print(f"  {uid} {name}  {url[:70]}")

    # ── 走真实链路：下头像 → base64 → 渲染 ────────────────────────
    store.init(OUT / "verify_list.sqlite3")
    avatars.init(OUT / "verify_avatars")

    for uid, name, url in found:
        store.subscribe(100, uid, baseline=0, author_name=name, author_avatar_url=url)
    rows = store.list_by_group(100)
    print(f"\n订阅表里 {len(rows)} 行")

    # 走 avatars.for_rows（内部带 Referer 下载 + 磁盘缓存 + base64 化）
    mapping = await avatars.for_rows(rows, fetch=client.fetch_image)
    print(f"头像映射：{len(mapping)} 个")
    for k, v in mapping.items():
        head = v[:40] if isinstance(v, str) else str(v)[:40]
        print(f"  {k}: {len(v) if isinstance(v,str) else '?'} 字符  {head}...")

    ok_all = all(isinstance(v, str) and v.startswith("data:image") for v in mapping.values())
    print(f"\n全部是 data URI: {ok_all}")

    # 渲染
    img = await render.render_subscription_list(rows, group_id=100)
    if img is None:
        print("❌ 渲染返回 None（htmlkit 不可用或出错）")
        return 1

    out_path = OUT / "real_list.png"
    out_path.write_bytes(img)
    print(f"✅ 渲染成功：{out_path}（{len(img)} 字节）")

    # ── 用 avatars 自动获取的路径再跑一遍（验证不传 fetch 时的默认路径）──
    img2 = await render.render_subscription_list(rows, group_id=100)
    print(f"重复渲染一致: {img2 == img}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
