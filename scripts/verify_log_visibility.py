"""验证插件日志在生产环境**真的可见**（能被 loguru 收到 → 会进 journal）。

背景（实测）：
    NoneBot 把 stdlib `logging` 的 root logger 钉在 WARNING，
    并且 root **没有任何 handler**：

        root level: 30        ← INFO 直接被 isEnabledFor 挡掉
        root handlers: []     ← 就算放行也没有去处（只剩 lastResort 兜 WARNING+）

    于是插件里所有 `logger.info(...)` 在生产**一条都看不到**：
    启动行「pixiv-novel 已启动：代理=… 间隔=…」、推送通知、交付方式全都消失。
    只有 `logger.warning` 靠 logging 的 lastResort 落到 stderr 才进 journal。

修法：在 `__init__.py` 挂上 NoneBot 自带的 `LoguruHandler`（stdlib→loguru 官方桥）
并把本 logger 级别设为 INFO。本脚本证明修法生效，且不引入重复行。

跑法：
    cd /c/dev/nonebot-plugin-pixiv-novel
    env -u UV_PYTHON -u UV_PROJECT_ENVIRONMENT -u SSL_CERT_FILE -u VIRTUAL_ENV \
        -u PYTHONPATH uv run python scripts/verify_log_visibility.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src" / "plugins"))

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402
from nonebot.log import LoguruHandler  # noqa: E402

# 与 conftest 一致：内置 none 驱动，不需要真连接
nonebot.init(driver="~none", log_level="INFO")
nonebot.get_driver().register_adapter(Adapter)

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    print(f"  {'✅' if cond else '❌'} {label}" + (f"  —— {detail}" if detail else ""))
    ok = ok and cond


print("=== 1. 生产真实情况：裸 stdlib logger 的 INFO 会丢 ===")
probe = logging.getLogger("pixiv_probe_bare")
check(
    "root logger 级别是 WARNING",
    logging.getLogger().level == logging.WARNING,
    f"{logging.getLogger().level}",
)
check(
    "root 没有任何 handler（所以 INFO 无处可去）",
    logging.getLogger().handlers == [],
    f"{logging.getLogger().handlers}",
)
check("裸 logger 的 INFO 被挡", not probe.isEnabledFor(logging.INFO))

print("\n=== 2. 导入插件后，logger 已接好（修法生效）===")
import nonebot_plugin_pixiv_novel as pkg  # noqa: E402,F401

plugin_logger = logging.getLogger("nonebot_plugin_pixiv_novel")
check(
    "级别已降到 INFO",
    plugin_logger.getEffectiveLevel() == logging.INFO,
    str(plugin_logger.getEffectiveLevel()),
)
check(
    "已挂上 NoneBot 的 LoguruHandler",
    any(isinstance(h, LoguruHandler) for h in plugin_logger.handlers),
    str(plugin_logger.handlers),
)
check(
    "LoguruHandler 只有 1 个（重复 import 不会叠加）",
    sum(isinstance(h, LoguruHandler) for h in plugin_logger.handlers) == 1,
)

print("\n=== 3. 端到端：INFO 真能被 loguru 收到（→ 会进 journal）===")
captured: list[str] = []

# 直接往 loguru 加一个 sink，模拟 journal 那条链路
from loguru import logger as loguru_logger  # noqa: E402

sink_id = loguru_logger.add(lambda m: captured.append(m), level="INFO", format="{message}")

plugin_logger.info("INFO_MARKER_应当被收到")
plugin_logger.warning("WARNING_MARKER_应当被收到且只有一条")
plugin_logger.debug("DEBUG_MARKER_不应被收到")

loguru_logger.remove(sink_id)

check(
    "INFO 被 loguru 收到",
    any("INFO_MARKER" in m for m in captured),
    f"收到 {len(captured)} 条",
)
check(
    "WARNING 只出现一次（没有与 lastResort 重复）",
    sum("WARNING_MARKER" in m for m in captured) == 1,
)
check(
    "DEBUG 仍被过滤（级别没被放宽过头）",
    not any("DEBUG_MARKER" in m for m in captured),
)

print("\n=== 4. 6 个模块共用同一 logger 名字（一处设置全覆盖）===")
names = set()
for mod in ("avatars", "render", "poller", "pixiv_client", "handlers"):
    m = sys.modules.get(f"nonebot_plugin_pixiv_novel.{mod}")
    if m is not None:
        names.add(getattr(m, "logger", None) and m.logger.name)
check(
    "各子模块的 logger 名都是 nonebot_plugin_pixiv_novel",
    names <= {"nonebot_plugin_pixiv_novel"},
    str(sorted(n for n in names if n)),
)

print("\n" + "=" * 60)
print("✅ 日志可见性验证全过" if ok else "❌ 有检查未通过")
raise SystemExit(0 if ok else 1)
