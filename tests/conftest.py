"""pytest 全局初始化。

**为什么必须有这个文件**：本包的 `__init__.py` 是 NoneBot 插件入口，模块级就调用
`on_command()` / `get_driver()` / `get_plugin_config()`。而测试要
`from nonebot_plugin_pixiv_novel import store` —— 这会**先执行 `__init__.py`**，
没有初始化过的 driver 就会直接抛 `ValueError: NoneBot has not been initialized.`。

`conftest.py` 会被 pytest 最先导入，所以在这里 `nonebot.init()` 正好赶在
任何测试模块 import 插件之前。
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter

# 用 **none 驱动**：单元测试不需要任何服务器/客户端连接，
# 这样就不必为跑测试装 fastapi+uvicorn 这一大坨。
# `~` 前缀告诉 NoneBot 这是内置驱动（避免它当成已安装的第三方包）。
nonebot.init(driver="~none", log_level="WARNING")
nonebot.get_driver().register_adapter(Adapter)
