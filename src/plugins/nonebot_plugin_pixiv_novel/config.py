"""插件配置。

所有字段都从 NoneBot 全局配置读取（大小写不敏感，字段名大写即环境变量名）。
即 `pixiv_refresh_token` ← `.env.dev` 里的 `PIXIV_REFRESH_TOKEN`。
列表字段在 .env 里写 JSON，如 `PIXIV_TEXT_TARGETS=["group","private"]`。

R18 相关有**三根互相独立的轴 + 一个非轴开关**（详见计划 §2.1 / §2.3 / §2.4），**不要合并**：
  - `pixiv_text_targets`         → 哪些渠道允许用「获取全文」（默认仅 group）
  - `pixiv_r18_text_allow_group` → R18 **全文**能否发群（默认 false，只管群、不管私聊）
  - `pixiv_r18_push_enabled`     → R18 **新作**要不要推送（默认 true）
  - `pixiv_blur_r18` / `pixiv_blur_radius` → R18 **封面**模糊开关与半径（不是「轴」）
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

TextTarget = Literal["group", "private"]


class Config(BaseModel):
    # ---- 凭据 ----
    pixiv_refresh_token: str = Field(
        default="",
        description="pixiv 账号的 refresh_token，用 gppt 取得；空值=插件不启动轮询",
    )

    # ---- 网络 ----
    pixiv_proxy: str = Field(
        default="http://127.0.0.1:1081",
        description="访问 pixiv 的代理地址；pixiv 域名在国内直连不通，留空=直连",
    )

    # ---- 轮询 ----
    pixiv_poll_interval: int = Field(
        default=600, ge=60,
        description="轮询间隔（秒）。下限 60 秒，防止触发 pixiv 限流",
    )
    pixiv_max_push_per_poll: int = Field(
        default=5, ge=1, le=50,
        description="单次轮询里，每个订阅最多推几条，防止一次性刷屏",
    )

    # ---- R18 封面 ----
    pixiv_blur_r18: bool = Field(default=True, description="R18 封面是否高斯模糊")
    pixiv_blur_radius: int = Field(
        default=9, ge=6, le=12,
        description="高斯模糊半径（像素）。取值 6~12，默认 9；固定像素，不随图片尺寸缩放",
    )
    pixiv_cover_max_width: int = Field(
        default=0, ge=0, le=2400,
        description="封面最大宽度（像素）。**0 = 发原图**（默认）；"
        ">0 时服务端把封面缩到该宽度再发，用来控制消息体积"
        "（原图约 1MB、base64 后约 1.3MB；缩到 800 约 156KB）",
    )

    # ---- R18 新作推送（与上面的全文开关是**两回事**，别混）----
    pixiv_r18_push_enabled: bool = Field(
        default=True,
        description="R18 新作是否推送；false=遇到 R18 新作静默跳过（高水位照常推进）",
    )

    # ---- 权限 ----
    # 默认 **False**（2026-10-02 用户拍板）：让群里普通成员也能用这 4 个命令，
    # 这样「渠道轴 / R18 轴」才真的对非管理员生效（否则能进来的人全是管理员，
    # 而管理员按需求可以绕过两轴 → 两轴等于不存在）。
    # 要恢复「仅管理员可用」，把 PIXIV_ADMIN_ONLY 设成 true 即可。
    pixiv_admin_only: bool = Field(
        default=False,
        description="指令是否仅管理员可用；**默认 false=所有人可用**（此时两轴对非管理员生效）",
    )
    pixiv_admin_ids: list[int] = Field(
        default_factory=list,
        description="管理员 QQ 名单；留空=回退到 NoneBot 全局 SUPERUSERS。"
        "注意这个名单同时决定「谁能绕过全文两轴」",
    )

    # ---- 全文投递 · 轴 1：哪些渠道能用「获取全文」----
    pixiv_text_targets: list[TextTarget] = Field(
        default_factory=lambda: ["group"],
        description='允许使用「获取全文」的渠道，可任意组合：'
        '["group"]（默认）/ ["private"] / ["group","private"]；空列表=都不受理',
    )

    # ---- 全文投递 · 轴 2：R18 全文能否发群（与轴 1 独立）----
    pixiv_r18_text_allow_group: bool = Field(
        default=False,
        description="R18 作品的全文是否允许发到群聊；**私聊不受此开关限制**",
    )

    # ---- 被动 URL hook：消息里出现 pixiv 小说链接就回卡片 ----
    pixiv_url_hook_enabled: bool = Field(
        default=True,
        description="群里/私聊里出现 pixiv 小说链接时，自动回一张作品/系列信息卡片",
    )
    pixiv_url_hook_cooldown: int = Field(
        default=60, ge=0, le=3600,
        description="同一作品在同一会话里的去重窗口（秒）；防刷屏，也省 pixiv 配额。0=不去重",
    )

    # ---- 全文长度 ----
    pixiv_text_max_chars: int = Field(
        default=4000, ge=100,
        description="群内直接发出的正文上限；超过则改发 .txt 文件",
    )

    @field_validator("pixiv_text_targets", mode="before")
    @classmethod
    def _normalize_targets(cls, v: object) -> object:
        """去重 + 小写化，保留首次出现的顺序；非法值交给 Literal 报错。

        这样 .env 里写 ["GROUP","group"] 不会变成两条重复渠道。
        """
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for item in v:
            s = str(item).strip().lower()
            if s and s not in out:
                out.append(s)
        return out
