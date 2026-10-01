from pathlib import Path

from nonebot_plugin_pixiv_novel.policy import (
    decide_text_delivery,
    is_admin,
    is_admin_identity,
)

# ── 管理员闸门（需求：所有指令默认仅管理员可用）───────────────


def test_admin_only_off_lets_everyone_in():
    assert is_admin(999, admin_only=False, admin_ids=[], superusers=["1"]) is True


def test_explicit_admin_ids_take_precedence_over_superusers():
    assert is_admin(123, admin_only=True, admin_ids=[123], superusers=["999"]) is True
    assert is_admin(999, admin_only=True, admin_ids=[123], superusers=["999"]) is False


def test_falls_back_to_superusers_when_admin_ids_empty():
    qbot_superusers = ["1330509996", "2846018938"]
    assert is_admin(1330509996, admin_only=True, admin_ids=[], superusers=qbot_superusers) is True
    assert is_admin(42, admin_only=True, admin_ids=[], superusers=qbot_superusers) is False


def test_admin_check_accepts_both_str_and_int():
    """NoneBot 的 user_id 是 str；配置里可能写成 int。两边都要认。"""
    assert is_admin("1330509996", admin_only=True, admin_ids=[], superusers=["1330509996"]) is True
    assert is_admin(1330509996, admin_only=True, admin_ids=[1330509996], superusers=[]) is True


def test_nobody_is_admin_when_admin_only_and_both_lists_empty():
    assert is_admin(1, admin_only=True, admin_ids=[], superusers=[]) is False


# ── 全文投递决策：轴 1（渠道）× 轴 2（R18）──────────────────


def _cfg(**kw):
    base = {"pixiv_text_targets": ["group"], "pixiv_r18_text_allow_group": False}
    base.update(kw)
    return base


def test_group_normal_allowed_by_default():
    ok, _ = decide_text_delivery(channel="group", x_restrict=0, **_cfg())
    assert ok is True


def test_group_normal_rejected_when_group_not_in_targets():
    ok, reason = decide_text_delivery(channel="group", x_restrict=0, **_cfg(pixiv_text_targets=["private"]))
    assert ok is False
    assert "群聊" in reason


def test_group_r18_rejected_by_default():
    ok, reason = decide_text_delivery(channel="group", x_restrict=1, **_cfg())
    assert ok is False
    assert "R-18" in reason


def test_group_r18_allowed_when_flag_on():
    ok, _ = decide_text_delivery(channel="group", x_restrict=1, **_cfg(pixiv_r18_text_allow_group=True))
    assert ok is True


def test_group_r18g_also_blocked_by_default():
    ok, reason = decide_text_delivery(channel="group", x_restrict=2, **_cfg())
    assert ok is False
    assert "R-18G" in reason


def test_channel_check_wins_over_r18_check():
    """渠道不允许 + R18 不允许 同时成立时，报的是**渠道**原因（判断顺序不可改）。"""
    ok, reason = decide_text_delivery(
        channel="group",
        x_restrict=1,
        **_cfg(pixiv_text_targets=[], pixiv_r18_text_allow_group=False),
    )
    assert ok is False
    assert "R-18" not in reason


def test_private_normal_allowed_when_configured():
    ok, _ = decide_text_delivery(channel="private", x_restrict=0, **_cfg(pixiv_text_targets=["private"]))
    assert ok is True


def test_private_normal_rejected_by_default():
    ok, reason = decide_text_delivery(channel="private", x_restrict=0, **_cfg())
    assert ok is False
    assert "私聊" in reason


def test_private_r18_allowed_even_when_group_flag_off():
    """R18 开关只管群聊；私聊不受它限制。"""
    ok, _ = decide_text_delivery(
        channel="private",
        x_restrict=1,
        **_cfg(pixiv_text_targets=["private"], pixiv_r18_text_allow_group=False),
    )
    assert ok is True


def test_private_r18_rejected_when_private_not_in_targets():
    ok, reason = decide_text_delivery(channel="private", x_restrict=1, **_cfg())
    assert ok is False
    assert "私聊" in reason


def test_empty_targets_blocks_everything():
    for ch in ("group", "private"):
        ok, _ = decide_text_delivery(
            channel=ch, x_restrict=0, **_cfg(pixiv_text_targets=[], pixiv_r18_text_allow_group=True)
        )
        assert ok is False


# ── 管理员绕过「获取全文」的限制（2026-10-02 用户要求）──────────


def test_admin_bypasses_channel_axis():
    """管理员不受渠道轴限制（默认只允许群聊，但管理员在私聊也能取）。"""
    ok, reason = decide_text_delivery(
        channel="private", x_restrict=0, is_admin=True, **_cfg()
    )
    assert ok is True
    assert reason == ""


def test_admin_bypasses_r18_axis_in_group():
    """管理员在群里也能取 R18 正文（默认是不允许的）。"""
    ok, reason = decide_text_delivery(
        channel="group", x_restrict=1, is_admin=True, **_cfg()
    )
    assert ok is True
    assert reason == ""


def test_admin_bypasses_both_axes_even_when_targets_empty():
    """targets 全空（对普通人等于全拒）也拦不住管理员。"""
    ok, _ = decide_text_delivery(
        channel="group",
        x_restrict=2,
        is_admin=True,
        **_cfg(pixiv_text_targets=[], pixiv_r18_text_allow_group=False),
    )
    assert ok is True


def test_non_admin_still_subject_to_all_axes():
    """非管理员一切照旧 —— 绕过只给管理员，不能顺手放宽。"""
    assert decide_text_delivery(channel="private", x_restrict=0, is_admin=False, **_cfg())[0] is False
    assert decide_text_delivery(channel="group", x_restrict=1, is_admin=False, **_cfg())[0] is False


def test_is_admin_defaults_to_false_keeps_backward_compat():
    """不传 is_admin 时行为与改前完全一致（默认 False）。"""
    ok, reason = decide_text_delivery(channel="group", x_restrict=1, **_cfg())
    assert ok is False
    assert "R-18" in reason


def test_admin_flag_does_not_leak_into_normal_path():
    """is_admin=True 走的是独立分支，不会改变 `targets` 的解析方式。"""
    ok, _ = decide_text_delivery(
        channel="GROUP",  # 大小写/空白应被归一化
        x_restrict=0,
        is_admin=False,
        **_cfg(pixiv_text_targets=["  Group  "]),
    )
    assert ok is True


def test_r18_rejection_suggests_private_only_when_private_is_available():
    _, reason = decide_text_delivery(channel="group", x_restrict=1, **_cfg())
    assert "私聊" not in reason          # 默认 targets 不含 private，不该误导用户

    _, reason2 = decide_text_delivery(
        channel="group", x_restrict=1, **_cfg(pixiv_text_targets=["group", "private"])
    )
    assert "私聊" in reason2             # 配了 private 才建议私聊


# ══════════════════════════════════════════════════════════════════
# 身份 ≠ 闸门：这两件事必须分开
#
# 踩过的坑：`is_admin()` 在 `admin_only=False` 时对**所有人**返回 True（放行语义）。
# 若把它直接当作「可绕过两轴」的判据，那么一关掉 admin_only 让普通群友也能用命令，
# **每个群友都会被当成管理员**，R18/渠道两轴随之彻底失效 —— 改配置等于白改。
# ══════════════════════════════════════════════════════════════════


def test_identity_ignores_admin_only_switch():
    """身份判定只看名单，不受 admin_only 影响。"""
    su = ["1330509996", "2846018938"]
    # 闸门关掉时：闸门对所有人放行，但身份仍然只认名单里的人
    assert is_admin(42, admin_only=False, admin_ids=[], superusers=su) is True
    assert is_admin_identity(42, admin_ids=[], superusers=su) is False

    assert is_admin(1330509996, admin_only=False, admin_ids=[], superusers=su) is True
    assert is_admin_identity(1330509996, admin_ids=[], superusers=su) is True


def test_identity_prefers_explicit_admin_ids():
    assert is_admin_identity(123, admin_ids=[123], superusers=["999"]) is True
    assert is_admin_identity(999, admin_ids=[123], superusers=["999"]) is False


def test_gate_and_identity_agree_when_admin_only_is_on():
    """admin_only=True 时两者结论一致（闸门就是身份）。"""
    su = ["1"]
    for uid in (1, 2):
        for ids in ([], [1]):
            assert is_admin(uid, admin_only=True, admin_ids=ids, superusers=su) == \
                is_admin_identity(uid, admin_ids=ids, superusers=su)


def test_ordinary_member_cannot_bypass_axes_while_admin_can():
    """把整个语义摆在一起：同一篇 R18、同一个群，
    普通成员被拦、管理员放行 —— 这正是拆开两者的目的。
    """
    cfg = {"pixiv_text_targets": ["group"], "pixiv_r18_text_allow_group": False}
    su = ["1330509996"]

    member_is_admin = is_admin_identity(42, admin_ids=[], superusers=su)
    admin_is_admin = is_admin_identity(1330509996, admin_ids=[], superusers=su)

    assert decide_text_delivery(channel="group", x_restrict=1,
                                is_admin=member_is_admin, **cfg)[0] is False
    assert decide_text_delivery(channel="group", x_restrict=1,
                                is_admin=admin_is_admin, **cfg)[0] is True


def test_config_default_is_admin_only_false():
    """配置默认值回归：默认必须是 False（否则两轴对实际使用者等于失效）。"""
    from nonebot_plugin_pixiv_novel.config import Config

    assert Config().pixiv_admin_only is False


# ══════════════════════════════════════════════════════════════════
# 接线检查：handler 里必须传**身份**判据，不能传**闸门**判据
#
# 为什么需要这条（变异测试发现的覆盖缺口）：
# 把 `is_admin=_is_admin_identity(event)` 改成 `is_admin=_is_admin(event)` 之后，
# 上面所有纯函数测试**依然全绿** —— 因为它们只测 policy 里的纯函数，
# 看不见 handler 到底把哪个结果传了进去。
# 而这个接错线的后果很隐蔽：admin_only=False（当前默认）时闸门对所有人放行，
# 于是每个普通群友都成了「管理员」，R18/渠道两轴静默失效。
# 所以这里直接读源码做静态断言。
# ══════════════════════════════════════════════════════════════════


def _pkg_source(name: str) -> str:
    import nonebot_plugin_pixiv_novel as pkg

    return (Path(pkg.__file__).parent / name).read_text(encoding="utf-8")


def test_text_handler_passes_identity_not_gate_to_bypass():
    src = _pkg_source("__init__.py")
    assert "is_admin=_is_admin_identity(event)" in src, (
        "「获取全文」的绕过判据必须是 _is_admin_identity（纯身份）；"
        "若换成 _is_admin（闸门），admin_only=False 时人人都能绕过两轴"
    )
    assert "is_admin=_is_admin(event)" not in src, "绕过判据接错成闸门了"


def test_gate_still_used_to_protect_commands():
    """命令闸门该用 `_is_admin`（含 admin_only 开关）—— 别被顺手改掉。"""
    src = _pkg_source("__init__.py")
    assert src.count("if not _is_admin(event):") == 4, "4 个命令都应有管理员闸门"
