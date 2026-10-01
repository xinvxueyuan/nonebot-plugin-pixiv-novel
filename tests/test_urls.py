"""`urls` 纯解析函数的测试 —— 被动 hook 的「认不认得出链接」全靠它。

穷举 URL 变体的理由：这个解析器判错**不会报错**，只会「机器人对链接毫无反应」
或者更糟「拿系列 ID 当作品 ID 去查」（见 urls.py 顶部注释）。
"""

from dataclasses import dataclass, field

import pytest
from nonebot_plugin_pixiv_novel import urls

# ── 单篇作品：各种真实写法都要认出来 ──────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "https://www.pixiv.net/novel/show.php?id=29081479",
        "https://www.pixiv.net/novel/show.php?id=29081479#1",          # 带锚点（第几页）
        "https://www.pixiv.net/novel/show.php?id=29081479&foo=1",      # id 在前
        "https://www.pixiv.net/novel/show.php?foo=1&id=29081479",      # id 不在第一个
        "https://www.pixiv.net/en/novel/show.php?id=29081479",         # 语言前缀
        "https://www.pixiv.net/zh-tw/novel/show.php?id=29081479",      # 带地区的语言前缀
        "http://www.pixiv.net/novel/show.php?id=29081479",             # http
        "https://pixiv.net/novel/show.php?id=29081479",                # 无 www
        "看看这个 https://www.pixiv.net/novel/show.php?id=29081479 挺好看",   # 夹在中文里
        "https://www.pixiv.net/NOVEL/SHOW.PHP?ID=29081479",            # 大小写
    ],
)
def test_parses_novel_url_variants(text):
    assert urls.parse(text) == ("novel", 29081479)


# ── 系列：必须与单篇区分开 ────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "https://www.pixiv.net/novel/series/15744810",
        "https://www.pixiv.net/en/novel/series/15744810",
        "https://www.pixiv.net/novel/series/15744810/",                # 结尾斜杠
        "https://www.pixiv.net/novel/series/15744810?foo=1",
        "https://www.pixiv.net/novel/series/15744810#top",
        "https://pixiv.net/novel/series/15744810",
    ],
)
def test_parses_series_url_variants(text):
    assert urls.parse(text) == ("series", 15744810)


def test_series_is_not_mistaken_for_a_novel_id():
    """**核心回归**：`/novel/series/N` 绝不能当成单篇作品的 N。

    这正是不能用 `handlers.extract_id()`（取第一个 3 位以上数字）的原因 ——
    那会拿系列 ID 去查一个不存在的作品，而且不报错。
    """
    kind, nid = urls.parse("https://www.pixiv.net/novel/series/15744810")
    assert kind == "series"
    assert nid == 15744810


def test_novel_url_is_not_mistaken_for_series():
    kind, nid = urls.parse("https://www.pixiv.net/novel/show.php?id=15744810")
    assert kind == "novel"


# ── 反例：这些不该触发 ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "",
        "普通聊天没有任何链接",
        "https://www.pixiv.net/users/88871942",              # 作者主页 —— 不是小说
        "https://www.pixiv.net/artworks/12345",              # 插画 —— 本插件不管
        "https://www.pixiv.net/member_illust.php?mode=medium&illust_id=1",
        "https://www.pixiv.net/novel/show.php?id=abc",        # id 不是数字
        "https://www.pixiv.net/novel/show.php?foo=1",         # 没有 id
        "https://example.com/novel/show.php?id=12345",        # 别的站
        "https://www.pixivnet.com/novel/show.php?id=1234",    # 相似域名（没有点）
    ],
)
def test_no_false_positives(text):
    assert urls.parse(text) is None


def test_no_digit_count_restriction_on_ids():
    """**刻意不加「ID 至少 N 位」这类规则** —— 钉住这个选择。

    理由：短 ID 若真存在，加了长度限制就会**静默不响应**（最难查的一类故障）；
    而放它过去最坏也只是打一次 API、回一句「取作品失败」，看得见。
    宁可多一次可见的失败，也不要一次看不见的忽略。
    """
    assert urls.parse("https://www.pixiv.net/novel/show.php?id=1") == ("novel", 1)
    assert urls.parse("https://www.pixiv.net/novel/series/12") == ("series", 12)


# ── 一条消息里多个链接 ────────────────────────────────────────────


def test_takes_the_first_link_in_the_message():
    """连贴多个链接时只回第一条 —— 全回会刷屏。"""
    text = ("https://www.pixiv.net/novel/show.php?id=111 和 "
            "https://www.pixiv.net/novel/series/222")
    assert urls.parse(text) == ("novel", 111)

    text2 = ("https://www.pixiv.net/novel/series/222 先系列 后单篇 "
             "https://www.pixiv.net/novel/show.php?id=111")
    assert urls.parse(text2) == ("series", 222)


def test_parse_all_dedupes_and_keeps_order():
    text = ("https://www.pixiv.net/novel/show.php?id=111 "
            "https://www.pixiv.net/novel/series/222 "
            "https://www.pixiv.net/novel/show.php?id=111")
    assert urls.parse_all(text) == [("novel", 111), ("series", 222)]


# ── candidates()：QQ 分享卡片那条路 ──────────────────────────────


@dataclass
class FakeSeg:
    type: str
    data: dict = field(default_factory=dict)


def test_candidates_reads_text_segments():
    msg = [FakeSeg("text", {"text": "看这个 https://www.pixiv.net/novel/show.php?id=999"})]
    assert "id=999" in urls.candidates(msg)
    assert urls.parse(urls.candidates(msg)) == ("novel", 999)


def test_candidates_reads_qq_share_card_json_segment():
    """**关键**：QQ 会把链接转成分享卡片（json 段），这时纯文本里一个链接都没有。

    只用 `get_plaintext()` 会漏掉这大半数情况 → 机器人对贴链接「毫无反应」。
    """
    card = FakeSeg("json", {"data": '{"app":"com.tencent.structmsg","meta":{'
                                    '"detail":{"url":"https://www.pixiv.net/novel/series/15744810"}}}'})
    msg = [FakeSeg("text", {"text": ""}), card]
    text = urls.candidates(msg)
    assert urls.parse(text) == ("series", 15744810)


def test_candidates_accepts_plain_string():
    assert urls.candidates("https://www.pixiv.net/novel/show.php?id=5") == \
        "https://www.pixiv.net/novel/show.php?id=5"


def test_candidates_tolerates_segments_without_data():
    """图片段之类的没有 data / data 不可序列化，不能因此抛异常。"""
    msg = [FakeSeg("image"), FakeSeg("text", {"text": "https://www.pixiv.net/novel/show.php?id=777"})]
    assert urls.parse(urls.candidates(msg)) == ("novel", 777)


def test_candidates_handles_empty_message():
    assert urls.candidates([]) == ""
    assert urls.parse(urls.candidates([])) is None
