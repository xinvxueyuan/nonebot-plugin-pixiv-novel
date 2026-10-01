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

    # ---- R18 新作推送（与上面的全文开关是**两回事**，别混）----
    pixiv_r18_push_enabled: bool = Field(
        default=True,
        description="R18 新作是否推送；false=遇到 R18 新作静默跳过（高水位照常推进）",
    )

    # ---- 权限（所有指令默认仅管理员可用）----
    pixiv_admin_only: bool = Field(
        default=True,
        description="指令是否仅管理员可用；false=所有人可用",
    )
    pixiv_admin_ids: list[int] = Field(
        default_factory=list,
        description="管理员 QQ 名单；留空=回退到 NoneBot 全局 SUPERUSERS",
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
