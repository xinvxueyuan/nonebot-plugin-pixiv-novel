"""验证：htmlkit 加载失败时，pixiv 插件还能不能活下来。

做法 = 把 htmlkit 的包目录临时改名（模拟 C++ 扩展加载失败 / 库缺失 / 没装），
在子进程里跑两件事，再改回来。**不碰任何生产环境**。

验两半：
  ① 插件本身仍能加载、4 个命令仍注册（否则连订阅/退订都不能用）
  ② `render.render_subscription_list()` 返回 None 而不是抛异常
     —— 这是「订阅列表」回退纯文本的前提

⚠️ require() 内部会 load_plugin() 并把异常抛出来，所以初稿的模块级
`require("nonebot_plugin_htmlkit")` 会让整个插件加载失败（4 个命令全消失）。
本脚本就是那次的回归验证。
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SP = next((ROOT / ".venv" / "Lib" / "site-packages").glob("nonebot_plugin_htmlkit"))
BACKUP = SP.with_name("nonebot_plugin_htmlkit__DISABLED")

PROBE = r"""
import asyncio, sys
from pathlib import Path
import nonebot

nonebot.init(driver="~none", log_level="INFO")
sys.path.insert(0, str(Path(r"{root}") / "src" / "plugins"))

# ⚠️ 必须让 load_plugin 来完成 import：先自己 import 再 load_plugin 会撞上
# NoneBot 的保护 "Module ... is not loaded as a plugin! Make sure not to import it before loading."
nonebot.load_plugin("nonebot_plugin_pixiv_novel")

plug = nonebot.get_plugin("nonebot_plugin_pixiv_novel")
print("__PROBE_PLUGIN_OK__", len(plug.matcher))

from nonebot_plugin_pixiv_novel import render
rows = [{"author_id": 1, "author_name": "甲", "author_avatar_url": "",
         "created_at": 0, "last_seen": 0}]
result = asyncio.run(render.render_subscription_list(rows, group_id=1))
print("__PROBE_RENDER__", repr(result))
""".replace("{root}", str(ROOT).replace("\\", "\\\\"))


def main() -> int:
    if BACKUP.exists():
        print(f"⚠️ 残留备份目录，先清掉：{BACKUP}")
        shutil.rmtree(BACKUP)

    print(f"临时禁用 htmlkit：{SP.name} → {BACKUP.name}\n")
    SP.rename(BACKUP)
    try:
        r = subprocess.run(
            [sys.executable, "-c", PROBE],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    finally:
        BACKUP.rename(SP)
        print("（已恢复 htmlkit）\n")

    out = (r.stdout or "") + (r.stderr or "")

    plugin_ok = "__PROBE_PLUGIN_OK__" in out
    n_matchers = ""
    if plugin_ok:
        line = next(ln for ln in out.splitlines() if "__PROBE_PLUGIN_OK__" in ln)
        n_matchers = line.split("__PROBE_PLUGIN_OK__")[1].strip()

    render_line = next((ln for ln in out.splitlines() if "__PROBE_RENDER__" in ln), "")
    render_none = render_line.endswith("None")

    print("=" * 62)
    print(f"① 插件仍能加载     : {'✅' if plugin_ok else '❌'}  matcher 数 = {n_matchers or '-'}")
    print(f"② render 返回 None : {'✅' if render_none else '❌'}  {render_line or '(探测行缺失)'}")
    print("=" * 62)

    if plugin_ok and render_none:
        print("\n✅ htmlkit 挂掉 → 插件照常可用，「订阅列表」走纯文本回退")
        return 0
    print("\n❌ 降级链路断了，摘录日志：")
    for ln in out.splitlines():
        if any(k in ln for k in ("ERROR", "Traceback", "Error", "WARNING", "失败")):
            print("  ", ln.strip())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
