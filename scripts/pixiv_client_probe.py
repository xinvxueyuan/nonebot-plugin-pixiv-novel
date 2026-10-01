"""给 verify_proxy_path.py 用的探针：往指定代理发一次真实 pixiv 请求。

**必须用真 token**（pixiv 的 API 需要鉴权），但 token 由脚本自己从 gppt
token 文件读，不经命令行/环境变量/日志。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

TOKEN_FILE = Path.home() / ".config" / "gppt" / "default.token.json"

# 插件包 import 会走 __init__.py（模块级 on_command/require），必须先初始化 NoneBot
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

import nonebot  # noqa: E402

nonebot.init(driver="~none", log_level="ERROR")


def run_probe(proxy: str) -> str:
    from nonebot_plugin_pixiv_novel.pixiv_client import PixivClient

    tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    client = PixivClient(tok["refresh_token"], proxy)
    print(f"用代理 {proxy} 发起真实 pixiv 请求 …")
    try:
        novels = asyncio.run(client.user_novels(61943687))
        print(f"  结果：拿到 {len(novels)} 篇作品")
        return f"ok({len(novels)})"
    except Exception as e:
        # 桩代理会拒绝连接，所以这里**报错才是预期的**；
        # 关键看桩代理有没有收到 CONNECT。
        print(f"  预期内失败：{type(e).__name__}: {str(e)[:90]}")
        return f"failed({type(e).__name__})"
