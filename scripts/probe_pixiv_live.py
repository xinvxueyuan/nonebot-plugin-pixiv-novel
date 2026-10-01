"""Task 0.3 —— pixiv R18 可见性 + 封面下载 实测。

**token 处理**：脚本自己从 gppt 的 token 文件读 refresh_token，
不经命令行参数、不经环境变量、不打印。pixiv 凭据只进内存。

验四件事（每件都是计划里标注「不通须停下问用户」的硬约束）：
  A. refresh_token 能不能换到 access_token
  B. 普通小说：novel_detail / novel_text / 封面下载
  C. R18 小说：能不能搜到、x_restrict 是不是 1、novel_text 能不能拿到正文
  D. 封面 i.pximg.net 不带 Referer 是不是 403（计划里的核心结论）

跑法：
    env -u UV_PYTHON ... uv run python scripts/probe_pixiv_live.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from pixivpy3 import AppPixivAPI

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"
PROXY = "http://127.0.0.1:7897"          # 本机 Clash mixed-port（服务器上是 1081）
UA = "PixivAndroidApp/5.0.234 (Android 12; Pixel 6)"

results: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  —— {detail}" if detail else ""))


def main() -> int:
    if not TOKEN_FILE.exists():
        print(f"❌ token 文件不存在：{TOKEN_FILE}")
        return 2
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    refresh_token = tok.get("refresh_token")
    if not refresh_token:
        print(f"❌ token 文件里没有 refresh_token，键有：{sorted(tok)}")
        return 2
    print(f"token 文件：{TOKEN_FILE}")
    print(f"refresh_token：已读到（长度 {len(refresh_token)}，不打印）\n")

    # 代理通道自动选择：本机 Clash 是 TUN 模式，直连即已被透明接管；
    # 但服务器上没 TUN，必须显式走 SYSTEM_PROXY。所以两条都试，用能通的那条。
    # ⚠️ 实测过一次 7897「积极拒绝」的瞬时故障，所以必须容错而不是写死。
    global PROXY
    proxied = AppPixivAPI(proxies={"http": PROXY, "https": PROXY})
    direct = AppPixivAPI()
    aapi = None
    for label, cand in (("显式代理 " + PROXY, proxied), ("直连（TUN 透明接管）", direct)):
        try:
            cand.auth(refresh_token=refresh_token)
            aapi = cand
            PROXY = PROXY if cand is proxied else None
            rec(f"通道可用：{label}", True, f"user_id={cand.user_id}")
            break
        except Exception as e:
            rec(f"通道可用：{label}", False, f"{type(e).__name__}: {str(e)[:110]}")
    if aapi is None:
        print("\n❌ 两条通道都不通，需要用户介入")
        return 1

    # ── A. 换 access_token ────────────────────────────────────────
    print("=== A. refresh_token → access_token ===")
    try:
        aapi.auth(refresh_token=refresh_token)
        rec("auth() 成功", bool(aapi.access_token),
            f"access_token 长度 {len(aapi.access_token or '')}, user_id={aapi.user_id}")
    except Exception as e:
        rec("auth() 成功", False, f"{type(e).__name__}: {e}")
        print("\n❌ 认证失败，后面全部跳过 —— 需要用户介入")
        return 1

    # ── 找样本 ────────────────────────────────────────────────────
    # ⚠️ pixivpy3 **没有** novel_ranking（只有 illust_ranking）—— 实测确认。
    # 所以 R18 样本只能靠搜索：pixiv 会给 x_restrict=1 的小说完自动打上 R-18 标签。
    print("\n=== 取样 ===")
    normal_id = r18_id = None

    try:
        r = aapi.novel_new(filter="for_android")
        got = [n for n in (r.novels or []) if n.id]
        if got:
            normal_id = int(got[0].id)
            rec("novel_new() 取普通样本", True, f"id={normal_id}")
    except Exception as e:
        rec("novel_new() 取普通样本", False, f"{type(e).__name__}: {e}")

    try:
        r = aapi.search_novel(word="R-18", filter="for_android", search_target="partial_match_for_tags")
        got = [n for n in (r.novels or []) if n.id]
        if got:
            r18_id = int(got[0].id)
            rec("search_novel('R-18') 取 R18 样本", True,
                f"id={r18_id} 共 {len(got)} 篇 ← R18 小说搜得到")
        else:
            rec("search_novel('R-18')", False, "返回 0 篇（账号地区可能不含 R18）")
    except Exception as e:
        rec("search_novel('R-18')", False, f"{type(e).__name__}: {e}")

    # ── B/C. 逐样本详验 ───────────────────────────────────────────
    for label, nid in (("普通", normal_id), ("R18", r18_id)):
        if nid is None:
            print(f"\n=== B/C. {label}小说 ===\n  ⏭ 无样本，跳过")
            continue
        print(f"\n=== {label}小说 id={nid} ===")

        detail = None
        try:
            detail = aapi.novel_detail(nid)
            xr = int(getattr(detail, "x_restrict", 0) or 0)
            rec("novel_detail()", True,
                f"x_restrict={xr} 标题={ (detail.title or '')[:28]!r}")
            if label == "普通":
                rec("普通作品 x_restrict=0", xr == 0, f"实际 {xr}")
            else:
                rec("R18 作品 x_restrict=1", xr == 1, f"实际 {xr} ← 账号地区是否含 R18 的直接证据")
        except Exception as e:
            rec("novel_detail()", False, f"{type(e).__name__}: {e}")

        # 正文（本插件「获取全文」的核心）
        try:
            text = aapi.novel_text(nid)
            body = getattr(text, "text", "") or ""
            rec("novel_text() 拿到正文", bool(body.strip()),
                f"{len(body)} 字，开头 {body.strip()[:24]!r}")
            if label == "R18":
                rec("R18 正文非空", bool(body.strip()),
                    "→ R18 全文可取，插件核心能力成立" if body.strip() else
                    "→ R18 正文为空 = 拿不到，须停下问用户")
        except Exception as e:
            rec("novel_text()", False, f"{type(e).__name__}: {e}")
            if label == "R18":
                print("     ⚠️ R18 正文取不到 —— 这是需求层阻塞，须停下问用户")

        # 封面：带 Referer vs 不带 Referer
        try:
            url = detail.image_urls.large
        except Exception:
            url = None
        if url:
            hdr = {"Referer": "https://www.pixiv.net/", "User-Agent": UA}
            try:
                r = httpx.get(url, headers=hdr, proxy=PROXY, timeout=25, follow_redirects=True)
                rec("封面 带 Referer", r.status_code == 200,
                    f"HTTP {r.status_code}，{len(r.content)} 字节")
            except Exception as e:
                rec("封面 带 Referer", False, f"{type(e).__name__}: {e}")

            if label == "普通":
                try:
                    r2 = httpx.get(url, headers={"User-Agent": UA}, proxy=PROXY,
                                   timeout=25, follow_redirects=True)
                    rec("封面 不带 Referer 应当 403（计划里的核心结论）",
                        r2.status_code == 403, f"实际 HTTP {r2.status_code}")
                except Exception as e:
                    rec("封面 不带 Referer", False, f"{type(e).__name__}: {e}")

        # 作者信息 —— ⚠️ 这里验的是插件 client.author_info() 依赖的**关键假设**：
        # 它用 user_novels(...).user 取名字和头像。若该字段不存在，
        # 订阅列表的作者名/头像会**永远为空**，且不会报错（静默功能失效）。
        try:
            uid = int(detail.user.id)
            un = aapi.user_novels(uid, filter="for_android")
            u = getattr(un, "user", None)
            if u is None:
                rec("user_novels().user 存在（author_info 的前提）", False,
                    f"响应顶层字段={sorted(un.keys())[:12]} ← 插件取作者名会永远拿到空串")
            else:
                name = getattr(u, "name", "")
                avatar = getattr(getattr(u, "profile_image_urls", None), "medium", "")
                rec("user_novels().user 存在（author_info 的前提）", bool(name),
                    f"name={name!r}")
                rec("头像 URL 可取", bool(avatar), f"{str(avatar)[:64]}...")
                # 头像也要带 Referer 才能下（i.pximg.net）
                if avatar:
                    try:
                        ra = httpx.get(avatar, headers={"Referer": "https://www.pixiv.net/",
                                                        "User-Agent": UA},
                                       proxy=PROXY, timeout=25, follow_redirects=True)
                        rec("头像下载（带 Referer）", ra.status_code == 200,
                            f"HTTP {ra.status_code}，{len(ra.content)} 字节")
                    except Exception as e:
                        rec("头像下载（带 Referer）", False, f"{type(e).__name__}: {e}")
            rec("user_novels() 返回作品（订阅播种用）", bool(getattr(un, "novels", None)),
                f"作者 {uid} 有 {len(getattr(un, 'novels', None) or [])} 篇")
        except Exception as e:
            rec("作者信息", False, f"{type(e).__name__}: {e}")

    # ── 汇总 ──────────────────────────────────────────────────────
    print("\n" + "=" * 66)
    bad = [r for r in results if not r[1]]
    print(f"共 {len(results)} 项：✅ {len(results) - len(bad)}  ❌ {len(bad)}")
    for name, _, detail in bad:
        print(f"  ❌ {name}  {detail}")
    print("=" * 66)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
