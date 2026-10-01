"""「获取全文」+ 引用被动卡片 → 自动识别作品。

实现方式是**查表**（发卡片时用发送响应里的 message_id 记下作品，引用时反查），
不是解析引用内容。所以这里要钉住三件事：
  ① 表能存能查、能跨重启（落库）、不会无限增长；
  ② 引用 id 的读取对**两种实现形状**都成立（pydantic Reply / reply 消息段）；
  ③ 发送响应里取 message_id 的容错（形状不对时降级，不能让发卡片失败）。
"""

import sqlite3
import time
from types import SimpleNamespace

import pytest
from nonebot_plugin_pixiv_novel import _extract_message_id, handlers, store


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.sqlite3"
    store.init(path)
    return path


# ── 存储：卡片消息映射 ────────────────────────────────────────────


def test_card_roundtrip(db):
    store.remember_card(1001, "novel", 29277050)
    row = store.lookup_card(1001)
    assert row is not None
    assert row["kind"] == "novel"
    assert row["target_id"] == 29277050


def test_unknown_message_id_returns_none(db):
    """引用的不是机器人发的卡片时返回 None（外层据此回「这条引用我不认识」）。"""
    assert store.lookup_card(999999) is None


def test_remember_is_idempotent(db):
    """同一个 message_id 记两次不该炸（INSERT OR REPLACE）。

    若用裸 INSERT，这里会抛 IntegrityError 把「发卡片」这条主流程带崩 ——
    为了一张便利功能表赔上核心功能。
    """
    store.remember_card(1001, "novel", 1)
    store.remember_card(1001, "novel", 1)
    assert store.lookup_card(1001)["target_id"] == 1


def test_series_kind_is_stored_too(db):
    """系列卡片也要记 —— 用户可能引用系列卡片，这时要能回一句有意义的提示。"""
    store.remember_card(2002, "series", 15744810)
    assert store.lookup_card(2002)["kind"] == "series"


def test_old_rows_get_cleaned_up_on_write(db):
    """过期行在写入时被清掉（这张表只服务「引用刚发的卡片」，不需要永久保留）。

    不清的话它会无限增长；更糟的是过期映射会指向早就被撤回的消息，
    让「获取全文」答非所问。
    """
    stale = int(time.time()) - 8 * 24 * 3600  # 8 天前，超过 7 天保留期
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO card_message (message_id, kind, target_id, created_at)"
        " VALUES (?, ?, ?, ?)",
        (777, "novel", 42, stale),
    )
    conn.commit()
    conn.close()

    store.remember_card(1001, "novel", 29277050)  # 触发清理

    assert store.lookup_card(777) is None
    assert store.lookup_card(1001) is not None


def test_recent_rows_survive_the_cleanup(db):
    """刚刚记下的不能被清理顺手带走（边界：保留期是 7 天，不是 0）。"""
    store.remember_card(1, "novel", 1)
    store.remember_card(2, "novel", 2)
    assert store.lookup_card(1) is not None


# ── 读引用 id（两种实现形状都要支持）────────────────────────────


def test_reply_id_from_pydantic_reply(db):
    """形状 A：适配器把 `event.reply` 填成 pydantic 模型（字段 `message_id`）。"""
    event = SimpleNamespace(reply=SimpleNamespace(message_id=1001))
    assert handlers.reply_message_id(event) == 1001


def test_reply_id_from_reply_segment(db):
    """形状 B：实现把引用塞成一个 `type="reply"` 的 MessageSegment，id 在 `data["id"]`。

    这是 LLBot 的实际形状（实测其源码用 `{type:"reply", data:{id: shortId}}`）。
    只支持形状 A 的写法在这里会静默返回 None —— 症状是「引用了卡片却说没引用」。
    """
    seg = SimpleNamespace(type="reply", data={"id": "1001"})
    event = SimpleNamespace(reply=seg)
    assert handlers.reply_message_id(event) == 1001


def test_reply_id_from_message_body_when_reply_attr_is_empty(db):
    """`event.reply` 是空的，但消息体里有 reply 段 —— 也要能取到。"""
    seg = SimpleNamespace(type="reply", data={"id": "1001"})
    event = SimpleNamespace(reply=None, get_message=lambda: [seg])
    assert handlers.reply_message_id(event) == 1001


def test_no_reply_returns_none(db):
    event = SimpleNamespace(reply=None, get_message=lambda: [])
    assert handlers.reply_message_id(event) is None


def test_event_without_reply_attribute_returns_none(db):
    """连 `reply` 属性都没有的事件（某些实现/事件类型）不能抛 AttributeError。"""
    assert handlers.reply_message_id(SimpleNamespace()) is None


def test_reply_id_zero_is_treated_as_no_reply(db):
    """`id=0` 是实现「找不到原消息」时的占位值。

    如果当成真 id 去查表，永远查不到 → 用户看到的是「这条引用我不认识」，
    而真实原因是「这条消息根本没引用成功」，两种情况的处置完全不同。
    """
    event = SimpleNamespace(reply=SimpleNamespace(message_id=0))
    assert handlers.reply_message_id(event) is None


def test_reply_id_garbage_is_ignored(db):
    event = SimpleNamespace(reply=SimpleNamespace(message_id="not-a-number"))
    assert handlers.reply_message_id(event) is None


# ── 从发送响应里取 message_id ────────────────────────────────────


def test_extract_message_id_from_data_field(db):
    """OneBot 适配器的 `Bot.send()` 返回的是响应的 `data` 字段本身。"""
    assert _extract_message_id({"message_id": 123, "res_id": 456}) == 123


def test_extract_message_id_from_nested_data(db):
    """也兼容「返回整个响应体」的形状（不同适配器/版本可能如此）。"""
    assert _extract_message_id({"status": "ok", "data": {"message_id": 123}}) == 123


def test_extract_message_id_tolerates_junk(db):
    """取不到就返回 None —— 调用方降级（warning），**不能让发卡片失败**。"""
    assert _extract_message_id(None) is None
    assert _extract_message_id({}) is None
    assert _extract_message_id("unexpected") is None
    assert _extract_message_id({"message_id": None}) is None
    assert _extract_message_id({"message_id": "abc"}) is None


def test_extract_message_id_str_number_is_accepted(db):
    assert _extract_message_id({"message_id": "123"}) == 123
