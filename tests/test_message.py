from dataclasses import dataclass, field

from nonebot_plugin_pixiv_novel.message import build_push, novel_url, user_url


@dataclass
class FakeTag:
    name: str
    translated_name: str | None = None


@dataclass
class FakeUser:
    id: int = 42
    name: str = "作者甲"


@dataclass
class FakeNovel:
    id: int = 900
    title: str = "测试小说"
    x_restrict: int = 0
    tags: list = field(default_factory=lambda: [FakeTag("タグA", "标签A"), FakeTag("タグB")])
    user: FakeUser = field(default_factory=FakeUser)


def test_urls():
    assert novel_url(900) == "https://www.pixiv.net/novel/show.php?id=900"
    assert user_url(42) == "https://www.pixiv.net/users/42"


def test_push_contains_all_required_parts():
    msg = build_push(FakeNovel(), cover=b"", blurred=False)
    text = str(msg)
    assert "测试小说" in text
    assert "作者甲" in text
    assert "https://www.pixiv.net/users/42" in text
    assert "https://www.pixiv.net/novel/show.php?id=900" in text
    assert "タグA" in text and "タグB" in text


def test_non_r18_has_no_warning_marker():
    text = str(build_push(FakeNovel(x_restrict=0), cover=b"", blurred=False))
    assert "R-18" not in text


def test_r18_blurred_marks_warning():
    text = str(build_push(FakeNovel(x_restrict=1), cover=b"", blurred=True))
    assert "R-18" in text
    assert "已模糊" in text


def test_r18_unblurred_says_not_blurred():
    text = str(build_push(FakeNovel(x_restrict=1), cover=b"", blurred=False))
    assert "R-18" in text
    assert "已模糊" not in text


def test_r18g_label():
    assert "R-18G" in str(build_push(FakeNovel(x_restrict=2), cover=b"", blurred=False))


def test_cover_segment_added_only_when_bytes_present():
    with_cover = build_push(FakeNovel(), cover=b"\x89PNG", blurred=False)
    without = build_push(FakeNovel(), cover=b"", blurred=False)
    assert len(with_cover) == len(without) + 1      # 多一个 image 段
