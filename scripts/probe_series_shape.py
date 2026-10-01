"""只读侦察：pixiv `novel_series` 的真实响应结构（为「系列卡片」定字段）。

为什么必须实测：上次 `novel_detail` 的 `image_urls.large` 就是我猜错键名
（写成了 `image_urls.urls`），差点把「探针自己写错」报成生产 bug。
pixivpy3 的 JsonDict 对**缺失键返回 None 而不抛异常**，所以猜错字段是**静默失效**。

token 只从本地 gppt token 文件读，**不进命令行、不进日志**。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pixivpy3 import AppPixivAPI

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"


def _retry(fn, tries=4, delay=3.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"    ⚠️ {i + 1}/{tries} 失败: {type(e).__name__}: {str(e)[:90]}")
            if i + 1 < tries:
                time.sleep(delay)
    raise last


def shape(obj, depth=2, max_keys=14):
    """打印一个响应的**结构**（键 + 类型 + 截断的短值），不打印长文本。"""
    if depth <= 0:
        return type(obj).__name__
    if isinstance(obj, dict):
        out = {}
        for k in list(obj.keys())[:max_keys]:
            v = obj[k]
            if isinstance(v, dict):
                out[k] = shape(v, depth - 1, max_keys)
            elif isinstance(v, list):
                out[k] = [shape(v[0], depth - 1, max_keys)] if v else []
            else:
                out[k] = f"{type(v).__name__}:{str(v)[:40]}"
        if len(obj.keys()) > max_keys:
            out["...其他键"] = f"还有 {len(obj.keys()) - max_keys} 个"
        return out
    return f"{type(obj).__name__}"


def main() -> int:
    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    api = AppPixivAPI()          # 本机走 TUN 直连，不设代理
    api.auth(refresh_token=tok["refresh_token"])
    print("✅ 鉴权成功\n")

    # 1) 找一篇「属于某个系列」的作品
    series_id = None

    print("=== 从 novel_new() 里找带 series 的作品 ===")
    try:
        res = _retry(lambda: api.novel_new())
        novels = getattr(res, "novels", None) or []
        print(f"  新作 {len(novels)} 篇")
        for n in novels[:30]:
            s = getattr(n, "series", None)
            sid = getattr(s, "id", None) if s is not None else None
            if sid:
                series_id = int(sid)
                print(f"  ★ 找到：作品 {n.id} 「{str(n.title)[:24]}」→ series_id={series_id}")
                break
    except Exception as e:
        print(f"  novel_new 失败: {type(e).__name__}: {str(e)[:100]}")

    if series_id is None:
        print("\n  novel_new 里没找到带系列的作品，改试现有 fixture 里的作者作品")
        try:
            res = _retry(lambda: api.user_novels(61943687))
            for n in getattr(res, "novels", None) or []:
                s = getattr(n, "series", None)
                sid = getattr(s, "id", None) if s is not None else None
                if sid:
                    series_id = int(sid)
                    print(f"  ★ 找到：作品 {n.id} → series_id={series_id}")
                    break
            if series_id is None:
                print("  该作者作品都不属于系列")
        except Exception as e:
            print(f"  失败: {type(e).__name__}: {str(e)[:100]}")

    if series_id is None:
        print("\n❌ 没找到任何系列 ID —— 无法录 fixture。可以手工给一个 series 链接再试。")
        return 1

    # 2) 系列详情的真实结构
    print(f"\n=== novel_series({series_id}) 顶层结构 ===")
    sres = _retry(lambda: api.novel_series(series_id))
    print("  顶层键:", sorted(sres.keys()) if hasattr(sres, "keys") else type(sres))
    print("  结构:", json.dumps(shape(sres, depth=2), ensure_ascii=False, indent=2)[:1800])

    # 3) 系列里第一篇作品（用于「系列卡片」取首篇）
    novels_in_series = getattr(sres, "novels", None)
    print(f"\n  novels 字段类型: {type(novels_in_series).__name__}, "
          f"长度={len(novels_in_series) if novels_in_series else 0}")
    if novels_in_series:
        first = novels_in_series[0]
        print(f"  第一篇: id={getattr(first, 'id', None)} "
              f"title={str(getattr(first, 'title', ''))[:30]!r} "
              f"x_restrict={getattr(first, 'x_restrict', None)} "
              f"tags={type(getattr(first, 'tags', None)).__name__} "
              f"image_urls={type(getattr(first, 'image_urls', None)).__name__}")
        print("  第一篇的结构:", json.dumps(shape(first, depth=2), ensure_ascii=False)[:900])
        print("  系列元信息:",
              json.dumps({k: str(v)[:60] for k, v in sres.items() if k != "novels"},
                         ensure_ascii=False)[:600])

        # 首篇的详情（系列列表里的对象字段可能不全，要对比）
        d = _retry(lambda: api.novel_detail(int(first.id)))
        nv = d["novel"] if isinstance(d, dict) and "novel" in d else d
        print(f"\n  第一篇 novel_detail 解包后: x_restrict={nv.get('x_restrict')} "
              f"image_urls.large={'有' if getattr(nv.get('image_urls'), 'large', None) else '无'} "
              f"series={(nv.get('series') or {}).get('id') if nv.get('series') else None}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
