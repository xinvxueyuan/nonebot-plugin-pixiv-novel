from nonebot_plugin_pixiv_novel.config import Config


def test_defaults_are_sane():
    c = Config()
    assert c.pixiv_refresh_token == ""
    assert c.pixiv_proxy == "http://127.0.0.1:1081"
    assert c.pixiv_poll_interval == 600
    assert c.pixiv_blur_r18 is True          # R18 默认模糊
    assert c.pixiv_blur_radius == 9          # 固定像素半径，6~12px 取中点
    assert c.pixiv_max_push_per_poll == 5
    assert c.pixiv_text_max_chars == 4000


def test_defaults_for_the_new_axes():
    """权限 / 投递渠道 / R18 全文 / R18 推送 / 模糊半径 的默认值。"""
    c = Config()
    # 2026-10-02 用户拍板改为 False：让普通群友也能用命令，
    # 这样「渠道轴 / R18 轴」才真的对非管理员生效
    # （admin_only=True 时能进来的人全是管理员，而管理员可绕过两轴 → 两轴形同不存在）。
    assert c.pixiv_admin_only is False
    assert c.pixiv_admin_ids == []                  # 空 = 回退 SUPERUSERS
    assert c.pixiv_text_targets == ["group"]        # 默认仅群聊
    assert c.pixiv_r18_text_allow_group is False    # R18 全文默认不发群聊
    assert c.pixiv_r18_push_enabled is True         # R18 新作默认照推（封面模糊）
    assert c.pixiv_blur_radius == 9                 # 6~12px 取中点


def test_blur_radius_range_matches_requirement():
    """需求：模糊半径是**固定像素** 6–12px（默认 9）。"""
    import pydantic
    import pytest

    assert Config().pixiv_blur_radius == 9      # 默认取中点
    assert Config(pixiv_blur_radius=6).pixiv_blur_radius == 6
    assert Config(pixiv_blur_radius=12).pixiv_blur_radius == 12

    with pytest.raises(pydantic.ValidationError):
        Config(pixiv_blur_radius=5)         # 低于下限 6
    with pytest.raises(pydantic.ValidationError):
        Config(pixiv_blur_radius=13)        # 高于上限 12
    assert not hasattr(Config(), "pixiv_blur_ratio")   # 旧的「比例」字段已被移除


def test_r18_push_and_r18_text_are_independent_switches():
    """两个开关互不影响：推送开关改了，全文开关不受影响，反之亦然。"""
    a = Config(pixiv_r18_push_enabled=False)
    assert a.pixiv_r18_text_allow_group is False
    b = Config(pixiv_r18_text_allow_group=True)
    assert b.pixiv_r18_push_enabled is True


def test_text_targets_accepts_any_combination():
    assert Config(pixiv_text_targets=["group"]).pixiv_text_targets == ["group"]
    assert Config(pixiv_text_targets=["private"]).pixiv_text_targets == ["private"]
    assert Config(pixiv_text_targets=["group", "private"]).pixiv_text_targets == ["group", "private"]
    assert Config(pixiv_text_targets=[]).pixiv_text_targets == []   # 空 = 谁都不给用


def test_text_targets_dedupes_and_normalizes_case():
    assert Config(pixiv_text_targets=["GROUP", "group"]).pixiv_text_targets == ["group"]
    assert Config(pixiv_text_targets=["private", "group", "GROUP"]).pixiv_text_targets == [
        "private",
        "group",
    ]


def test_text_targets_rejects_unknown_channel():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        Config(pixiv_text_targets=["guild"])        # 只认 group / private


def test_admin_ids_are_ints():
    assert Config(pixiv_admin_ids=[123]).pixiv_admin_ids == [123]
    assert Config(pixiv_admin_ids=[]).pixiv_admin_ids == []


def test_env_style_override():
    c = Config(pixiv_blur_r18=False, pixiv_poll_interval=120)
    assert c.pixiv_blur_r18 is False
    assert c.pixiv_poll_interval == 120


def test_poll_interval_has_lower_bound():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        Config(pixiv_poll_interval=1)   # 不能让用户设成 1 秒，会被 pixiv 限流


def test_url_hook_defaults():
    """被动 URL hook 的两个开关：默认开、冷却 60s。"""
    c = Config()
    assert c.pixiv_url_hook_enabled is True     # 默认开启（群聊 + 私聊都响应）
    assert c.pixiv_url_hook_cooldown == 60      # 同作品 60s 内不重复回


def test_url_hook_cooldown_range_and_zero_means_no_dedupe():
    """0 是**合法值**，语义是「不去重」—— 不是「用默认值」。"""
    import pydantic
    import pytest

    assert Config(pixiv_url_hook_cooldown=0).pixiv_url_hook_cooldown == 0
    assert Config(pixiv_url_hook_cooldown=3600).pixiv_url_hook_cooldown == 3600
    with pytest.raises(pydantic.ValidationError):
        Config(pixiv_url_hook_cooldown=-1)      # 负数无意义
    with pytest.raises(pydantic.ValidationError):
        Config(pixiv_url_hook_cooldown=3601)    # 上限 1 小时，避免「等于永久静音」


def test_url_hook_is_independent_of_push_switches():
    """关掉新作推送不影响被动卡片，反之亦然 —— 两者是不同功能。"""
    assert Config(pixiv_r18_push_enabled=False).pixiv_url_hook_enabled is True
    assert Config(pixiv_url_hook_enabled=False).pixiv_r18_push_enabled is True


def test_cover_max_width_defaults_to_original():
    """**默认 0 = 发原图**（2026-10-02 用户拍板）。

    背景：App 的 `image_urls.large` 是 CDN 缩略（实测 240x347 / 24KB），
    去掉 `/c/…/` 缩放段才是原图（828x1200 起 / 661KB~1007KB，约 37 倍）。
    用户实测后要求发原图，所以默认不缩放。
    """
    assert Config().pixiv_cover_max_width == 0
    assert Config().pixiv_cover_max_width or "原图" == "原图"   # 启动行按这个显示


def test_cover_max_width_range():
    """0 合法（= 不缩放）；负数无意义；上限 2400 防止手滑写成像素尺寸以外的值。"""
    import pydantic
    import pytest

    assert Config(pixiv_cover_max_width=0).pixiv_cover_max_width == 0
    assert Config(pixiv_cover_max_width=800).pixiv_cover_max_width == 800
    assert Config(pixiv_cover_max_width=2400).pixiv_cover_max_width == 2400
    with pytest.raises(pydantic.ValidationError):
        Config(pixiv_cover_max_width=-1)
    with pytest.raises(pydantic.ValidationError):
        Config(pixiv_cover_max_width=2401)
