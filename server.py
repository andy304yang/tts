#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语音对话后端服务
- WebSocket 服务 (port 8765): 接收浏览器音频 → RTASR 识别 → TTS 合成 → 返回音频
- HTTP 服务 (port 8080): 托管前端 HTML
"""
import asyncio
import json
import base64
import hashlib
import hmac
import datetime
import uuid
import urllib.parse
import logging
import threading
import http.server
import functools
import os
import websockets
import aiohttp
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── 讯飞凭证 ────────────────────────────────────────────────────────────────
APPID      = "fd497302"
API_KEY    = "082b45fb21382b473a59e8b84a5e1fb6"
API_SECRET = "ZTE4MjNiYTk2ZTM5M2Q5NDgwMWI3NWMz"


# ─── RTASR 常量 ───────────────────────────────────────────────────────────────
AUDIO_FRAME_SIZE = 1280   # 16k 16bit 单声道 40ms = 1280 bytes
FRAME_INTERVAL   = 0.04   # 40ms

# ─── URL 构造 ─────────────────────────────────────────────────────────────────

def _beijing_now():
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S%z")


def build_rtasr_url():
    params = {
        "accessKeyId": API_KEY,
        "appId":       APPID,
        "uuid":        uuid.uuid4().hex,
        "utc":         _beijing_now(),
        "audio_encode": "pcm_s16le",
        "lang":         "autodialect",
        "samplerate":   "16000",
    }
    sorted_params = dict(sorted(
        (k, v) for k, v in params.items() if v and str(v).strip()
    ))
    base_str = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted_params.items()
    )
    sig = hmac.new(
        API_SECRET.encode(), base_str.encode(), hashlib.sha1
    ).digest()
    params["signature"] = base64.b64encode(sig).decode()
    qs = urllib.parse.urlencode(params)
    return f"wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1?{qs}"


def build_tts_url():
    host = "tts-api.xfyun.cn"
    date = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    sig_origin = f"host: {host}\ndate: {date}\nGET /v2/tts HTTP/1.1"
    sig = base64.b64encode(
        hmac.new(API_SECRET.encode(), sig_origin.encode(), hashlib.sha256).digest()
    ).decode()
    auth = base64.b64encode(
        f'api_key="{API_KEY}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{sig}"'.encode()
    ).decode()
    return (f"wss://tts-api.xfyun.cn/v2/tts"
            f"?authorization={auth}&date={urllib.parse.quote(date)}&host={host}")


# ─── 文本提取（兼容多种 RTASR 返回格式）────────────────────────────────────

def _extract_text(obj) -> str:
    if isinstance(obj, str):
        try:
            return _extract_text(json.loads(obj))
        except Exception:
            return ""
    if not isinstance(obj, dict):
        return ""

    # 直接字段
    for key in ("text", "result", "transcript", "recognition", "asr_result"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # 递归 data
    data = obj.get("data")
    if data:
        t = _extract_text(data)
        if t:
            return t

    # iFlytek RTASR v1 嵌套格式: data.cn.st.rt[].ws[].cw[].w
    cn = obj.get("cn", {})
    st = cn.get("st", {}) if isinstance(cn, dict) else {}
    rt = st.get("rt", []) if isinstance(st, dict) else []
    words = []
    for r in rt:
        for ws in r.get("ws", []):
            for cw in ws.get("cw", []):
                w = cw.get("w", "")
                if w:
                    words.append(w)
    if words:
        return "".join(words)

    return ""


# ─── RTASR 识别 ───────────────────────────────────────────────────────────────

async def recognize_audio(audio_bytes: bytes) -> str:
    url = build_rtasr_url()
    logger.info("Connecting to RTASR ...")

    texts = []
    session_id = None
    done = asyncio.Event()

    try:
        async with websockets.connect(url, ping_interval=None, open_timeout=15) as ws:
            logger.info("RTASR connected, waiting for server ready ...")
            await asyncio.sleep(1.5)

            async def recv_loop():
                nonlocal session_id
                try:
                    async for msg in ws:
                        if not isinstance(msg, str):
                            continue
                        logger.info(f"RTASR ← {msg[:300]}")
                        try:
                            data = json.loads(msg)
                        except Exception:
                            continue
                        # session id
                        d = data.get("data")
                        if (data.get("msg_type") == "action"
                                and isinstance(d, dict) and "sessionId" in d):
                            session_id = d["sessionId"]
                        # text
                        text = _extract_text(data)
                        if text:
                            texts.append(text)
                            logger.info(f"Recognized segment: {text}")
                except websockets.ConnectionClosed:
                    pass
                finally:
                    done.set()

            recv_task = asyncio.create_task(recv_loop())

            # 按帧发送音频（精确 40ms 节奏）
            total = len(audio_bytes)
            start = asyncio.get_event_loop().time()
            frame_idx = 0
            for offset in range(0, total, AUDIO_FRAME_SIZE):
                chunk = audio_bytes[offset: offset + AUDIO_FRAME_SIZE]
                await ws.send(chunk)
                frame_idx += 1
                expected = start + frame_idx * FRAME_INTERVAL
                delay = expected - asyncio.get_event_loop().time()
                if delay > 0:
                    await asyncio.sleep(delay)

            logger.info(f"Audio sent: {total} bytes ({frame_idx} frames)")

            # 发送结束标记
            end_msg: dict = {"end": True}
            if session_id:
                end_msg["sessionId"] = session_id
            await ws.send(json.dumps(end_msg, ensure_ascii=False))
            logger.info("End marker sent")

            # 等待识别结果
            duration = total / (16000 * 2)
            try:
                await asyncio.wait_for(done.wait(), timeout=max(4.0, duration + 4.0))
            except asyncio.TimeoutError:
                logger.info("Recognition timeout, using collected results")

            recv_task.cancel()

    except Exception as e:
        logger.error(f"RTASR error: {e}")

    result = "".join(texts)
    logger.info(f"Final recognized text: '{result}'")
    return result


# ─── 大模型对话（DeepSeek via DashScope）─────────────────────────────────────

LLM_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
LLM_API_KEY = "sk-942ab8947d584741a4a304a9e4167002"
LLM_MODEL   = "deepseek-v4-flash"


async def call_llm(user_text: str, history: Optional[list] = None) -> str:
    """
    调用 DeepSeek（DashScope 兼容模式），流式接收并拼接完整回复。

    参数:
        user_text : 用户本轮输入（RTASR 识别结果）
        history   : 可选多轮历史，格式 [{"role": "user"|"assistant", "content": "..."}]
    """
    messages = list(history or [])
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": False},
        "enable_thinking": False,
    }
    headers = {
        "Authorization": LLM_API_KEY,
        "Content-Type": "application/json",
    }

    parts = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(LLM_API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"LLM HTTP {resp.status}: {body[:200]}")
                    return ""
                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        data = json.loads(chunk)
                        content = data["choices"][0]["delta"].get("content") or ""
                        if content:
                            parts.append(content)
                    except Exception:
                        pass
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return ""

    reply = "".join(parts)
    logger.info(f"LLM reply: '{reply[:100]}'")
    return reply


# ─── TTS 合成 ─────────────────────────────────────────────────────────────────

async def synthesize_speech(text: str) -> bytes:
    url = build_tts_url()
    logger.info(f"Connecting to TTS, text='{text}'")
    chunks = []
    try:
        async with websockets.connect(url, ping_interval=None, open_timeout=15) as ws:
            params = {
                "common": {"app_id": APPID},
                "business": {
                    "aue":    "raw",
                    "auf":    "audio/L16;rate=16000",
                    "vcn":    "xiaoyan",
                    "speed":  50,
                    "volume": 50,
                    "pitch":  50,
                    "bgs":    1,
                    "tte":    "UTF8",
                },
                "data": {
                    "status": 2,
                    "text": base64.b64encode(text.encode("utf-8")).decode(),
                },
            }
            await ws.send(json.dumps(params))
            async for msg in ws:
                if not isinstance(msg, str):
                    continue
                data = json.loads(msg)
                code = data.get("code", -1)
                if code != 0:
                    logger.error(f"TTS error response: {data}")
                    break
                audio_b64 = data["data"]["audio"]
                chunks.append(base64.b64decode(audio_b64))
                logger.info(f"TTS chunk #{len(chunks)}, status={data['data']['status']}")
                if data["data"]["status"] == 2:
                    break
    except Exception as e:
        logger.error(f"TTS error: {e}")

    return b"".join(chunks)


# ─── 浏览器 WebSocket 处理 ────────────────────────────────────────────────────

async def handle_client(websocket):
    logger.info("Browser connected")
    audio_buf = bytearray()

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                audio_buf.extend(message)

            elif isinstance(message, str):
                try:
                    msg = json.loads(message)
                except Exception:
                    continue

                if msg.get("type") != "end":
                    continue

                total = len(audio_buf)
                logger.info(f"Recording ended: {total} bytes")

                if total < AUDIO_FRAME_SIZE:
                    await websocket.send(json.dumps(
                        {"type": "error", "message": "录音太短，请重新录制"}
                    ))
                    audio_buf.clear()
                    continue

                # Step 1: 识别
                await websocket.send(json.dumps({"type": "status", "message": "正在识别语音..."}))
                text = await recognize_audio(bytes(audio_buf))
                audio_buf.clear()

                if not text:
                    await websocket.send(json.dumps(
                        {"type": "error", "message": "未能识别到语音，请重新录制"}
                    ))
                    audio_buf.clear()
                    continue

                # 把识别到的文本发给前端显示
                await websocket.send(json.dumps({"type": "text", "text": text, "role": "user"}))

                # Step 2: 大模型
                await websocket.send(json.dumps({"type": "status", "message": "大模型思考中..."}))
                reply = await call_llm(text)

                if not reply:
                    await websocket.send(json.dumps(
                        {"type": "error", "message": "大模型返回为空，请稍后再试"}
                    ))
                    continue

                # 把模型回复发给前端显示
                await websocket.send(json.dumps({"type": "text", "text": reply, "role": "bot"}))

                # Step 3: TTS 合成
                await websocket.send(json.dumps({"type": "status", "message": "正在合成语音..."}))
                pcm = await synthesize_speech(reply)

                if pcm:
                    audio_b64 = base64.b64encode(pcm).decode()
                    await websocket.send(json.dumps({
                        "type":       "audio",
                        "data":       audio_b64,
                        "sampleRate": 16000,
                    }))
                    logger.info(f"Sent TTS audio: {len(pcm)} bytes")
                else:
                    await websocket.send(json.dumps(
                        {"type": "error", "message": "语音合成失败，请检查 API 凭证"}
                    ))

                await websocket.send(json.dumps({"type": "status", "message": ""}))

    except websockets.ConnectionClosed:
        logger.info("Browser disconnected")
    except Exception as e:
        logger.exception(f"Handler error: {e}")


# ─── HTTP 静态文件服务 ────────────────────────────────────────────────────────

def _start_http():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=base_dir)
    # suppress request logs
    handler.log_message = lambda *a: None
    with http.server.HTTPServer(("", 8080), handler) as httpd:
        logger.info("HTTP server → http://localhost:8080/voice_chat.html")
        httpd.serve_forever()


# ─── 入口 ─────────────────────────────────────────────────────────────────────

async def main():
    threading.Thread(target=_start_http, daemon=True).start()
    logger.info("WebSocket server → ws://localhost:8765")
    async with websockets.serve(handle_client, "0.0.0.0", 8765):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
