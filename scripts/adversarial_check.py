"""对抗式复查：边界输入 + 决策矩阵全格 + 降级路径。

不复用单测里的断言，独立穷举一遍，看有没有单测没覆盖到的漏洞。
"""

import sys
from pathlib import Path

import nonebot

nonebot.init(driver="~none", log_level="ERROR")
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

from nonebot_plugin_pixiv_novel import policy  # noqa: E402
from nonebot_plugin_pixiv_novel.handlers import extract_id  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{label}: got={got!r} want={want!r}")
    return "✅" if ok else "❌"


print("=== extract_id 边界 ===")
cases = [
    ("12345", 12345),
    ("  12345  ", 12345),
    ("https://www.pixiv.net/users/12345", 12345),
    ("https://www.pixiv.net/novel/show.php?id=67890", 67890),
    ("https://www.pixiv.net/novel/show.php?id=67890#part=3", 67890),
    ("abc", None), ("", None), (None, None),
    ("12", None),
    ("https://www.pixiv.net/users/1", None),
    ("id=99999999999999999999", 99999999999999999999),
    ("12345 67890", 12345),
]
for inp, want in cases:
    got = extract_id(inp)
    print(f"  {check(f'extract_id({inp!r})', got, want)} {str(inp)[:50]:52} → {got!r}")

print("\n=== policy 决策矩阵（穷举）===")
grid = [
    ("group", 0, ["group"], False, True),
    ("group", 0, ["private"], False, False),
    ("group", 1, ["group"], False, False),
    ("group", 1, ["group"], True, True),
    ("group", 1, [], False, False),
    ("private", 0, ["private"], False, True),
    ("private", 0, ["group"], False, False),
    ("private", 1, ["private"], False, True),
    ("private", 1, [], False, False),
    ("group", 0, [], True, False),
    ("group", 2, ["group"], False, False),
    ("private", 2, ["private"], False, True),
    ("group", 2, ["group"], True, True),
]
for ch, xr, tg, flag, want in grid:
    ok, reason = policy.decide_text_delivery(
        channel=ch, x_restrict=xr, pixiv_text_targets=tg, pixiv_r18_text_allow_group=flag
    )
    print(f"  {check(f'grid {ch}/{xr}/{tg}/{flag}', ok, want)} "
          f"{ch:8} x={xr} targets={tg!s:20} r18群={flag!s:5} → {ok!s:5} {reason}")

print("\n=== 判断顺序：渠道优先于 R18 ===")
_, r = policy.decide_text_delivery(
    channel="group", x_restrict=1, pixiv_text_targets=[], pixiv_r18_text_allow_group=False
)
print(f"  {check('渠道优先', 'R-18' not in r, True)} 两条都不满足时报：{r!r}")

print("\n=== R18 拒绝文案 ===")
_, r1 = policy.decide_text_delivery(
    channel="group", x_restrict=1, pixiv_text_targets=["group"], pixiv_r18_text_allow_group=False
)
_, r2 = policy.decide_text_delivery(
    channel="group", x_restrict=1, pixiv_text_targets=["group", "private"],
    pixiv_r18_text_allow_group=False,
)
print(f"  {check('仅群聊不提私聊', '私聊' in r1, False)} 仅群聊: {r1!r}")
print(f"  {check('含私聊要提私聊', '私聊' in r2, True)} 含私聊: {r2!r}")

print("\n=== 管理员闸门边界 ===")
admin_cases = [
    (1, False, [], [], True, "admin_only=false 全放行"),
    (123, True, [123], ["999"], True, "显式名单命中"),
    (999, True, [123], ["999"], False, "显式名单未命中（不回退）"),
    (1330509996, True, [], ["1330509996", "2846018938"], True, "回退 SUPERUSERS"),
    (42, True, [], ["1330509996"], False, "回退后未命中"),
    ("1330509996", True, [], ["1330509996"], True, "str/int 都要认"),
    (1, True, [], [], False, "两边都空 → 谁都不是"),
    (123, True, ["123"], [], True, "名单是 str 也要认"),
]
for uid, only, ids, sup, want, label in admin_cases:
    got = policy.is_admin(uid, admin_only=only, admin_ids=ids, superusers=sup)
    print(f"  {check(label, got, want)} {label:28} uid={uid!r} → {got}")

print("\n=== 大小写/去重/空白 ===")
import pydantic  # noqa: E402
from nonebot_plugin_pixiv_novel.config import Config  # noqa: E402

c = Config(pixiv_text_targets=["GROUP", " Group ", "group", "private"])
print(f"  {check('去重+小写', c.pixiv_text_targets, ['group', 'private'])} "
      f"['GROUP',' Group ','group','private'] → {c.pixiv_text_targets}")
c2 = Config(pixiv_text_targets=["", "  "])
print(f"  {check('空白项被丢弃', c2.pixiv_text_targets, [])} ['','  '] → {c2.pixiv_text_targets}")
try:
    Config(pixiv_text_targets=["telegram"])
    print("  ❌ 非法渠道没被拒")
    fails.append("非法渠道未拒绝")
except pydantic.ValidationError:
    print("  ✅ 非法渠道被拒（telegram）")

print("\n=== 模糊半径全区间 ===")
for r in range(0, 16):
    try:
        Config(pixiv_blur_radius=r)
        ok = 6 <= r <= 12
    except pydantic.ValidationError:
        ok = not (6 <= r <= 12)
    print(f"  {'✅' if ok else '❌'} radius={r:2} → {'接受' if ok == (6<=r<=12) else '?'}"
          f"{'（应为接受）' if 6<=r<=12 else '（应拒绝）'}")
    if not ok:
        fails.append(f"radius={r} 边界错误")

print("\n" + "=" * 60)
if fails:
    print(f"❌ 发现 {len(fails)} 个问题：")
    for f in fails:
        print("   -", f)
    raise SystemExit(1)
print("✅ 对抗式复查全过（无单测未覆盖的漏洞）")
