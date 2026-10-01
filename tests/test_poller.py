import io
from dataclasses import dataclass, field

import pytest
from conftest import load_fixture, wrap_json
from nonebot_plugin_pixiv_novel import poller, store
from nonebot_plugin_pixiv_novel.config import Config
from PIL import Image


def _real_png_bytes(size=(640, 1216), color=(200, 60, 60)) -> bytes:
    """真正可解码的图片字节（见下面全链路测试里打桩处的说明）。"""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class FakeTag:
    name: str


@dataclass
class FakeUser:
    id: int = 42
    name: str = "作者甲"


@dataclass
class FakeImageUrls:
    # ⚠️ **必须复刻真实形状**：App 返回的 `large` 带 CDN 缩放段 `/c/240x480_80/`
    # （实测实际只有 240x347）。如果这里写成不带缩放段的假 URL，
    # 「poller 有没有取原图」的接线断言就会**假通过**。
    large: str = (
        "https://i.pximg.net/c/240x480_80/novel-cover-master/img/2026/04/14/16/26/17/"
        "sci15744810_x_master1200.jpg"
    )


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
        self.cover_max_widths = []

    async def user_novels(self, author_id):
        return self.novels_by_author.get(author_id, [])

    async def novel_detail(self, novel_id):
        return self.detail_map[novel_id]

    async def download_cover(self, url, *, blur, radius, max_width=0):
        # 记录 max_width 单独一列，不动下面那些三元素解包的老断言
        self.cover_calls.append((url, blur, radius))
        self.cover_max_widths.append(max_width)
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
async def test_poller_requests_the_original_cover_not_the_thumbnail():
    """⚠️ 接线断言：poller 传给下载的 URL **必须是原图**（没有 `/c/…/` 缩放段）。

    纯函数 `original_cover_url()` 测得再对，**忘了在调用处用**也全绿 ——
    这类「写了函数没人调」的错只有全链路断言抓得到。
    实测 App 的 `image_urls.large` 带 `/c/240x480_80/`（240x347 小图），
    去掉缩放段才是 828x1200 的原图。
    """
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)   # 播种

    client.novels_by_author[200] = [FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600)
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    url = client.cover_calls[-1][0]
    assert url, "这一轮应该真的去下封面了"
    assert "/c/" not in url, f"带缩放段说明还是缩略图，没取原图：{url}"


@pytest.mark.asyncio
async def test_poller_passes_cover_max_width_from_config():
    """配置的封面宽度上限要**真的传到下载层**（默认 0 = 原图）。"""
    store.subscribe(100, 200, baseline=0)
    client = FakeClient({200: [FakeNovel(500)]}, {500: FakeNovel(500)})
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)

    client.novels_by_author[200] = [FakeNovel(600), FakeNovel(500)]
    client.detail_map[600] = FakeNovel(600)
    await poller.poll_once(
        client, _cfg(pixiv_cover_max_width=800), send=_noop_send, send_file=None
    )

    assert client.cover_max_widths[-1] == 800

    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)
    # 默认 0 = 发原图（用户 2026-10-02 拍板）
    client.novels_by_author[200] = [FakeNovel(700), FakeNovel(600)]
    client.detail_map[700] = FakeNovel(700)
    await poller.poll_once(client, _cfg(), send=_noop_send, send_file=None)
    assert client.cover_max_widths[-1] == 0


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


# ══════════════════════════════════════════════════════════════════
# 真实 fixture 端到端：用录下来的 pixiv 真响应跑完整轮询链路
#
# 上面那些用 FakeNovel 的测试是「我相信字段长这样」；
# 这一组是「字段实际就长这样」—— 专门堵住
# 「novel_detail 响应顶层是 {"novel": {...}}」那类静默 bug。
# ══════════════════════════════════════════════════════════════════


class _FixtureClient:
    """从真实 fixture 提供数据的假 client。

    刻意**不做任何解包** —— 它就代表「pixivpy3 的原样响应」，
    真正的解包发生在 PixivClient.novel_detail() 里。
    """

    def __init__(self, novels, detail):
        self._novels = novels
        self._detail = detail
        self.cover_calls = []
        self.cover_max_widths = []

    async def user_novels(self, author_id):
        return self._novels

    async def novel_detail(self, novel_id):
        # 真实 client 的返回值 = 解包后的 novel 本体
        return self._detail

    async def download_cover(self, url, *, blur, radius, max_width=0):
        # 记录 max_width 单独一列，不动下面那些三元素解包的老断言
        self.cover_calls.append((url, blur, radius))
        self.cover_max_widths.append(max_width)
        return b"\xff\xd8\xff\xe0fakejpeg"


@pytest.mark.asyncio
async def test_real_fixture_r18_novel_is_recognised_and_not_pushed_when_switch_off():
    """真实 fixture（x_restrict=1）在 R18 推送开关关闭时**必须**被挡住。

    这是那个 bug 的直接后果验证：修复前 x_restrict 恒为 0，
    这条 R18 作品会被当普通作品推出去。
    """
    fixture = load_fixture("user_novels.json")
    novels = fixture.novels
    detail = load_fixture("novel_detail_r18.json").novel      # 真·解包后的形状

    store.subscribe(100, 61943687, baseline=0)
    store.set_last_seen(100, 61943687, 1)          # 高水位调到最小，让所有作品都算「新」

    client = _FixtureClient(novels, detail)
    n = await poller.poll_once(
        client, _cfg(pixiv_r18_push_enabled=False), send=_noop_send, send_file=None
    )
    assert n == 0                                   # 全是 R18 → 一条都不推
    assert client.cover_calls == []                 # 也不该白白下载封面
    assert store.list_by_group(100)[0]["last_seen"] == max(int(n_.id) for n_ in novels)


@pytest.mark.asyncio
async def test_real_fixture_cover_url_and_blur_actually_flow_through():
    """打开 R18 推送后：封面 URL 要真取到（修复前恒为空）且按配置模糊。

    修复前 `getattr(detail, "image_urls", None)` 恒为 None
    → cover_url 为空 → **永远不下载封面**，需求要的封面图静默消失。
    """
    fixture = load_fixture("user_novels.json")
    detail = load_fixture("novel_detail_r18.json").novel

    store.subscribe(100, 61943687, baseline=0)
    store.set_last_seen(100, 61943687, 1)

    sent = []

    async def _send(group_id, msg):
        sent.append(msg)

    client = _FixtureClient([fixture.novels[0]], detail)
    n = await poller.poll_once(
        client, _cfg(pixiv_r18_push_enabled=True, pixiv_blur_r18=True,
                     pixiv_blur_radius=9),
        send=_send, send_file=None,
    )

    assert n == 1
    assert len(client.cover_calls) == 1
    url, blur, radius = client.cover_calls[0]
    assert url.startswith("https://i.pximg.net/")     # ← 修复前这里是空串
    assert blur is True                               # R18 + 模糊开关 → 要模糊
    assert radius == 9                                # 固定像素半径

    text = sent[0].extract_plain_text()
    assert "R-18" in text                             # R18 标记
    assert "封面已模糊" in text
    assert "むっちむち" in text                        # 真标题（修复前是 None）
    assert "さむしんぐ" in text                        # 真作者名
    assert "29277050" in text                         # 正文链接
    assert "61943687" in text                         # 作者链接


@pytest.mark.asyncio
async def test_real_fixture_r18_off_still_blurs_when_blur_disabled():
    """关闭模糊开关时，R18 封面不模糊但同样要推出去（两根轴互相独立）。"""
    fixture = load_fixture("user_novels.json")
    detail = load_fixture("novel_detail_r18.json").novel

    store.subscribe(100, 61943687, baseline=0)
    store.set_last_seen(100, 61943687, 1)

    sent = []

    async def _send(group_id, msg):
        sent.append(msg)

    client = _FixtureClient([fixture.novels[0]], detail)
    await poller.poll_once(
        client, _cfg(pixiv_r18_push_enabled=True, pixiv_blur_r18=False),
        send=_send, send_file=None,
    )
    assert client.cover_calls[0][1] is False                   # 不模糊
    assert "封面未模糊" in sent[0].extract_plain_text()


@pytest.mark.asyncio
async def test_missing_visible_field_is_treated_as_pushable():
    """JsonDict 缺字段返 None：不能因为「没这个字段」就把作品静默丢掉。

    `bool(getattr(n, "visible", True))` 这种写法在这里会返回 False
    （默认值救不了），导致作品被永久丢弃且**完全不报错**。
    """
    from conftest import wrap_json

    novel = wrap_json({"id": 999, "title": "没 visible 字段"})   # 故意不带 visible
    assert novel.visible is None                                 # 确认前提成立
    assert poller._is_pushable(novel) is True                    # 但必须照推

    hidden = wrap_json({"id": 1, "visible": False})
    assert poller._is_pushable(hidden) is False

    mypixiv = wrap_json({"id": 2, "is_mypixiv_only": True})
    assert poller._is_pushable(mypixiv) is False


@pytest.mark.asyncio
async def test_full_chain_real_pixiv_client_real_fixture(monkeypatch):
    """**最强的一条**：真 PixivClient + 真 fixture + 真 poller，全链路跑通。

    为什么还要这一条：上面 `_FixtureClient` 的 novel_detail 直接返回**已解包**的对象，
    所以它绕过了 `unwrap_novel_shape`。如果把客户端里的解包删掉（变异测试），
    那些用例**照样全绿**。只有让「原始 pixiv 响应」真正流过客户端，
    才能测到解包这一环。

    链路：原始响应 {"novel": {...}} → PixivClient 解包 → poller 读 x_restrict/image_urls
          → build_push 拼消息 → 发送
    """
    import nonebot_plugin_pixiv_novel.pixiv_client as pc_mod
    from nonebot_plugin_pixiv_novel.pixiv_client import PixivClient

    raw_detail = load_fixture("novel_detail_r18.json")          # 原始形状，未解包
    raw_user_novels = load_fixture("user_novels.json")
    assert sorted(raw_detail.keys()) == ["novel"]               # 确认喂进去的是原始响应

    class FakeAPI:
        """冒充 pixivpy3，原样返回录下来的响应（**不做任何解包**）。"""

        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def user_novels(self, author_id):
            # fixture 里有 3 篇；只留 1 篇让断言干净
            return wrap_json({"user": raw_user_novels.user,
                              "novels": [raw_user_novels.novels[0]]})

        def novel_detail(self, novel_id):
            return raw_detail                                  # 原始 {"novel": {...}}

    monkeypatch.setattr(pc_mod, "AppPixivAPI", FakeAPI)

    sent = []
    cover_calls = []

    async def _send(group_id, msg):
        sent.append(msg)

    # ⚠️ 必须打桩 fetch_image：否则真 PixivClient 会真的去下 i.pximg.net，
    # 单测里发真实网络请求 = 慢 + 不稳 + 依赖外网。
    async def _fake_fetch(self, url):
        cover_calls.append(url)
        # ⚠️ 必须是**真能解码**的图片：poller 会调 blur_image()，
        # 假字节会让 PIL 抛异常 → 封面被丢弃、blurred 变 False，
        # 于是断言「封面已模糊」会失败（而且掩盖了真实链路）。
        return _real_png_bytes()

    monkeypatch.setattr(PixivClient, "fetch_image", _fake_fetch)

    store.subscribe(100, 61943687, baseline=0)
    store.set_last_seen(100, 61943687, 1)

    real_client = PixivClient("tok", "")
    n = await poller.poll_once(
        real_client,
        _cfg(pixiv_r18_push_enabled=True, pixiv_blur_r18=True, pixiv_blur_radius=9),
        send=_send, send_file=None,
    )

    assert n == 1
    assert len(cover_calls) == 1                      # 封面 URL 真的从解包后的对象取到了
    assert cover_calls[0].startswith("https://i.pximg.net/")
    text = sent[0].extract_plain_text()
    assert "むっちむち" in text          # 真标题（不解包会是 "None"）
    assert "R-18" in text                # x_restrict 解包后才是 1
    assert "封面已模糊" in text
    assert "さむしんぐ" in text          # 真作者名
    assert "https://www.pixiv.net/novel/show.php?id=29277050" in text


@pytest.mark.asyncio
async def test_full_chain_r18_push_switch_blocks_real_fixture(monkeypatch):
    """同一链路下关掉 R18 推送开关 → 真实 R18 fixture 必须一条都不推。

    修复前 x_restrict 恒为 0，这条会**突破开关**被推出去。
    """
    import nonebot_plugin_pixiv_novel.pixiv_client as pc_mod
    from nonebot_plugin_pixiv_novel.pixiv_client import PixivClient

    raw_detail = load_fixture("novel_detail_r18.json")
    raw_user_novels = load_fixture("user_novels.json")

    class FakeAPI:
        def __init__(self, **kwargs):
            pass

        def auth(self, refresh_token):
            pass

        def user_novels(self, author_id):
            return raw_user_novels

        def novel_detail(self, novel_id):
            return raw_detail

    monkeypatch.setattr(pc_mod, "AppPixivAPI", FakeAPI)

    # 打桩，杜绝单测里发真实网络请求
    async def _no_fetch(self, url):
        raise AssertionError("R18 推送关闭时不该下载封面")

    monkeypatch.setattr(PixivClient, "fetch_image", _no_fetch)

    store.subscribe(100, 61943687, baseline=0)
    store.set_last_seen(100, 61943687, 1)

    n = await poller.poll_once(
        PixivClient("tok", ""),
        _cfg(pixiv_r18_push_enabled=False),
        send=_noop_send, send_file=None,
    )
    assert n == 0
