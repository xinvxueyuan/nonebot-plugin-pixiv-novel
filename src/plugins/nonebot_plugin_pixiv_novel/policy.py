"""权限闸门与投递策略。

**两根正交的轴**（计划 §2.1），不要合并成一个配置：
  - 轴 1 `pixiv_text_targets`         → 群聊/私聊能不能用「获取全文」
  - 轴 2 `pixiv_r18_text_allow_group` → R18 全文能不能进群（只管群，不管私聊）
"""

from __future__ import annotations

from collections.abc import Iterable

_R18_LABEL = {1: "R-18", 2: "R-18G"}


def _as_id_set(ids: Iterable[object]) -> set[str]:
    """统一成字符串集合：NoneBot 的 user_id 是 str，配置里可能写 int。"""
    return {str(i) for i in ids}


def is_admin_identity(
    user_id: object,
    *,
    admin_ids: Iterable[object],
    superusers: Iterable[object],
) -> bool:
    """**纯身份**判定：这个 QQ 号是不是管理员 —— 与「闸门开关」无关。

    为什么必须和 `is_admin()` 分开（这里踩过一个坑）：
    `is_admin()` 在 `admin_only=False` 时对**所有人**返回 True（那是「放行」的语义）。
    如果把它的结果直接当作「可以绕过 R18/渠道限制」的判据，
    那么一旦把 `pixiv_admin_only` 关掉让普通群友也能用命令，
    **每个普通群友都会被当成管理员**，两轴随之彻底失效 —— 改配置等于白改。

    所以两件事分开：
      · 「能不能用这个命令」→ `is_admin()`（含 admin_only 开关）
      · 「是不是管理员本人」→ 本函数（只看名单）
    """
    uid = str(user_id)
    explicit = _as_id_set(admin_ids)
    if explicit:
        return uid in explicit
    return uid in _as_id_set(superusers)


def is_admin(
    user_id: object,
    *,
    admin_only: bool,
    admin_ids: Iterable[object],
    superusers: Iterable[object],
) -> bool:
    """命令闸门：这个用户能不能用命令。

    admin_only=False            → 所有人放行（闸门关掉）
    admin_ids 非空              → 只认这个名单
    admin_ids 为空 且 admin_only → 回退 NoneBot 全局 SUPERUSERS
    """
    if not admin_only:
        return True
    return is_admin_identity(user_id, admin_ids=admin_ids, superusers=superusers)


def decide_text_delivery(
    *,
    channel: str,
    x_restrict: int,
    pixiv_text_targets: Iterable[str],
    pixiv_r18_text_allow_group: bool,
    is_admin: bool = False,
) -> tuple[bool, str]:
    """「获取全文」能否在此渠道投递。返回 `(是否允许, 拒绝原因)`。

    判断顺序（**测试依赖这个顺序，不要改**）：
        ⓪ 管理员直接绕过 → ① 渠道在 targets 内 → ② 非「R18 且群聊且开关未开」→ ③ 放行

    ⓪ 是 2026-10-02 用户要求：「管理员应当可以绕过获取全文的限制」。
    两根轴（渠道 + R18）**都**对管理员失效 —— 管理员要能把任何一篇正文取到手上，
    否则遇到 R18 就没办法在群里排查问题。

    ⚠️ 一个需要知道的相互作用：`pixiv_admin_only` 默认为 True，此时**只有管理员**
    能用这 4 个命令，于是这两根轴对实际使用者等于失效（能进来的人都是管理员）。
    想让两根轴真正生效，要么把 `pixiv_admin_only` 关掉让普通群友也能用命令，
    要么把管理员之外的发放方式另外设计。这是配置层面的取舍，不是这里的 bug。
    """
    # ⓪ 管理员绕过（对所有轴生效）
    if is_admin:
        return True, ""

    # 渠道名做归一化：配置里可能写 "Group"/"GROUP"/带空格。
    # 顺带说明这也是被测试逼出来的 —— 原先只归一化了 targets 不归一化 channel，
    # 于是 targets=["  Group  "] 配得再随意，channel="GROUP" 依然匹配不上。
    channel = str(channel).strip().lower()
    targets = {str(t).strip().lower() for t in pixiv_text_targets}

    # ① 渠道轴
    if channel not in targets:
        where = "群聊" if channel == "group" else "私聊"
        return False, f"当前配置不允许在{where}使用「获取全文」"

    # ② 内容轴：只约束群聊；私聊不被 R18 开关限制
    if x_restrict > 0 and channel == "group" and not pixiv_r18_text_allow_group:
        label = _R18_LABEL.get(x_restrict, "R-18")
        hint = "如需阅读请私聊机器人" if "private" in targets else ""
        return False, (f"{label} 作品全文默认不发群聊。{hint}").strip()

    # ③ 放行
    return True, ""
