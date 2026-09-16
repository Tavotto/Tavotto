"""本机 WSGI server：bind 与 listen 之间**不做任何名字解析**。

浏览器模式（`app.main`）与桌面 sidecar（`desktop.SidecarServer`）起 HTTP 服务
都从这里拿 server；werkzeug 的 `make_server` / Flask 的 `app.run` 不再直接用。

缺陷：`werkzeug.serving.BaseWSGIServer` 继承 `http.server.HTTPServer`，而
`HTTPServer.server_bind` 在 `socket.bind` **之后**、`listen` **之前**调
`socket.getfqdn(host)`——按 127.0.0.1 反查主机名去填一个没人读的 `server_name`
（werkzeug 的 `make_environ` 用的是 `server_address[0]`，Flask 读的是自己的
`SERVER_NAME` 配置）。反向 DNS 无回音的机器上这一步卡 30–60 s：离线、公司 DNS
不答 PTR、部分 VPN，以及 GitHub 的 macOS runner。此间端口**已 bind、未 listen**，
而 macOS 对这种端口的 SYN 是丢掉不是 RST——连上来的不是 refused 而是
timed out，浏览器 / 桌面壳 / CLI 的就绪探测全部干等，用户看到的是首开莫名
多等半分钟。证据：PR #376 首跑（run 35024490379）`package (macos-latest, 3.13)`
的 `ready_seconds` = 35.78 s（Linux 0 / Windows 2）；诊断 run 35028309531 的桩日志
停在「socket 已 bind（还没 listen）；getfqdn 前」之后 15 s 无下一行；CI00 基线里
macOS 起服务那步 48 s vs 其它腿 13 s。werkzeug 3.1.8 没有覆写 `server_bind`，
`make_server` 也不接受自定义类——所以这里自己继承一层。

修法：`server_bind` 只调 `socketserver.TCPServer.server_bind`（bind），
`server_name` 直接填 host，`server_port` 照旧——两个属性都保留，只是不再反查。
其余（`ThreadedWSGIServer` 的线程模型、`WSGIRequestHandler`、HTTP/1.1、
`passthrough_errors`、EADDRINUSE 的报错与退出）全部沿用父类。
"""

from __future__ import annotations

import socketserver

from flask import Flask
from flask.cli import show_server_banner
from werkzeug.serving import ThreadedWSGIServer


class LocalWSGIServer(ThreadedWSGIServer):
    """`ThreadedWSGIServer`，但 bind → listen 之间不解析主机名。"""

    def server_bind(self) -> None:
        # 跳过 http.server.HTTPServer.server_bind 里的 socket.getfqdn(host)：
        # 直接调 TCPServer 那层做 bind，再把 HTTPServer 会填的两个属性填上。
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def serve_browser(app: Flask, host: str, port: int) -> None:
    """浏览器模式的服务循环：与 `app.run(host, port, threaded=True)` 逐项等价。

    `app.run` → `run_simple` 今天实际做的事，逐项对照：

    * 启动横幅（`Serving Flask app` / `Debug mode: off`）——照打，顺序也一样
      （横幅在构造 server 之前）；
    * `threaded=True` → `ThreadedWSGIServer`——`LocalWSGIServer` 继承它；
    * `request_handler=None` → `WSGIRequestHandler`，多线程下 HTTP/1.1；
      `passthrough_errors=False`；`ssl_context=None`——全是父类默认值；
    * `srv.log_startup()`（`Running on …` + 开发服务器警告）与
      `Press CTRL+C to quit`——照打；后者少了 werkzeug 给它上的黄色
      ANSI（`_ansi_style` 是私有函数，不为一个颜色去 import 它）；
    * `serve_forever()` 吞 KeyboardInterrupt、退出时 `server_close()`——沿用；
    * debug / reloader / debugger：`app.run` 只在 `FLASK_DEBUG` 环境变量或
      `app.debug` 为真时才开，产品两者都不设，这里**固定关**——不再因为用户
      shell 里碰巧有 `FLASK_DEBUG=1` 就把 werkzeug 的调试器挂到产品上；
    * `WERKZEUG_SERVER_FD` 环境变量 + 监听 socket 置 inheritable：只服务
      reloader 的子进程接管，产品没有 reloader，不做；
    * `.env` / `.flaskenv` 加载：python-dotenv 不是依赖，从来没有生效过，不做；
    * `FLASK_RUN_FROM_CLI` 守卫：只对 `flask run` 有意义，产品入口不是它，不做。
    """
    show_server_banner(False, app.name)
    srv = LocalWSGIServer(host, port, app)
    srv.log_startup()
    srv.log("info", "Press CTRL+C to quit")
    srv.serve_forever()
