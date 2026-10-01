"""Task 0.5 —— htmlkit 渲染冒烟测试。

目的（三条一起验）：
  1. htmlkit 能不能在本机加载（它是 C++ 扩展 core.pyd）
  2. 中文能不能正常渲染（不是方块）
  3. 头像走 base64 data URI 能不能被 htmlkit 原生解码显示

跑法：
    env -u UV_PYTHON ... uv run python scripts/smoke_htmlkit.py
产物： out/subscription_list.png
"""

import asyncio
import base64
import io
import sys
import time
from pathlib import Path

import nonebot

# htmlkit 在 import 时就 get_driver()，必须先 init
nonebot.init(driver="~none", log_level="INFO")

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

from PIL import Image, ImageDraw  # noqa: E402

from nonebot_plugin_pixiv_novel import avatars, render  # noqa: E402

OUT = Path(__file__).parent.parent / "out"


def fake_avatar(label: str, color) -> bytes:
    """造一张 64x64 的假头像，画个色块 + 不用字体（避免依赖系统字体）。"""
    img = Image.new("RGB", (64, 64), color)
    ImageDraw.Draw(img).rectangle([16, 16, 48, 48], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def to_data_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


AVATAR_A = "https://i.pximg.net/avatar_a.jpg"
AVATAR_B = "https://i.pximg.net/avatar_b.jpg"


async def main() -> int:
    OUT.mkdir(exist_ok=True)
    avatars.init(OUT / "avatar_cache")

    rows = [
        {
            "author_id": 11111111,
            "author_name": "作者甲（中文测试）",
            "author_avatar_url": AVATAR_A,
            "created_at": int(time.time()) - 3600,
            "last_seen": 900,
        },
        {
            "author_id": 22222222,
            "author_name": "作者乙 with English",
            "author_avatar_url": AVATAR_B,
            "created_at": int(time.time()) - 86400,
            "last_seen": 800,
        },
        {
            "author_id": 33333333,
            "author_name": "",                     # 没名字 → 应显示 ID
            "author_avatar_url": "",               # 没头像 → 应显示首字占位块
            "created_at": int(time.time()),
            "last_seen": 700,
        },
    ]

    avatar_map = {
        AVATAR_A: to_data_uri(fake_avatar("A", (220, 90, 90))),
        AVATAR_B: to_data_uri(fake_avatar("B", (90, 140, 220))),
    }

    print("== 开始渲染 ==")
    png = await render.render_subscription_list(
        rows, group_id=1094538078, avatars=avatar_map
    )

    if png is None:
        print("❌ 渲染返回 None（htmlkit 加载失败或渲染抛异常）—— 看上面的 WARNING 日志")
        return 1

    path = OUT / "subscription_list.png"
    path.write_bytes(png)
    print(f"✅ 渲染成功：{len(png)} 字节 → {path}")

    with Image.open(io.BytesIO(png)) as im:
        print(f"   尺寸 {im.width}x{im.height}，格式 {im.format}")
        if im.height < 60:
            print("⚠️ 图片高度过小，可能渲染的是空白页")
            return 2

    # 顺带验证 data URI 真的被 htmlkit 解码了（不是画了个碎图）
    print("\n== 供人工确认的点 ==")
    print("  1. 中文有没有变成方块（□□□）")
    print("  2. 前两条有没有头像图片（红/蓝色块）")
    print("  3. 第三条有没有灰底首字占位块")
    print("  4. 布局有没有明显错乱")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
