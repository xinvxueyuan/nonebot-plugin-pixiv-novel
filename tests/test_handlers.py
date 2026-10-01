import pytest
from nonebot_plugin_pixiv_novel import store
from nonebot_plugin_pixiv_novel.handlers import (
    deliver_novel_text,
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


# ══════════════════════════════════════════════════════════════════
# 正文交付的三级降级（内联 / 文件 / 链接）
#
# 起因：对抗式复查发现，若内联发送因超平台单条上限失败，
# 异常上抛会让用户**什么都收不到**（连链接都没有）。
# ══════════════════════════════════════════════════════════════════


class _Sender:
    """记录调用、可配置在第几次抛异常的假发送器。"""

    def __init__(self, fail_times=0, exc=None, ret=True):
        self.calls = []
        self.fail_times = fail_times
        self.exc = exc or RuntimeError("平台拒收")
        self.ret = ret

    async def send_text(self, msg):
        self.calls.append(("text", msg))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.exc
        return {"message_id": 1}

    async def send_file(self, filename, body):
        self.calls.append(("file", filename, len(body)))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.exc
        return self.ret


async def _deliver(sender, *, text, max_chars=4000, title="标题", novel_id=123,
                   filename="123_t.txt"):
    return await deliver_novel_text(
        text=text, title=title, novel_id=novel_id, filename=filename,
        max_chars=max_chars,
        send_text=sender.send_text, send_file=sender.send_file,
    )


@pytest.mark.asyncio
async def test_deliver_inlines_short_text_and_stops():
    """短正文内联发一次就结束 —— **绝不能**再多发一个文件。

    这是那个 FinishedException 陷阱的回归：若在 `finish()` 外套
    `except Exception`（FinishedException 是 Exception 子类），
    正常成功也会被当成失败，于是多发一条文件消息。
    这里断言「只调用了一次」，把坑锁死。
    """
    s = _Sender()
    assert await _deliver(s, text="短正文") == "inline"
    assert len(s.calls) == 1, f"多发消息了: {s.calls}"
    kind, msg = s.calls[0]
    assert kind == "text"
    assert "短正文" in msg
    assert "https://www.pixiv.net/novel/show.php?id=123" in msg


@pytest.mark.asyncio
async def test_deliver_sends_file_when_too_long():
    s = _Sender()
    assert await _deliver(s, text="长" * 100, max_chars=50) == "file"
    kinds = [c[0] for c in s.calls]
    assert kinds == ["file", "text"]              # 先传文件，再通知一句
    assert "过长" in s.calls[1][1]
    assert "已作为 txt 文件发送" in s.calls[1][1]


@pytest.mark.asyncio
async def test_deliver_falls_back_to_file_when_inline_rejected():
    """内联失败（超平台上限）→ 改发文件，用户仍拿到正文。"""
    s = _Sender(fail_times=1)                      # 第一次 send_text 失败
    assert await _deliver(s, text="短正文") == "file_after_inline"
    kinds = [c[0] for c in s.calls]
    assert kinds == ["text", "file", "text"]
    assert "内联发送失败" in s.calls[-1][1]


@pytest.mark.asyncio
async def test_deliver_falls_back_to_file_when_inline_raises_finished_like():
    """内联失败抛任何异常都要降级 —— 不挑异常类型。"""
    s = _Sender(fail_times=1, exc=ValueError("message too long"))
    assert await _deliver(s, text="短正文") == "file_after_inline"


@pytest.mark.asyncio
async def test_deliver_gives_link_when_both_inline_and_file_fail():
    """内联和文件都失败 → **必须**至少把链接给出去，不能让用户空等。"""
    s = _Sender(fail_times=1)                      # 内联失败
    s.ret = False                                  # 文件「上传失败」（返回 False）
    assert await _deliver(s, text="短正文") == "link"
    last = s.calls[-1]
    assert last[0] == "text"
    assert "show.php?id=123" in last[1]


@pytest.mark.asyncio
async def test_deliver_gives_link_when_file_upload_raises():
    """超长正文 + 上传文件抛异常 → 也要退化到链接。"""
    s = _Sender()
    calls = {"n": 0}

    async def boom_file(filename, body):
        calls["n"] += 1
        raise RuntimeError("upload api down")

    got = await deliver_novel_text(
        text="长" * 100, title="标题", novel_id=123, filename="123_t.txt",
        max_chars=50, send_text=s.send_text, send_file=boom_file,
    )
    assert got == "link"
    assert calls["n"] == 1
    assert "show.php?id=123" in s.calls[-1][1]


@pytest.mark.asyncio
async def test_deliver_returns_empty_for_blank_text():
    s = _Sender()
    assert await _deliver(s, text="   \n  ") == "empty"
    assert s.calls == []                           # 不发任何消息


@pytest.mark.asyncio
async def test_deliver_boundary_uses_inline_at_exactly_max_chars():
    """长度正好等于阈值 → 内联（`<=` 不是 `<`）。"""
    s = _Sender()
    assert await _deliver(s, text="x" * 4000, max_chars=4000) == "inline"
    assert [c[0] for c in s.calls] == ["text"]

    s2 = _Sender()
    assert await _deliver(s2, text="x" * 4001, max_chars=4000) == "file"
    assert [c[0] for c in s2.calls] == ["file", "text"]


def test_finished_exception_is_an_exception_subclass_documented_trap():
    """把这个坑本身固化成测试：`FinishedException` 是 `Exception` 子类。

    只要这行还是 True，就**不能**在 `matcher.finish()` 外面写
    `except Exception` —— 那会把正常结束当成失败。
    `deliver_novel_text` 因此用 `send` 驱动，不用 `finish`。
    """
    import nonebot.exception as nbe

    assert issubclass(nbe.FinishedException, Exception)
    assert issubclass(nbe.SkippedException, Exception)
    # 且它们确实是控制流异常，不是错误
    assert issubclass(nbe.FinishedException, nbe.NoneBotException)
