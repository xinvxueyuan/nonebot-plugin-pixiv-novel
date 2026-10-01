from dataclasses import dataclass, field

import pytest
from nonebot_plugin_pixiv_novel import poller, store
from nonebot_plugin_pixiv_novel.config import Config


@dataclass
class FakeTag:
    name: str


@dataclass
class FakeUser:
    id: int = 42
    name: str = "作者甲"


@dataclass
class FakeImageUrls:
    large: str = "https://i.pximg.net/cover.jpg"


@dataclass
class FakeNovel:
    id: int
    title: str = "小说"
    x_restrict: int = 0
    tags: list = field(default_factory=list)
    user: FakeUser = field(default_factory=FakeUser)
    visible: bool = True
    is_mypixiv_only: bool = False
    image_urls: FakeImageUrls = field(default_factory=FakeImageUrls)


class FakeClient:
    def __init__(self, novels_by_author, detail_map=None):
        self.novels_by_author = novels_by_author
        self.detail_map = detail_map or {}
        self.cover_calls = []

    async def user_novels(self, author_id):
        return self.novels_by_author.get(author_id, [])

    async def novel_detail(self, novel_id):
        return self.detail_map[novel_id]

    async def download_cover(self, url, *, blur, radius):
        self.cover_calls.append((url, blur, radius))
        return b"\xff\xd8fakejpeg"


@pytest.fixture(autouse=True)
def _db(tmp_path):
    store.init(tmp_path / "t.sqlite3")
    yield


async def _noop_send(group_id, message):
    pass


def _cfg(**kw):
    base = {
        "pixiv_max_push_per_poll": 5,
        "pixiv_blur_r18": True,
        "pixiv_blur_radius": 9,
        "pixiv_r18_push_enabled": True,
    }
    base.update(kw)
    return Config(**base)


@pytest.mark.asyncio
async def test_seed_then_push_only_new():
    """第一次轮询只播种，不推送历史作品；之后只推比高水位更新的。"""
    store.subscribe(group_id=100, author_id=200, baseline=0)
    client = FakeClient(
        {200: [FakeNovel(500), FakeNovel(400), FakeNovel(300)]},
        {500: FakeNovel(500, title="新作500")},
    )
    sent = []

    async def send(gid, msg):
        sent.append((gid, str(msg)))

    # 第一次：只播种
    n = await poller.poll_once(client, _cfg(), send=send, send_file=None)
    assert n == 0
    assert sent == []
    assert store.list_by_group(100)[0]["last_seen"] == 500

    # 出现更新的作品 → 推送
    client.novels_by_author[200] = [FakeNovel(600, title="新作600"), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600, title="新作600")
    n = await poller.poll_once(client, _cfg(), send=send, send_file=None)
    assert n == 1
    assert "新作600" in sent[0][1]
    assert store.list_by_group(100)[0]["last_seen"] == 600


@pytest.mark.asyncio
async def test_r18_cover_is_blurred_when_enabled():
    store.subscribe(100, 200, baseline=0)
    client = FakeClient(
        {200: [FakeNovel(500)]},
        {500: FakeNovel(500, x_restrict=1)},
    )
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)   # 播种

    client.novels_by_author[200] = [FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600, x_restrict=1)
    await poller.poll_once(client, _cfg(pixiv_blur_r18=True), send=_noop_send, send_file=None)

    url, blur, radius = client.cover_calls[-1]
    assert blur is True
    assert radius == 9                       # 模糊半径来自配置（固定 px）


@pytest.mark.asyncio
async def test_r18_blur_can_be_disabled_by_config():
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500, x_restrict=1)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    client.novels_by_author[200] = [FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600, x_restrict=1)
    await poller.poll_once(client, _cfg(pixiv_blur_r18=False), send=_noop_send, send_file=None)

    _, blur, _ = client.cover_calls[-1]
    assert blur is False


@pytest.mark.asyncio
async def test_non_r18_cover_never_blurred():
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    client.novels_by_author[200] = [FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600, x_restrict=0)
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)
    assert client.cover_calls[-1][1] is False


@pytest.mark.asyncio
async def test_r18_push_switch_off_skips_r18_novels():
    """`PIXIV_R18_PUSH_ENABLED=false` 时 R18 新作**完全不推**（连封面都不下）。

    注意这跟 `pixiv_blur_r18` / `pixiv_r18_text_allow_group` 是**三根不同的轴**：
    - `pixiv_r18_push_enabled`  → 新作推送**要不要带 R18 这一条**
    - `pixiv_blur_r18`         → 推出去的 R18 封面**模不模糊**
    - `pixiv_r18_text_allow_group` → R18 的**正文全文**能不能进群
    """
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    # 只有一篇新作，且是 R18
    client.novels_by_author[200] = [FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600, x_restrict=1)

    sent = []

    async def send(group_id, message):
        sent.append(message)

    n = await poller.poll_once(
        client, _cfg(pixiv_r18_push_enabled=False), send=send, send_file=None
    )
    assert n == 0
    assert sent == []
    assert client.cover_calls == []      # 被跳过的 R18 连封面都没去下


@pytest.mark.asyncio
async def test_r18_push_switch_off_still_pushes_non_r18():
    """关掉开关只影响 R18，同批次的全年龄新作照推（且封面不模糊）。"""
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    client.novels_by_author[200] = [FakeNovel(700), FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600, x_restrict=1)     # R18
    client.detail_map[700] = FakeNovel(700, x_restrict=0)     # 全年龄

    sent = []

    async def send(group_id, message):
        sent.append(message)

    n = await poller.poll_once(
        client, _cfg(pixiv_r18_push_enabled=False), send=send, send_file=None
    )
    assert n == 1                        # 只推了全年龄那篇
    assert len(sent) == 1
    assert len(client.cover_calls) == 1  # 只有全年龄那篇下了封面
    assert client.cover_calls[0][1] is False   # 非 R18 不模糊


@pytest.mark.asyncio
async def test_r18_push_switch_on_does_push_and_blurs():
    """开关打开（默认）时 R18 新作照推，封面按配置模糊。"""
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    client.novels_by_author[200] = [FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600, x_restrict=1)

    sent = []

    async def send(group_id, message):
        sent.append(message)

    n = await poller.poll_once(
        client, _cfg(pixiv_r18_push_enabled=True), send=send, send_file=None
    )
    assert n == 1
    assert client.cover_calls[-1][1] is True        # 封面被模糊了


@pytest.mark.asyncio
async def test_r18_skipped_by_switch_still_advances_high_watermark():
    """被开关跳过的 R18 作品也要推进高水位，否则下次轮询会反复处理它。"""
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    client.novels_by_author[200] = [FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600, x_restrict=1)
    await poller.poll_once(
        client, _cfg(pixiv_r18_push_enabled=False), send=_noop_send, send_file=None
    )

    row = store.list_by_group(100)[0]
    assert row["last_seen"] == 600        # 高水位推到了 600


@pytest.mark.asyncio
async def test_max_push_per_poll_caps_and_keeps_order():
    """一次出现多篇新作时，按 ID 升序（从旧到新）推，且不超过上限。"""
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    client.novels_by_author[200] = [FakeNovel(900), FakeNovel(800), FakeNovel(700), FakeNovel(500)]
    for i in (700, 800, 900):
        client.detail_map[i] = FakeNovel(i, title=f"新作{i}")
    sent = []

    async def send(gid, msg):
        sent.append(str(msg))

    n = await poller.poll_once(client, _cfg(pixiv_max_push_per_poll=2), send=send, send_file=None)
    assert n == 2
    assert "新作700" in sent[0]           # 先推旧的
    assert "新作800" in sent[1]
    assert store.list_by_group(100)[0]["last_seen"] == 800   # 只推进到已推的最大值


@pytest.mark.asyncio
async def test_invisible_and_mypixiv_only_are_skipped():
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    client.novels_by_author[200] = [
        FakeNovel(600, is_mypixiv_only=True),
        FakeNovel(700, visible=False),
        FakeNovel(500),
    ]
    n = await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)
    assert n == 0
    assert store.list_by_group(100)[0]["last_seen"] == 700   # 跳过但高水位仍前进


@pytest.mark.asyncio
async def test_author_failure_does_not_break_other_authors():
    store.subscribe(100, 200, baseline=0)
    store.subscribe(100, 300, baseline=0)

    class FlakyClient(FakeClient):
        async def user_novels(self, author_id):
            if author_id == 200:
                raise RuntimeError("boom")
            return await super().user_novels(author_id)

    client = FlakyClient({300: [FakeNovel(800)]}, {800: FakeNovel(800, title="别人的新作")})
    n = await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)
    assert n == 0                                            # 300 只播种
    assert store.list_by_group(100)                          # 200 的异常没让循环崩掉
