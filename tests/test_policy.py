from nonebot_plugin_pixiv_novel.policy import decide_text_delivery, is_admin

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


def test_r18_rejection_suggests_private_only_when_private_is_available():
    _, reason = decide_text_delivery(channel="group", x_restrict=1, **_cfg())
    assert "私聊" not in reason          # 默认 targets 不含 private，不该误导用户

    _, reason2 = decide_text_delivery(
        channel="group", x_restrict=1, **_cfg(pixiv_text_targets=["group", "private"])
    )
    assert "私聊" in reason2             # 配了 private 才建议私聊
