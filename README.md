# nonebot-plugin-pixiv-novel

pixiv 小说订阅推送插件（NoneBot2 / OneBot V11）。

## 命令

**所有命令默认仅管理员可用**（`PIXIV_ADMIN_ONLY=true`）。

| 命令 | 渠道 | 说明 |
|---|---|---|
| `订阅 <作者id>` | 仅群聊 | 本群订阅该作者新作 |
| `退订 <作者id>` | 仅群聊 | 取消订阅 |
| `订阅列表` | 仅群聊 | 查看本群订阅（渲染成图片） |
| `获取全文 <作品id>` | 群聊 / 私聊 | 拉取小说全文 |

参数支持直接粘贴 pixiv 链接。

## 配置

| 键 | 默认 | 说明 |
|---|---|---|
| `PIXIV_REFRESH_TOKEN` | 空 | pixiv refresh_token（用 `gppt login` 取得）；为空则只注册命令不轮询 |
| `PIXIV_PROXY` | `http://127.0.0.1:1081` | 出网代理，pixiv 域名国内直连不通 |
| `PIXIV_POLL_INTERVAL` | `600` | 轮询间隔（秒），下限 60 |
| `PIXIV_MAX_PUSH_PER_POLL` | `5` | 单轮每个订阅最多推几条 |
| `PIXIV_BLUR_R18` | `true` | R18 **封面**是否高斯模糊（`false` = 完全不打码） |
| `PIXIV_BLUR_RADIUS` | `9` | 高斯模糊半径（**固定像素**，允许 6–12，不随图片尺寸缩放） |
| `PIXIV_R18_PUSH_ENABLED` | `true` | **新作推送**是否包含 R18 作品（`false` = 整条不推） |
| `PIXIV_ADMIN_ONLY` | `true` | 指令是否仅管理员可用 |
| `PIXIV_ADMIN_IDS` | `[]` | 管理员 QQ 名单；空 = 回退 NoneBot 全局 `SUPERUSERS` |
| `PIXIV_TEXT_TARGETS` | `["group"]` | 允许用「获取全文」的渠道，可任意组合：`["group"]` / `["private"]` / `["group","private"]` |
| `PIXIV_R18_TEXT_ALLOW_GROUP` | `false` | **R18 全文**是否允许发群聊（私聊不受此开关限制） |
| `PIXIV_TEXT_MAX_CHARS` | `4000` | 直接发正文的上限，超出改发 txt 文件 |

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

### 订阅列表显示什么

`订阅列表` 会渲染成图片（`nonebot-plugin-htmlkit`），每条显示：
**作者名 · 作者头像 · 订阅时间 · 作者id**（外带作者主页链接）。
- 作者名/头像在**订阅时**顺带存进库；取不到时降级成显示 ID + 首字占位块。
- 头像走**磁盘缓存**（`localstore` 的 cache 目录），不会每次重新下载。
- 渲染失败会自动**回退成纯文本列表**，命令不会「没反应」。

### 其他约定

- **不引用**：回复不引用原消息，直接发。
- **不共享**：订阅是**按群**隔离的，A 群的订阅不会推到 B 群。
- **分发**：只通过 GitHub Release 发布版本，不发 PyPI。

## 前置条件

1. pixiv 账号的「国・地域」设为**日本**（否则 R18 内容受地区限制；本服务器出口在 US，尤其要注意）
2. pixiv 账号打开「显示 R18 内容」
3. 服务器能访问 `127.0.0.1:1081` 代理（pixiv 全域名国内直连不通）
4. 系统有 CJK 字体（渲染订阅列表用；Debian 上装 `fonts-noto-cjk`）
