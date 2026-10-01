"""验证渲染出来的订阅列表里，头像**真的是一张图**（不是空白块/占位色）。

比 OCR 更可靠：直接看像素。
  · 头像区域应当有明显颜色多样性（真照片）
  · 若头像丢失，模板会画「首字占位块」= 单色背景 + 一个字，颜色多样性极低

做法：把渲染图按行切片，找出「几乎一整行都是彩色噪声」的带状区域
（那是头像缩略图），再对比占位块的多样性。
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

from PIL import Image

IMG = Path(sys.argv[1] if len(sys.argv) > 1 else
           r"C:\dev\nonebot-plugin-pixiv-novel\out\real_list.png")

im = Image.open(IMG).convert("RGB")
w, h = im.size
print(f"图片：{IMG.name}  {w}x{h}")

px = im.load()

print("\n=== 每行的颜色多样性（唯一色数）===")
rows_info = []
for y in range(h):
    colors = {px[x, y] for x in range(0, w, 3)}
    rows_info.append(len(colors))

# 打印分布概况
mx = max(rows_info)
busy = [i for i, c in enumerate(rows_info) if c > mx * 0.5]
print(f"最多唯一色数 {mx}；超过半数多样性的行数 {len(busy)}")
if busy:
    # 找连续带
    bands, start = [], busy[0]
    for a, b in itertools.pairwise(busy):
        if b - a > 3:
            bands.append((start, a))
            start = b
    bands.append((start, busy[-1]))
    print(f"连续「高多样性」行带（很可能是头像缩略图）：{len(bands)} 条")
    for s, e in bands:
        print(f"  y={s:4}..{e:4}  高度 {e - s + 1:3}px  峰值多样性 {max(rows_info[s:e+1])}")

print("\n=== 判断 ===")
# 真照片缩略图：单行唯一色数会很高（几十到上百）
photo_bands = [b for b in (bands if busy else []) if max(rows_info[b[0]:b[1]+1]) >= 30]
if photo_bands:
    print(f"✅ 找到 {len(photo_bands)} 条像真实照片的头像带 → 头像确实被渲染出来了")
    for s, e in photo_bands:
        print(f"     y={s}..{e}  峰值多样性 {max(rows_info[s:e+1])}")
    rc = 0
else:
    print("❌ 没找到任何像照片的区域 —— 头像可能是空白/占位块")
    print(f"   行多样性最大值仅 {mx}（真照片通常 ≥ 30）")
    rc = 1

# 顺便看看整图是否非空白
allc = {px[x, y] for y in range(0, h, 5) for x in range(0, w, 5)}
print(f"\n整图采样唯一色数：{len(allc)}")
if len(allc) < 10:
    print("❌ 整图几乎是单色 —— 渲染很可能失败/空白")
    rc = 1
else:
    print("✅ 整图内容正常（不是空白）")

sys.exit(rc)
