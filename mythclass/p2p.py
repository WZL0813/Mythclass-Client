"""P2P 直连：WebRTC 数据通道

为什么只开 DataChannel、不搞视频轨道：
帧本来就是 JPEG，走 SCTP 比走 RTP 省事得多——不用编解码器、不用协商媒体格式。
浏览器收到就是一段 base64，跟中继那条路的格式一模一样，前端渲染代码不用改。

信令仍然走服务端（offer / answer 转发），连上之后画面直接点对点，不占服务端带宽。

用的是**非 trickle ICE**：双方等候选收齐再发完整的 SDP。
这样就不必单独交换 ice 事件，省掉一大堆边界情况；代价是建连慢一两秒。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

# 公共 STUN：跨网段时帮双方找到彼此的地址。
# 学校网络常有 NAT，没有它就只能同网段直连。
STUN_URLS = [
    "stun:stun.cloudflare.com:3478",
    "stun:stun.l.google.com:19302",
]

GATHER_TIMEOUT = 6.0      # 等候选收齐的上限，秒
OFFER_TIMEOUT = 20.0      # 从收到 offer 到通道建立的上限，秒


class P2PSession:
    """一台机器上的一个 P2P 会话。

    send_signal(event, payload)  往服务端发 answer（信令）
    on_control(event_dict)       收到老师的鼠标键盘，交给原来的处理函数
    log(message)                 写日志
    """

    def __init__(self, send_signal, on_control, log, stun_urls: list[str] | None = None):
        self.send_signal = send_signal
        self.on_control = on_control
        self.log = log
        self.stun_urls = stun_urls if stun_urls is not None else STUN_URLS

        self.active = False          # 数据通道开了吗
        self.frames_sent = 0
        self.started_at = 0.0

        self._pc: RTCPeerConnection | None = None
        self._channel = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="mythclass-p2p", daemon=True).start()

    # ------------------------------ 对外 ------------------------------

    def handle_offer(self, offer: dict) -> None:
        """老师发来 offer，回一个 answer"""
        if not isinstance(offer, dict) or not offer.get("sdp"):
            self.log("收到一个空 offer，忽略")
            return
        self._submit(self._handle_offer(offer))

    def send_frame(self, payload: dict) -> bool:
        """把一帧丢进数据通道。通道没开就返回 False，调用方去走中继"""
        channel = self._channel
        if channel is None:
            return False

        # 自愈：open 事件偶尔会错过（应答方尤其容易），直接看通道自己怎么说
        if not self.active and getattr(channel, "readyState", "") == "open":
            self._mark_open()
        if not self.active:
            return False

        try:
            data = json.dumps(payload, ensure_ascii=False)
            # 必须回到 asyncio 线程里发，别在采集线程里直接碰底层
            self._loop.call_soon_threadsafe(channel.send, data)
            self.frames_sent += 1
            return True
        except Exception as err:
            self.log(f"P2P 发帧失败：{type(err).__name__}: {err}")
            return False

    def close(self) -> None:
        self._submit(self._close())

    def status(self) -> str:
        if self.active:
            return f"直连中（已发 {self.frames_sent} 帧）"
        return "没在用"

    # ------------------------------ 内部 ------------------------------

    def _submit(self, coro) -> None:
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:
            pass  # 循环已经关了，随它去

    def _mark_open(self) -> None:
        if not self.active:
            self.active = True
            self.started_at = time.time()
            self.log("P2P 直连已建立，画面改走直连")

    async def _handle_offer(self, offer: dict) -> None:
        try:
            await self._close()

            config = RTCConfiguration(iceServers=[RTCIceServer(urls=self.stun_urls)])
            pc = RTCPeerConnection(configuration=config)
            self._pc = pc

            @pc.on("datachannel")
            def on_datachannel(channel):  # noqa: ANN001
                self._channel = channel

                @channel.on("open")
                def on_open():
                    self._mark_open()

                @channel.on("close")
                def on_close():
                    if self.active:
                        self.log("P2P 直连断开，退回服务端中继")
                    self.active = False

                # 应答方拿到通道时它可能已经是 open 了，open 事件早就过去了，
                # 所以这儿补一刀，不能只等事件
                if getattr(channel, "readyState", "") == "open":
                    self._mark_open()

                @channel.on("message")
                def on_message(message):
                    if not isinstance(message, str):
                        return
                    try:
                        payload = json.loads(message)
                    except ValueError:
                        return
                    event = payload.get("event") if isinstance(payload, dict) else None
                    if isinstance(event, dict):
                        try:
                            self.on_control(event)
                        except Exception as err:
                            self.log(f"P2P 控制事件处理失败：{err}")

            @pc.on("connectionstatechange")
            def on_state():
                if pc.connectionState in ("failed", "closed"):
                    self.active = False

            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer["sdp"], type=offer.get("type", "offer")))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # 非 trickle：等候选收齐再回 answer，之后就不用再交换 ice 了
            deadline = time.time() + GATHER_TIMEOUT
            while pc.iceGatheringState != "complete" and time.time() < deadline:
                await asyncio.sleep(0.1)

            local = pc.localDescription
            self.send_signal("answer", {"sdp": {"type": local.type, "sdp": local.sdp}})
            self.log(f"已回复 P2P answer（候选状态 {pc.iceGatheringState}）")
        except Exception as err:
            self.log(f"P2P 处理 offer 失败：{type(err).__name__}: {err}")
            await self._close()

    async def _close(self) -> None:
        self.active = False
        self._channel = None
        pc, self._pc = self._pc, None
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                pass
