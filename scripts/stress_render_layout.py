"""用「最坏情况」数据渲染订阅列表，检查会不会被卡片裁掉。

要区分的两件事：
  · 页脚 `.foot` 是 `text-align: right`，本来就贴着内区右缘 —— 那 2px 是它，正常
  · 真正的风险是**最长的那行内容**（长作者名 / 9 位作者 ID 的连接行）会不会溢出

所以这里用极端数据渲染，并分别量出「每一条内容行的右边缘」：
如果最长的那行离内区右缘还有余量，就安全。
"""

from __future__ import annotations

import asyncio
import base64
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src" / "plugins"))

import nonebot  # noqa: E402

nonebot.init(driver="~none", log_level="WARNING")

from nonebot_plugin_pixiv_novel import render  # noqa: E402
from PIL import Image  # noqa: E402

OUT = REPO / "out"


def _avatar(color=(255, 0, 0), w=128, h=128) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


AV = _avatar()

# 最坏情况：超长中文作者名 + 9 位作者 ID（URL 会最长）
ROWS = [
    {
        "author_id": 123456789,                    # 9 位
        "author_name": "とても長いペンネームの作者さん（長名字压力测试）",
        "author_avatar_url": "u1",
        "created_at": 1759300000,
        "last_seen": 29277050,
    },
    {
        "author_id": 99999999,
        "author_name": "短名",
        "author_avatar_url": "u2",
        "created_at": 1759300000,
        "last_seen": 29277050,
    },
]


def content(p) -> bool:
    r, g, b = p
    return (r * 299 + g * 587 + b * 114) // 1000 <= 200


async def main() -> int:
    template_to_pic = render._load_htmlkit()
    ctx = render.build_context(ROWS, group_id=1094538078, avatars={"u1": AV, "u2": AV})
    png = await template_to_pic(
        render.TEMPLATES_DIR, render.TEMPLATE_NAME, ctx,
        dpi=96.0, max_width=860, device_height=600,
        allow_refit=True, image_format="png",
    )
    (OUT / "stress_subscription_list.png").write_bytes(png)

    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    px = im.load()
    inner_left, inner_right = 50, w - 50
    print(f"图 {w}x{h}   卡片内区 x∈[{inner_left},{inner_right}]")

    # 逐行算「最右内容像素」
    rows_info = []
    run = None
    for y in range(h):
        xs = [x for x in range(w) if content(px[x, y])]
        if xs:
            if run is None:
                run = [y, y, min(xs), max(xs)]
            else:
                run[1] = y
                run[2] = min(run[2], min(xs))
                run[3] = max(run[3], max(xs))
        elif run is not None:
            rows_info.append(tuple(run))
            run = None
    if run is not None:
        rows_info.append(tuple(run))

    print(f"\n纵向内容带 {len(rows_info)} 段：")
    worst = 0
    for y0, y1, x0, x1 in rows_info:
        slack = inner_right - x1
        worst = max(worst, x1)
        flag = "❌ 溢出" if slack < 0 else ("⚠️ 余量太小" if slack < 12 else "✅")
        print(f"  y∈[{y0:3},{y1:3}]  x∈[{x0:3},{x1:3}]  右侧余量 {slack:4}px  {flag}")

    over = sum(1 for y in range(h) for x in range(inner_right + 1, w) if content(px[x, y]))
    print(f"\n溢出内区右缘的像素: {over}  最右内容 x={worst}")
    print("（页脚 `.foot` 是 text-align:right，紧贴内区右缘属正常）")
    return 0 if over == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
