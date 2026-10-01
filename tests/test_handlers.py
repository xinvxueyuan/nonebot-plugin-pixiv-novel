import pytest
from nonebot_plugin_pixiv_novel import store
from nonebot_plugin_pixiv_novel.handlers import (
    extract_id,
    reply_list,
    reply_subscribe,
    reply_unsubscribe,
)


@pytest.fixture(autouse=True)
def _db(tmp_path):
    store.init(tmp_path / "t.sqlite3")
    yield


def test_extract_id_takes_plain_number():
    assert extract_id("12345") == 12345
    assert extract_id("  12345  ") == 12345


def test_extract_id_takes_id_from_url():
    assert extract_id("https://www.pixiv.net/users/12345") == 12345
    assert extract_id("https://www.pixiv.net/novel/show.php?id=67890") == 67890


def test_extract_id_rejects_garbage():
    assert extract_id("abc") is None
    assert extract_id("") is None
    assert extract_id(None) is None


def test_reply_subscribe_creates_and_reports_history_count():
    # baseline 表示「订阅瞬间作者最新作品 ID 之前有多少篇历史」
    text = reply_subscribe(group_id=100, author_id=200, baseline=555, author_name="作者甲")
    assert "订阅" in text
    assert "作者甲" in text                     # 作者名要显示出来
    assert store.list_by_group(100)[0]["author_id"] == 200


def test_reply_subscribe_stores_author_name_and_avatar():
    reply_subscribe(
        group_id=100,
        author_id=200,
        baseline=555,
        author_name="作者甲",
        author_avatar_url="https://i.pximg.net/avatar.jpg",
    )
    row = store.list_by_group(100)[0]
    assert row["author_name"] == "作者甲"
    assert row["author_avatar_url"] == "https://i.pximg.net/avatar.jpg"


def test_reply_subscribe_works_without_author_name():
    """取不到作者信息时也要能订阅成功（只是显示降级成 ID）。"""
    text = reply_subscribe(100, 200, baseline=555)
    assert "订阅" in text
    assert "200" in text
    assert store.list_by_group(100)[0]["author_name"] == ""


def test_reply_subscribe_twice_says_already():
    reply_subscribe(100, 200, baseline=1, author_name="作者甲")
    text = reply_subscribe(100, 200, baseline=1, author_name="作者甲")
    assert "已经" in text or "重复" in text


def test_reply_unsubscribe_ok_and_not_found():
    reply_subscribe(100, 200, baseline=1, author_name="作者甲")
    assert "退订" in reply_unsubscribe(100, 200)
    assert "没有" in reply_unsubscribe(100, 200)


def test_reply_list_prefers_author_name_and_falls_back_to_id():
    """回退用的纯文本列表：有名字显示名字，没名字显示 ID。"""
    reply_subscribe(100, 200, baseline=1, author_name="作者甲")
    reply_subscribe(100, 300, baseline=1, author_name="")
    text = reply_list(100)
    assert "作者甲" in text
    assert "300" in text
    assert "1." in text and "2." in text


def test_reply_list_empty():
    assert "没有" in reply_list(100)
