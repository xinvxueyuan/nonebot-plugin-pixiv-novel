"""度量订阅列表渲染的版面：图片尺寸 + 头像与右侧元素的真实间距。

用户反馈两件事，都得**实测**才能定改法：
  1. 渲染图在 QQ 里偏小
  2. 头像与右侧元素「粘在一起」

已确认（第一轮测量）：
  · `dpi` 与 `max_width` 对输出像素**完全无影响**（96/144/192、700/900 都是 335×222）
    → 尺寸由内容的固有宽度决定，想变大只能改 CSS 尺寸
  · 不能用「整列是否为纯背景」来找间距 —— `.row` 的 `border-bottom` 横贯整行，
    每一列都至少有一个非背景像素，会把真实留白吞掉

本脚本改用**按头像色块定位**：
  先找头像（纯色块）的包围盒 → 取头像垂直中线那一行 → 向右逐像素扫，
  数到第一个非背景像素为止 = 头像与右侧内容的实际间距。
"""

from __future__ import annotations

import asyncio
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
OUT.mkdir(exist_ok=True)

ROW = {
    "author_id": 61943687,
    "author_name": "さむしんぐ",
    "author_avatar_url": "https://i.pximg.net/user-profile/img/x.jpg",
    "created_at": 1759300000,
    "last_seen": 29277050,
}

# 用 PIL 现生成一张纯红头像 PNG → 可在像素上精确定位头像块。
# （不要手搓 base64：早先硬编码的那串根本不是红色，导致「头像色块找不到」）
import base64  # noqa: E402


def _red_avatar(w: int = 128, h: int = 128) -> str:
    im = Image.new("RGB", (w, h), (255, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


AVATAR = _red_avatar()


def _is_red(p: tuple[int, int, int]) -> bool:
    r, g, b = p
    return r > 150 and g < 110 and b < 110


LUMA_CONTENT = 200          # 亮度 <= 此值 视为「内容」


def _content(p: tuple[int, int, int]) -> bool:
    """判定「内容像素」。

    ⚠️ 不能拿页面灰底（246,247,249）当参照色 —— **卡片是白底**，
    那一行里除了最外侧全是「非页面色」，于是永远量出 0px 间距，
    看起来像「头像与文字粘住」的假象（这是我第一版的错误）。
    改用亮度阈值：文字深色 / 头像彩色 / 边框线 都算内容，
    页面灰底与卡片白底都算留白。
    """
    r, g, b = p
    return (r * 299 + g * 587 + b * 114) // 1000 <= LUMA_CONTENT


def measure(png: bytes, label: str, *, verbose: bool = True) -> dict:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    px = im.load()

    # 1) 头像包围盒（红色块）
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if _is_red(px[x, y]):
                xs.append(x)
                ys.append(y)

    info: dict = {"w": w, "h": h}
    if not xs:
        if verbose:
            print(f"\n--- {label} ---")
            print(f"  尺寸: {w} x {h}   ⚠️ 未找到头像色块")
        return info

    ax0, ax1 = min(xs), max(xs)
    ay0, ay1 = min(ys), max(ys)
    info["avatar"] = (ax0, ay0, ax1, ay1)
    info["avatar_size"] = (ax1 - ax0 + 1, ay1 - ay0 + 1)

    # 2) 头像垂直中线那一行，向右/向左数留白 = 实际间距
    mid = (ay0 + ay1) // 2
    gap_right = 0
    for x in range(ax1 + 1, w):
        if not _content(px[x, mid]):
            gap_right += 1
        else:
            break
    gap_left = 0
    for x in range(ax0 - 1, -1, -1):
        if not _content(px[x, mid]):
            gap_left += 1
        else:
            break

    info["gap_left"] = gap_left
    info["gap_right"] = gap_right

    if verbose:
        print(f"\n--- {label} ---")
        print(f"  尺寸: {w} x {h}")
        print(f"  头像: {info['avatar_size'][0]}x{info['avatar_size'][1]}"
              f"  x∈[{ax0},{ax1}] y∈[{ay0},{ay1}]")
        print(f"  左侧间距(序号↔头像): {gap_left}px")
        print(f"  右侧间距(头像↔文字): {gap_right}px"
              f"   {'❌ 粘在一起' if gap_right < 8 else '✅'}")
    return info


async def render_at(label: str, *, dpi: float = 96.0, max_width: int = 700,
                    template: str | None = None) -> bytes | None:
    template_to_pic = render._load_htmlkit()
    ctx = render.build_context(
        [ROW], group_id=1094538078, avatars={ROW["author_avatar_url"]: AVATAR}
    )
    tdir, tname = render.TEMPLATES_DIR, render.TEMPLATE_NAME
    if template:                       # 让测量脚本能试候选模板而不改生产文件
        tdir, tname = Path(template).parent, Path(template).name
    try:
        png = await template_to_pic(
            tdir, tname, ctx,
            dpi=dpi, max_width=max_width, device_height=600,
            allow_refit=True, image_format="png",
        )
    except Exception as e:
        print(f"\n--- {label} --- 失败: {type(e).__name__}: {e}")
        return None
    (OUT / f"metric_{label}.png").write_bytes(png)
    return png


async def main() -> int:
    png = await render_at("new")
    if png:
        m = measure(png, "改后（card 660px / 头像 76px / 名字 26px / margin 22px）")
        print()
        print("  改前基线: 335 x 222，头像 40px，间距 0px（gap 不生效）")
        print(f"  改后尺寸: {m['w']} x {m['h']}px"
              f"   → 宽度 {335} → {m['w']}（放大约 {m['w'] / 335:.2f}x）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
