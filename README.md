# nonebot-plugin-pixiv-novel

pixiv 小说订阅推送插件（NoneBot2 / OneBot V11）：按群订阅作者新作自动推送，并支持拉取单篇全文，R18 封面可打码。

[![Python](https://img.shields.io/badge/python-3.10--3.13-blue)](pyproject.toml)

> **qbot 插件** · 宿主仓库：[xinvxueyuan/qbot](https://github.com/xinvxueyuan/qbot)

> - 命令裸词触发（qbot 的 `COMMAND_START=[""]`）：`订阅` / `退订` / `订阅列表` / `获取全文`。
> - 订阅**按群隔离**；R18 的「渠道 / 全文 / 推送」三根轴互相独立，默认偏保守（**R18 全文默认哪里都发不出去**）。
> - 只通过 GitHub Release 分发，不发布到 PyPI。

## 功能特性

- **新作推送**：按群订阅作者，定时轮询该作者新作并推送到群；过滤不可见 / 仅 mypixiv 可见的作品。
- **订阅播种**：订阅时取作者当前最新作品 ID 作为基线，**历史作品不会被一次性补推**。
- **订阅列表**：渲染成图片（`nonebot-plugin-htmlkit`），每条显示 **作者名 · 作者头像 · 订阅时间 · 作者id**（外带作者主页链接）。
  - 作者名/头像在**订阅时**顺带存进库；取不到时降级成显示 ID + 首字占位块。
  - 头像走**磁盘缓存**（`localstore` 的 cache 目录），不会每次重新下载。
  - 渲染失败会自动**回退成纯文本列表**，命令不会「没反应」。
- **获取全文**：内联发正文 → 超出 `PIXIV_TEXT_MAX_CHARS` 改发 `.txt` 文件 → 发送失败再回一条链接（三级降级）。
- **引用取全文**：`获取全文` 不带参数时，引用机器人发过的**单篇作品卡片**即可取全文；引用**系列卡片不猜**是哪一篇，直接回系列链接。
- **被动 URL hook**：消息里出现 pixiv **小说 / 系列**链接时自动回信息卡片（可开关）；同一会话同一作品有去重窗口防刷屏；机器人自己的消息不触发（避免自我循环）。
- **群白名单 / 黑名单**：白名单为空 = 全放行（默认）；黑名单优先于白名单；两者只管群，私聊不受影响。
- **表态反馈**：慢操作（拉卡片 / 取全文）打「处理中 → 完成 / 失败」表情；由**可选**依赖 `nonebot-plugin-message-reaction` 提供，库缺失时插件照常工作。
- **权限闸门**：命令与被动 hook 先过群白名单，再过管理员闸门；被拒会明确回文案（不用 NoneBot 的静默 `permission`）。
- 订阅数据按 `(group_id, author_id)` 存在 SQLite（`localstore` 数据目录），各群之间不共享。

## 安装

### 依赖

- 运行依赖：`nonebot2 >= 2.5.0`、`nonebot-adapter-onebot >= 2.4.0`、`nonebot-plugin-apscheduler >= 0.5.0`、`nonebot-plugin-localstore >= 0.7.4`、`nonebot-plugin-htmlkit >= 0.1.0rc5`、`pixivpy3 >= 3.7.5`、`httpx >= 0.28.0`、`pillow >= 12.0.0`；Python `>=3.10,<3.14`。
- 可选依赖（缺失只降级，不影响插件加载）：
  - `nonebot-plugin-htmlkit` 加载失败 → 「订阅列表」回退纯文本；
  - `nonebot-plugin-message-reaction`（表态）没装 → 没有表情反馈；该库不在 PyPI，由宿主以 git 子模块提供。

### 前置条件

1. pixiv 账号的「国・地域」设为**日本**（否则 R18 内容受地区限制；本服务器出口在 US，尤其要注意）
2. pixiv 账号打开「显示 R18 内容」
3. 服务器能访问 `127.0.0.1:1081` 代理（pixiv 全域名国内直连不通）
4. 系统有 CJK 字体（渲染订阅列表用；Debian 上装 `fonts-noto-cjk`）

### 接入宿主

```bash
git submodule add -b main https://github.com/xinvxueyuan/nonebot-plugin-pixiv-novel.git vendor/nonebot-plugin-pixiv-novel
git -C vendor/nonebot-plugin-pixiv-novel checkout <钉定 SHA>
```

- 宿主 `[tool.nonebot] plugin_dirs` 加一行 `vendor/nonebot-plugin-pixiv-novel/src/plugins`

## 配置

### 环境变量

| 键 | 默认 | 说明 |
|---|---|---|
| `PIXIV_REFRESH_TOKEN` | `""` | pixiv refresh_token（用 `gppt login` 取得）；为空则只注册命令不轮询 |
| `PIXIV_PROXY` | `http://127.0.0.1:1081` | 出网代理，pixiv 域名国内直连不通；留空 = 直连 |
| `PIXIV_POLL_INTERVAL` | `600` | 轮询间隔（秒），下限 60（防触发限流） |
| `PIXIV_MAX_PUSH_PER_POLL` | `5` | 单轮每个订阅最多推几条（1–50），防刷屏 |
| `PIXIV_BLUR_R18` | `true` | R18 **封面**是否高斯模糊（`false` = 完全不打码） |
| `PIXIV_BLUR_RADIUS` | `9` | 高斯模糊半径（**固定像素**，允许 6–12，不随图片尺寸缩放） |
| `PIXIV_COVER_MAX_WIDTH` | `0` | 封面最大宽度：`0` = 发原图（默认）；`>0` 时缩到该宽度再发（0–2400） |
| `PIXIV_R18_PUSH_ENABLED` | `true` | **新作推送**是否包含 R18 作品（`false` = 整条不推） |
| `PIXIV_ADMIN_ONLY` | `false` | 指令是否仅管理员可用（默认所有人；`true` = 仅管理员） |
| `PIXIV_ADMIN_IDS` | `[]` | 管理员 QQ 名单；空 = 回退 NoneBot 全局 `SUPERUSERS`；名单同时决定「谁能绕过全文两轴」 |
| `PIXIV_TEXT_TARGETS` | `["group"]` | 允许用「获取全文」的渠道，可任意组合：`["group"]` / `["private"]` / `["group","private"]`；空列表 = 都不受理 |
| `PIXIV_R18_TEXT_ALLOW_GROUP` | `false` | **R18 全文**是否允许发群聊（私聊不受此开关限制） |
| `PIXIV_GROUP_WHITELIST` | `[]` | 群白名单：**只**在列出的群里启用；空 = 关闭白名单（全放行） |
| `PIXIV_GROUP_BLACKLIST` | `[]` | 群黑名单：列出的群**永不**启用（优先级高于白名单）；空 = 关闭 |
| `PIXIV_URL_HOOK_ENABLED` | `true` | 消息里出现 pixiv 小说链接时自动回信息卡片 |
| `PIXIV_URL_HOOK_COOLDOWN` | `60` | 同一会话同一作品的去重窗口（秒，0–3600；`0` = 不去重） |
| `PIXIV_TEXT_MAX_CHARS` | `4000` | 直接发正文的上限，超出改发 txt 文件（下限 100） |

列表字段在 `.env` 里写 JSON，如 `PIXIV_TEXT_TARGETS=["group","private"]`；群号名单里转不成 int 的项会被**丢弃并记 warning**。

### 「R18」相关的**三根正交的轴**（别混）

| 轴 | 配置键 | 管什么 | 默认 |
|---|---|---|---|
| 1. 投递渠道 | `PIXIV_TEXT_TARGETS` | 「获取全文」能在哪些**渠道**用 | 仅群聊 |
| 2. R18 全文 | `PIXIV_R18_TEXT_ALLOW_GROUP` | R18 的**正文全文**能不能进**群** | 不发群 |
| 3. R18 推送 | `PIXIV_R18_PUSH_ENABLED` | **新作推送**要不要带 R18 作品 | 要带 |

轴 2 与轴 3 是两件不同的事：**轴 2 管「全文」，轴 3 管「新作推送」**。
轴 2 又**只管群聊** —— 私聊能不能发 R18 全文只取决于轴 1。

另有一根不是「轴」的开关：`PIXIV_BLUR_R18` 决定 R18 封面**模不模糊**（不影响发不发）。

**默认配置的合并效果**（targets 只有 group + 轴 2 关闭）：**R18 全文哪里都发不出去**。
这是刻意的保守默认。想让管理员在私聊看 R18 全文，把 `PIXIV_TEXT_TARGETS` 加上 `"private"` 即可。

## 使用 / 命令

### 命令表

默认**所有人可用**（`PIXIV_ADMIN_ONLY=false`）；设为 `true` 后仅管理员（`PIXIV_ADMIN_IDS`，名单为空则回退 NoneBot 全局 `SUPERUSERS`）可用。

| 命令 | 别名 | 说明 |
|---|---|---|
| `订阅 <作者id>` | — | 仅群聊。本群订阅该作者新作（订阅时播种基线 + 顺带存作者名/头像） |
| `退订 <作者id>` | — | 仅群聊。取消订阅 |
| `订阅列表` | — | 仅群聊。查看本群订阅（渲染成图片，失败回退纯文本） |
| `获取全文 <作品id>` | — | 群聊 / 私聊（受 `PIXIV_TEXT_TARGETS` 约束）。拉取小说全文；不带参数时也可**引用机器人发的作品卡片** |

参数支持直接粘贴 pixiv 链接。「获取全文」对**管理员**会绕过渠道与 R18 两根轴（见配置）。

### 其他约定

- **不引用**：回复不引用原消息，直接发。
- **不共享**：订阅是**按群**隔离的，A 群的订阅不会推到 B 群。
- **分发**：只通过 GitHub Release 发布版本，不发 PyPI。

## 开发与测试

### 开发环境

- dev 依赖（`pyproject.toml` 的 `dependency-groups.dev`）：`pytest`、`pytest-asyncio`（`asyncio_mode = "auto"`）、`ruff`、`nonebot-plugin-alconna`（「表态接线」集成测试用；该库不在 PyPI，宿主以 git 子模块提供）。
- 测试数据在 `tests/fixtures/`（真实 pixiv 响应的 JSON 快照）。
- `scripts/` 下是抓取真实 pixiv 响应、量渲染布局的探针脚本，不在 `pytest` 收集范围内（`testpaths = ["tests"]`）。

### 测试与门禁

```bash
uv run python -m pytest tests/ -q
uv run ruff check .
```

本仓未配置 pyright（`pyproject.toml` 里没有 `[tool.pyright]`），`ruff format` 也未纳入门禁 —— 门禁以上面两条为准。

## 许可证与相关仓库

### 许可证

MIT（见 `pyproject.toml` 的 `license` 字段；仓库暂未附 `LICENSE` 文件）。

### 相关仓库

- [xinvxueyuan/qbot](https://github.com/xinvxueyuan/qbot)：宿主仓库，本插件以 `vendor/` 子模块方式接入。
- [xinvxueyuan/nonebot-plugin-message-reaction](https://github.com/xinvxueyuan/nonebot-plugin-message-reaction)：可选依赖，提供「处理中 → 完成 / 失败」的表态反馈；缺失时插件照常工作。
