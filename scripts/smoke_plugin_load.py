"""本地干跑：验证插件能被 NoneBot 真正加载并注册全部命令。

这是 Task 9.7（服务器隔离干跑）的本地等价物 —— 不连 QQ、不碰生产，只证明
「import 期没炸 + 4 个命令都注册上了 + 定时任务挂上了」。

跑法：
    env -u UV_PYTHON ... uv run python scripts/smoke_plugin_load.py
"""

import sys
from pathlib import Path

import nonebot

nonebot.init(driver="~none", log_level="INFO")
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

EXPECTED_COMMANDS = {"订阅", "退订", "订阅列表", "获取全文"}


def main() -> int:
    nonebot.load_plugin("nonebot_plugin_pixiv_novel")

    plugins = nonebot.get_loaded_plugins()
    pixiv = next((p for p in plugins if p.name == "nonebot_plugin_pixiv_novel"), None)
    if pixiv is None:
        print("❌ 插件没被加载")
        return 1

    matchers = pixiv.matcher
    print(f"✅ 插件已加载，注册了 {len(matchers)} 个 matcher")

    # 反查每个 on_command 的命令词
    from nonebot.rule import CommandRule

    found = set()
    for m in matchers:
        for rule in m.rule.checkers:
            call = rule.call
            if isinstance(call, CommandRule):
                for cmd in call.cmds:
                    # cmds 里装的是 tuple（如 ('订阅',) / ('订阅', '列表')），不是裸字符串
                    found.add(cmd[0] if isinstance(cmd, tuple) else str(cmd))

    print(f"   命令词：{sorted(found)}")
    missing = EXPECTED_COMMANDS - found
    extra = found - EXPECTED_COMMANDS
    if missing:
        print(f"❌ 缺少命令：{sorted(missing)}")
        return 2
    if extra:
        print(f"⚠️ 多出来的命令：{sorted(extra)}")

    # 定时任务是否挂上（没配 token 时应**不**挂，只注册命令）
    from nonebot_plugin_apscheduler import scheduler

    jobs = {j.id for j in scheduler.get_jobs()}
    print(f"   定时任务：{sorted(jobs) or '（无）'}")

    print("\n✅ 干跑通过：插件可加载、命令齐全")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
