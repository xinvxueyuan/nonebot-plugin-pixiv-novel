import pytest
from nonebot_plugin_pixiv_novel import store
from nonebot_plugin_pixiv_novel.handlers import (
    extract_id,
    reply_list,
    reply_subscribe,
    reply_unsubscribe,
    safe_filename,
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


# ── 作者信息拉取失败必须警告（否则 ID 打错 → 「✅ 已订阅」→ 永远静默）──


def test_reply_subscribe_warns_when_author_fetch_failed():
    """有 token 但拉不到作者信息（多半是 ID 打错）→ 回复里必须有警告。

    没有这条警告，用户看到「✅ 已订阅」却永远收不到推送，还找不到原因。
    """
    text = reply_subscribe(
        group_id=100, author_id=99999999999, baseline=0, author_fetch_failed=True
    )
    assert "已订阅" in text                 # 订阅仍然生效（不因拉取失败而拒绝）
    assert "⚠️" in text                     # 但必须带警告
    assert "ID" in text                     # 且点明是 ID 可能有问题
    assert store.list_by_group(100)[0]["author_id"] == 99999999999


def test_reply_subscribe_silent_when_fetch_ok():
    """正常路径**不能**出现警告 —— 否则每次订阅都吓唬用户一次。"""
    text = reply_subscribe(group_id=100, author_id=200, baseline=555, author_name="作者甲")
    assert "⚠️" not in text


def test_reply_subscribe_still_warns_when_already_subscribed():
    """重复订阅走「已经订阅过」分支，此时**不该**再叠加 ID 错误警告。

    那个警告是给「第一次订阅且拉不到作者」用的；重复订阅时作者早已在库里，
    再报一次会误导用户以为订阅有毛病。
    """
    reply_subscribe(group_id=100, author_id=200, baseline=555, author_name="作者甲")
    again = reply_subscribe(
        group_id=100, author_id=200, baseline=555, author_fetch_failed=True
    )
    assert "已经订阅过" in again
    assert "⚠️" not in again


# ── 文件名清洗（标题是用户可自定内容）─────────────────────────


def test_safe_filename_blocks_path_separators():
    """标题里的路径分隔符必须被挡住 —— 否则能跳出缓存目录（目录穿越）。"""
    name = safe_filename(123, "../../etc/passwd")
    assert "/" not in name
    assert "\\" not in name
    assert ".." not in name
    assert name.startswith("123_") and name.endswith(".txt")


def test_safe_filename_strips_newlines_and_control_chars():
    """换行/控制字符原样进路径会让 upload_group_file 行为异常。"""
    name = safe_filename(123, "标题\n带换行\t和制表")
    assert "\n" not in name
    assert "\t" not in name
    assert "\r" not in name


def test_safe_filename_falls_back_when_title_is_all_illegal():
    """标题全是非法字符 → 不能产生空文件名，退回 untitled。"""
    assert safe_filename(123, "///\\") == "123_untitled.txt"
    assert safe_filename(123, "") == "123_untitled.txt"


def test_safe_filename_keeps_chinese_and_truncates():
    """中文标题要保留（pixiv 标题多为中文/日文），并截断到上限。"""
    name = safe_filename(123, "百舸川掮客的综漫乐队故事", limit=20)
    assert "百舸川掮客" in name
    body = name[len("123_") : -len(".txt")]
    assert len(body) <= 20

    long_name = safe_filename(123, "长" * 100)
    assert len(long_name[len("123_") : -len(".txt")]) <= 20
