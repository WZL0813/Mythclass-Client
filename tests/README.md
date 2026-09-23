# 测试

客户端这边只有一个冒烟脚本，专门验它自己手写的那套 Engine.IO v4 帧能不能跟服务端对上话。

（REST 和教师端 WebSocket 的测试在主仓库 [Mythclass](https://github.com/WZL0813/Mythclass) 的 `tests/` 里。）

---

## 先起个服务端

```bash
cd server
$env:PORT=3111        # macOS / Linux: PORT=3111 node src/index.js
node src/index.js
```

服务端在主仓库，客户端这边只当它是「对面那台机器」。

## 再跑

```bash
pip install requests websocket-client
python tests/client-ws-smoke.py
```

期望输出：

```
通过 11 项，失败 0 项
```

## 它测什么

| 项 | 内容 |
|---|---|
| 连接 | 握手、Socket.IO CONNECT、auth 带 token |
| 双向事件 | 收 `command`、回 `command_result`、推 `screen_frame` |
| 心跳 | 发 `heartbeat`、收 `heartbeat:ack` |
| 断线重连 | 断开后自己接回来 |

## 它不测什么

真实屏幕捕获、音频会话、文件监控、托盘、开机自启、进程保护。

这些得在 Windows 一体机上手动走一遍，步骤见 [客户端安装与自启](../docs/客户端安装与自启.md)。

---

© 2025 Ryokuryuneko · [AGPL-3.0](../LICENSE)
