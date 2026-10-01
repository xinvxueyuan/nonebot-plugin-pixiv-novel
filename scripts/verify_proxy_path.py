"""证明「显式代理」这条路径真的走通了。

**为什么必须验**：服务器上没有 TUN，插件只能靠 `PIXIV_PROXY` 走显式代理；
本机 Clash 是纯 TUN 模式（没有任何 mixed-port 监听），所以本机测不了真代理。
而这条路径一旦有问题，服务器上表现为**插件完全拉不到数据**。

做法：起一个最小的 HTTP CONNECT 桩代理，把 `PIXIV_PROXY` 指向它，
然后跑一次真实的 pixiv 请求，看桩代理有没有记录到。

**不需要 token**：用 `auth` 失败也算成功 —— 只要请求经过了桩代理，
就证明 `proxies` 真的生效（不然连桩代理都碰不到）。
"""

from __future__ import annotations

import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "plugins"))

CONNECTS: list[str] = []
PORT = 18899


class _ProxyHandler(socketserver.BaseRequestHandler):
    """最小 CONNECT 代理：记录目标后一律拒绝（不求转发成功，只求证明被访问到）。"""

    def handle(self) -> None:
        self.request.settimeout(5)
        try:
            head = self.request.recv(4096).decode("latin-1", errors="replace")
        except Exception:
            return
        first = head.splitlines()[0] if head else ""
        if first.upper().startswith("CONNECT"):
            CONNECTS.append(first)
            # 拒绝掉，避免真去连外网；我们只关心「请求到过这里」
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        else:
            CONNECTS.append(first)


def main() -> int:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), _ProxyHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    print(f"桩代理已监听 127.0.0.1:{PORT}\n")

    from pixiv_client_probe import run_probe

    ok = run_probe(f"http://127.0.0.1:{PORT}")
    server.shutdown()

    print(f"\n桩代理共收到 {len(CONNECTS)} 个请求：")
    for c in CONNECTS[:8]:
        print(f"  · {c}")

    hit_oauth = any("oauth" in c or "pixiv" in c for c in CONNECTS)
    print()
    if CONNECTS and hit_oauth:
        print("✅ 显式代理路径成立：pixivpy3 的请求确实经过 PIXIV_PROXY")
        print("   → 服务器上 PIXIV_PROXY 能生效（前提是那个端口真有代理在听）")
        return 0
    if CONNECTS:
        print("⚠️ 有请求经过代理，但没看到 pixiv 域名，摘录如上")
        return 1
    print("❌ 完全没有请求经过代理 —— `proxies` 没生效！")
    print("   服务器部署会表现为「插件拉不到任何数据」。")
    print(f"   （run_probe 结果：{ok}）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
