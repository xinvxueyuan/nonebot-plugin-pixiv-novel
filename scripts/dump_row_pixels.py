"""把渲染图某一行的像素打成可读的「地图」，看头像左右到底是什么。

测量脚本报「左侧 0px / 右侧 0px」，但加 margin 后头像确实右移了 12px ——
说明有别的元素紧贴着头像。直接看像素，别猜。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from PIL import Image  # noqa: E402

OUT = REPO / "out"


def dump(name: str, y_ratio: float = 0.55) -> None:
    path = OUT / f"metric_{name}.png"
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    bg = px[2, 2]
    y = int(h * y_ratio)

    print(f"\n=== {name}  {w}x{h}  扫描行 y={y}  背景={bg} ===")
    # 每 1 px 一个字符：'.' = 背景，'#' = 非背景；并标出头像（红）
    line = []
    for x in range(w):
        r, g, b = px[x, y]
        if (r, g, b) == bg:
            line.append(".")
        elif r > 150 and g < 110 and b < 110:
            line.append("R")           # 头像（红）
        elif r > 200 and g > 200 and b > 200:
            line.append("w")           # 近白（卡片底）
        else:
            line.append("#")           # 其它（文字/线）
    s = "".join(line)
    # 分段打印，带坐标尺
    for start in range(0, w, 100):
        chunk = s[start:start + 100]
        print(f"  x={start:4d} |{chunk}|")
    print("  图例: . 背景(页面灰)   R 头像红   w 近白(卡片)   # 其它(文字/边框)")


for n in ("current", "cand_margin"):
    try:
        dump(n)
    except FileNotFoundError:
        print("缺图:", n)
