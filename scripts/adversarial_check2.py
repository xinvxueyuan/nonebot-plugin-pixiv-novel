"""并发与边界的对抗式复查（第三轮）。

挖三处测试覆盖最薄的路径，每处都是**生产会真发生**的场景：

  A. store 并发写 —— apscheduler 轮询任务与命令处理器会在 await 点交错，
     各自开独立 sqlite3 连接。默认 journal 模式下并发写可能
     `database is locked`（默认只等 5 秒）。轮询里这个异常会让**整轮白跑**。
  B. 满员模板渲染 —— MAX_ITEMS=50，从没在真 htmlkit 上渲染过 50 条。
  C. 长正文发文件 —— `_send_file` 的落盘逻辑（含超长文件名、路径安全）。

跑法：
    env -u UV_PYTHON ... uv run python scripts/adversarial_check2.py
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

import nonebot

nonebot.init(driver="~none", log_level="ERROR")

from nonebot_plugin_pixiv_novel import handlers, render, store  # noqa: E402

OUT = Path(__file__).parent.parent / "out"
OUT.mkdir(exist_ok=True)
fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fails.append(f"{label} {detail}")
    print(f"  {'✅' if ok else '❌'} {label}" + (f"  —— {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════
print("=== A. store 并发写（apscheduler + 命令处理器交错）===")

db = OUT / "concurrency.sqlite3"
if db.exists():
    db.unlink()
store.init(db)

ERRORS: list[str] = []
DONE = [0]

N_THREADS = 8
N_OPS = 30


def writer(tid: int) -> None:
    """模拟一个「群」不停订阅/推进水位。"""
    for i in range(N_OPS):
        try:
            store.subscribe(1000 + tid, 5000 + tid, baseline=i)
            store.set_last_seen(1000 + tid, 5000 + tid, 100_000 + i)
            store.list_by_group(1000 + tid)
            store.all_authors()
            DONE[0] += 1
        except Exception as e:
            ERRORS.append(f"{type(e).__name__}: {e}")


threads = [threading.Thread(target=writer, args=(t,)) for t in range(N_THREADS)]
t0 = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.time() - t0

print(f"  {N_THREADS} 线程 × {N_OPS} 轮 = {N_THREADS * N_OPS} 次操作，耗时 {elapsed:.2f}s")
check("并发写没有异常", not ERRORS,
      f"{len(ERRORS)} 个错误，前 3 个：{ERRORS[:3]}" if ERRORS else "")
locked = [e for e in ERRORS if "locked" in e.lower()]
if locked:
    print(f"  ⚠️ 其中 {len(locked)} 个是 database is locked")
check("全部写操作落库", DONE[0] == N_THREADS * N_OPS, f"完成 {DONE[0]}")
rows = store.list_by_group(1000)
check("数据一致（每线程 1 条）", len(rows) == 1, f"group 1000 有 {len(rows)} 条")
check("所有作者都记下了", len(store.all_authors()) == N_THREADS,
      f"{len(store.all_authors())} 个作者")


# ══════════════════════════════════════════════════════════════
print("\n=== B. 满员模板渲染（50 条，真 htmlkit）===")

db2 = OUT / "full_list.sqlite3"
if db2.exists():
    db2.unlink()
store.init(db2)

N = 50
for i in range(N):
    store.subscribe(1094538078, 60000 + i, baseline=0,
                    author_name=f"作者{i}号" if i % 7 else "",
                    author_avatar_url="" if i % 5 == 0 else f"https://i.pximg.net/a{i}.jpg")
rows = store.list_by_group(1094538078)
check(f"造足 {N} 条订阅", len(rows) == N, f"实际 {len(rows)}")

show_all = store.list_by_group(1094538078)


async def _render_full():
    return await render.render_subscription_list(show_all, group_id=1094538078)


img = asyncio.run(_render_full())
check("50 条渲染成功（返回 bytes）", isinstance(img, bytes) and len(img) > 5000,
      f"{len(img) if img else 0} 字节")
if img:
    p = OUT / "full_list.png"
    p.write_bytes(img)
    import io

    from PIL import Image
    im = Image.open(io.BytesIO(img))
    print(f"     尺寸 {im.size}  已存 {p.name}")
    check("图片高度合理（50 条不该被截成一条）", im.size[1] > 800, f"高 {im.size[1]}px")

    # 超过 MAX_ITEMS 时的截断标记
    check("总数 50 未触发截断", render.build_context(show_all, group_id=1)["truncated"] == 0)

ctx51 = render.build_context([{"author_id": i, "author_name": "x", "author_avatar_url": "",
                              "created_at": 0, "last_seen": 0} for i in range(60)],
                            group_id=1)
check("超过 50 条时 truncated 正确", ctx51["truncated"] == 10, f"truncated={ctx51['truncated']}")
check("渲染条数封顶 50", len(ctx51["items"]) == 50, f"{len(ctx51['items'])} 条")


# ══════════════════════════════════════════════════════════════
print("\n=== C. 长正文落盘（_send_file 的写文件部分）===")

LONG = "长" * 200_000                      # 单条消息发不出去，必须走文件
name = handlers.safe_filename(29277050, "むっちむち痴女シーフお姉さんの色気に乗せられて")
check("文件名安全且非空", name.endswith(".txt") and ".." not in name and "/" not in name, name)

# 恶意标题
for bad in ("../../etc/passwd", "a\nb", "con:*?\"<>|", ""):
    n = handlers.safe_filename(1, bad)
    ok = "/" not in n and "\\" not in n and "\n" not in n and n.endswith(".txt")
    check(f"恶意/异常标题 {bad!r} → 安全", ok, n)

# 真写一次长文本（用 temp 目录，不碰生产缓存）
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    fp = Path(td) / name
    fp.write_text(LONG, encoding="utf-8")
    back = fp.read_text(encoding="utf-8")
    check("长正文写盘后能原样读回", back == LONG, f"{len(LONG)} 字")
    check("文件字节数合理", fp.stat().st_size > 100_000, f"{fp.stat().st_size} 字节")

# QQ 单条消息长度阈值（pixiv_text_max_chars 默认值应低于常见上限）
from nonebot_plugin_pixiv_novel.config import Config  # noqa: E402

cfg = Config()
print(f"     默认 pixiv_text_max_chars = {cfg.pixiv_text_max_chars}")
# 注：早先这里断言 <3000 是**我自己的假设**，不是用户要求 —— 已改正。
# 真正要保证的是「超限时能可靠降级」，由 handlers.deliver_novel_text 的三级降级覆盖，
# 所以这里只断言配置值本身合理（有下限、不是 0）。
check("阈值配置合理（>=100）", cfg.pixiv_text_max_chars >= 100, str(cfg.pixiv_text_max_chars))
check("超限有降级路径（deliver_novel_text 存在）",
      callable(getattr(handlers, "deliver_novel_text", None)))


# ══════════════════════════════════════════════════════════════
print("\n=== D. store 的边界（顺带查）===")
store.init(db2)
# 超长文本 / 特殊字符的作者名
weird = "名前\u2028with\u2029line\u0000sep"
store.subscribe(1, 70001, baseline=0, author_name=weird)
r = next(x for x in store.list_by_group(1) if x["author_id"] == 70001)
check("特殊字符作者名可存可读", r["author_name"] == weird, repr(r["author_name"][:30]))

# set_last_seen 只前进不能后退
store.set_last_seen(1, 70001, 500)
store.set_last_seen(1, 70001, 100)          # 倒退 → 应被 WHERE 条件挡住
r2 = next(x for x in store.list_by_group(1) if x["author_id"] == 70001)
check("高水位不能倒退", r2["last_seen"] == 500, f"last_seen={r2['last_seen']}")

# 退订不存在的
check("退订不存在的订阅返回 False", store.unsubscribe(1, 999999) is False)
check("重复 subscribe 第二次返回 False",
      store.subscribe(1, 70002, baseline=0) is True
      and store.subscribe(1, 70002, baseline=0) is False)

# 重复订阅不覆盖已有作者信息
store.subscribe(1, 70003, baseline=0, author_name="原名")
store.subscribe(1, 70003, baseline=0, author_name="新名")
r3 = next(x for x in store.list_by_group(1) if x["author_id"] == 70003)
check("重复订阅不覆盖作者名", r3["author_name"] == "原名", r3["author_name"])

# 空库
store.init(OUT / "empty.sqlite3")
check("空库 all_authors 返回 []", store.all_authors() == [])
check("空库 list_by_group 返回 []", store.list_by_group(1) == [])


print("\n" + "=" * 64)
if fails:
    print(f"❌ {len(fails)} 项有问题：")
    for f in fails:
        print("   -", f)
    raise SystemExit(1)
print("✅ 第三轮对抗式复查全过")
