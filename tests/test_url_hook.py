"""被动 URL hook 的行为测试（`__init__.py` 里的那部分）。

这些测试针对的是**容易写错又不容易发现**的几点：

  · 机器人自己的消息不能被自己触发（否则卡片里的链接 → 无限循环）
  · 去重窗口要**按会话隔离**（A 群贴过不该让 B 群不响应）
  · 命令消息（如「获取全文 <链接>」）不该被回两遍
  · 总开关关掉时一句话都不发

导入 `nonebot_plugin_pixiv_novel` 就会执行插件入口（on_message 注册等），
`conftest.py` 已先 init 过 NoneBot，所以这里可以直接 import。
"""

import time

import pytest

pytest.importorskip("nonebot")

from conftest import wrap_json
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
from nonebot_plugin_pixiv_novel import _seen, _session_key, _should_skip


def group_event(group_id: int, user_id: int = 10001) -> GroupMessageEvent:
    """真事件替身（走 `isinstance` 分支）。

    用 `model_construct` 绕开 pydantic 校验：真实 GroupMessageEvent 有十几个必填字段，
    逐个填没意义；`model_construct` 只填给到的字段，`isinstance` 仍然为真 ——
    这样测的是**生产代码那条 isinstance 分支**，而不是「把 isinstance 改成鸭子类型」。
    """
    return GroupMessageEvent.model_construct(group_id=group_id, user_id=user_id)


def private_event(user_id: int = 10001) -> PrivateMessageEvent:
    return PrivateMessageEvent.model_construct(user_id=user_id)


@pytest.fixture(autouse=True)
def _clear_seen():
    _seen.clear()
    yield
    _seen.clear()


# ── 会话隔离 ──────────────────────────────────────────────────────


def test_session_key_separates_groups_and_private():
    assert _session_key(group_event(111)) == "group:111"
    assert _session_key(group_event(222)) == "group:222"
    assert _session_key(private_event(333)) == "private:333"


# ── 去重窗口 ──────────────────────────────────────────────────────


def test_same_target_in_same_session_is_skipped_second_time():
    key = ("group:111", "novel", 900)
    assert _should_skip(key) is False        # 第一次：放行
    assert _should_skip(key) is True         # 紧接着第二次：跳过


def test_different_session_is_not_affected():
    """**关键**：去重按会话隔离。A 群贴过的链接，B 群要照常响应。"""
    assert _should_skip(("group:111", "novel", 900)) is False
    assert _should_skip(("group:222", "novel", 900)) is False
    assert _should_skip(("private:333", "novel", 900)) is False


def test_different_target_is_not_affected():
    assert _should_skip(("group:111", "novel", 900)) is False
    assert _should_skip(("group:111", "novel", 901)) is False
    assert _should_skip(("group:111", "series", 900)) is False


def test_expired_window_allows_again(monkeypatch):
    """窗口过期后应重新响应。用**伪造的单调时钟**推进，不真等 60 秒。"""
    key = ("group:111", "novel", 900)
    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    assert _should_skip(key) is False
    clock["t"] += 59.0
    assert _should_skip(key) is True         # 窗口内
    clock["t"] += 2.0
    assert _should_skip(key) is False        # 61 秒后：重新放行


def test_stale_entries_are_pruned(monkeypatch):
    """长期运行不能把字典撑爆：过期键要在下次调用时被清掉。"""
    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    for i in range(50):
        _should_skip(("group:1", "novel", i))
    assert len(_seen) == 50

    clock["t"] += 3600.0                      # 远超窗口
    _should_skip(("group:1", "novel", 999))
    assert len(_seen) == 1                    # 旧的 50 个都被清理了


def test_cooldown_zero_disables_dedup(monkeypatch):
    """`pixiv_url_hook_cooldown=0` = 不去重（用户要原文照回的场景）。"""
    from nonebot_plugin_pixiv_novel import plugin_config

    monkeypatch.setattr(plugin_config, "pixiv_url_hook_cooldown", 0)
    key = ("group:111", "novel", 900)
    assert _should_skip(key) is False
    assert _should_skip(key) is False        # 仍放行
    assert _seen == {}                       # 且不记录状态


# ── 卡片构造（用真实 fixture，不碰网络）──────────────────────────


@pytest.mark.asyncio
async def test_build_novel_card_uses_detail_and_blurs_r18(monkeypatch):
    """R18 作品：封面要被要求模糊，且卡片带 R18 标记。"""
    from conftest import load_fixture
    from nonebot_plugin_pixiv_novel import _build_novel_card, client, plugin_config

    raw = load_fixture("novel_detail_r18.json")
    detail = raw["novel"]

    async def fake_detail(novel_id):
        return detail

    blur_calls = []

    async def fake_cover(url, *, blur, radius):
        blur_calls.append((url, blur, radius))
        return b"\x89PNG"

    monkeypatch.setattr(plugin_config, "pixiv_blur_r18", True)
    monkeypatch.setattr(plugin_config, "pixiv_blur_radius", 9)
    monkeypatch.setattr(client, "novel_detail", fake_detail)
    monkeypatch.setattr(client, "download_cover", fake_cover)

    msg = await _build_novel_card(29277050)
    text = str(msg)

    assert blur_calls and blur_calls[0][1] is True      # blur=True
    assert blur_calls[0][2] == 9                        # 半径用配置值
    assert "R-18" in text and "已模糊" in text
    assert "https://www.pixiv.net/novel/show.php?id=" in text


@pytest.mark.asyncio
async def test_build_novel_card_does_not_blur_when_disabled(monkeypatch):
    from conftest import load_fixture
    from nonebot_plugin_pixiv_novel import _build_novel_card, client, plugin_config

    detail = load_fixture("novel_detail_r18.json")["novel"]
    blur_calls = []

    async def fake_detail(novel_id):
        return detail

    async def fake_cover(url, *, blur, radius):
        blur_calls.append(blur)
        return b"\x89PNG"

    monkeypatch.setattr(plugin_config, "pixiv_blur_r18", False)   # 关掉模糊
    monkeypatch.setattr(client, "novel_detail", fake_detail)
    monkeypatch.setattr(client, "download_cover", fake_cover)

    text = str(await _build_novel_card(29277050))
    assert blur_calls == [False]
    assert "R-18" in text
    assert "已模糊" not in text and "未模糊" in text


@pytest.mark.asyncio
async def test_build_novel_card_skips_cover_download_when_url_missing(monkeypatch):
    """没有封面 URL 时**不该**去请求（省一次必然失败的调用）。"""
    from nonebot_plugin_pixiv_novel import _build_novel_card, client

    async def fake_detail(novel_id):
        # 用 wrap_json 复刻真实 JsonDict 语义（属性访问可用）——
        # 生产里 novel_detail 返回的就是 JsonDict，传普通 dict 会失真
        return wrap_json({"id": novel_id, "title": "无封面", "x_restrict": 0,
                          "image_urls": None, "user": {"id": 1, "name": "a"}})

    called = []

    async def fake_cover(url, *, blur, radius):
        called.append(url)
        return b""

    monkeypatch.setattr(client, "novel_detail", fake_detail)
    monkeypatch.setattr(client, "download_cover", fake_cover)
    text = str(await _build_novel_card(1))
    assert called == []
    assert "无封面" in text


@pytest.mark.asyncio
async def test_build_series_card_uses_series_level_x_restrict(monkeypatch):
    """**关键**：系列卡的 R18 判据是系列自己的 `xRestrict`，不是别的字段。"""
    from conftest import load_fixture
    from nonebot_plugin_pixiv_novel import _build_series_card, client, plugin_config

    body = load_fixture("novel_series_r18.json")
    blur_calls = []

    async def fake_series(series_id):
        return body

    async def fake_cover(url, *, blur, radius):
        blur_calls.append((url, blur))
        return b"\x89PNG"

    monkeypatch.setattr(plugin_config, "pixiv_blur_r18", True)
    monkeypatch.setattr(plugin_config, "pixiv_blur_radius", 9)
    monkeypatch.setattr(client, "novel_series", fake_series)
    monkeypatch.setattr(client, "download_cover", fake_cover)

    text = str(await _build_series_card(15744810))
    assert blur_calls and blur_calls[0][1] is True      # 因为 xRestrict=1 才模糊
    assert "R-18" in text and "已模糊" in text
    assert "https://www.pixiv.net/novel/series/15744810" in text


@pytest.mark.asyncio
async def test_build_series_card_safe_series_not_blurred(monkeypatch):
    from conftest import load_fixture
    from nonebot_plugin_pixiv_novel import _build_series_card, client, plugin_config

    body = load_fixture("novel_series_safe.json")
    blur_calls = []

    async def fake_series(series_id):
        return body

    async def fake_cover(url, *, blur, radius):
        blur_calls.append(blur)
        return b"\x89PNG"

    monkeypatch.setattr(plugin_config, "pixiv_blur_r18", True)
    monkeypatch.setattr(client, "novel_series", fake_series)
    monkeypatch.setattr(client, "download_cover", fake_cover)

    text = str(await _build_series_card(16486288))
    assert blur_calls == [False]
    assert "R-18" not in text


@pytest.mark.asyncio
async def test_cover_failure_does_not_kill_the_card(monkeypatch):
    """封面下载失败 → 卡片照发（少张图），不能整条挂掉。"""
    from nonebot_plugin_pixiv_novel import _fetch_cover, client

    async def boom(url, *, blur, radius):
        raise OSError("i.pximg.net 403")

    monkeypatch.setattr(client, "download_cover", boom)
    data, blurred = await _fetch_cover("https://i.pximg.net/x.jpg", 1)
    assert data == b""
    assert blurred is False
