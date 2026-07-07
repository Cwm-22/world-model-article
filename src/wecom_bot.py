"""企业微信群机器人：上传文件并推送 file / text 消息。

适用场景：把每天的推文 PDF 发到一个你自己的企业微信群（电脑关机也能在
企业微信 App 收到 PDF 文件）。

企业微信机器人发文件分两步：
  1. 调用 upload_media 上传文件获得 media_id（有效期 3 天）
  2. 调用 webhook send 发送 msgtype=file 的消息

官方文档：
  - https://developer.work.weixin.qq.com/document/path/91770
  - upload_media: /cgi-bin/webhook/upload_media?key=KEY&type=file
  - send:        /cgi-bin/webhook/send?key=KEY

公共 API：
    send_pdf(bot_key, pdf_path, title=None)
    send_text(bot_key, content)

bot_key：企业微信群机器人 Webhook URL 末尾的 key 部分，
  例如 Webhook 是
  https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc-123
  则 bot_key = abc-123
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import requests

log = logging.getLogger(__name__)

_BASE_SEND = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
_BASE_UPLOAD = "https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media"

# 企业微信 upload_media 限制 20MB
MAX_FILE_BYTES = 20 * 1024 * 1024

# 允许的文件后缀：依据企业微信文档，file 类型必须带后缀
_ALLOWED_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
               ".txt", ".zip", ".rar", ".7z", ".mp4", ".mp3"}


def _normalize_key(bot_key_or_url: str) -> str:
    """兼容传入完整 Webhook URL 或只传 key。"""
    s = bot_key_or_url.strip()
    if s.startswith("http"):
        # 取 ?key= 之后部分
        if "key=" in s:
            return s.split("key=", 1)[1].split("&", 1)[0]
        raise ValueError(f"Webhook URL 缺少 key 参数：{s}")
    return s


def upload_media(bot_key: str, file_path: str | Path) -> str:
    """上传文件，返回 media_id。"""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(p)
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"文件过大 {size} bytes，企业微信上限 {MAX_FILE_BYTES} bytes")

    ext = p.suffix.lower()
    if ext not in _ALLOWED_EXT:
        # .pdf 在白名单里；其它后缀建议自行扩展
        log.warning("文件后缀 %s 不在企业微信 file 白名单内，可能上传失败", ext)

    url = f"{_BASE_UPLOAD}?key={bot_key}&type=file"
    with open(p, "rb") as f:
        # multipart：字段名固定为 media
        files = {"media": (p.name, f, "application/octet-stream")}
        log.info("上传 %s (%.1f KB) 到企业微信 ...", p.name, size / 1024)
        r = requests.post(url, files=files, timeout=120)

    if r.status_code != 200:
        raise RuntimeError(f"upload_media HTTP {r.status_code}: {r.text}")
    data = r.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"upload_media 失败: {data}")
    media_id = data["media_id"]
    log.info("上传成功 media_id=%s", media_id)
    return media_id


def send_file(bot_key: str, media_id: str) -> dict:
    """发 msgtype=file 消息。"""
    payload = {"msgtype": "file", "file": {"media_id": media_id}}
    url = f"{_BASE_SEND}?key={bot_key}"
    r = requests.post(url, json=payload, timeout=60)
    data = r.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"send file 失败: {data}")
    log.info("已发送 file 消息")
    return data


def send_text(bot_key: str, content: str,
              mentioned_list: Iterable[str] = (),
              mentioned_mobile_list: Iterable[str] = ()) -> dict:
    """发 msgtype=text 消息（可选 @人 / @手机号）。"""
    payload: dict = {
        "msgtype": "text",
        "text": {
            "content": content,
            "mentioned_list": list(mentioned_list),
            "mentioned_mobile_list": list(mentioned_mobile_list),
        },
    }
    url = f"{_BASE_SEND}?key={bot_key}"
    r = requests.post(url, json=payload, timeout=60)
    data = r.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"send text 失败: {data}")
    log.info("已发送 text 消息")
    return data


def send_pdf(bot_key: str, pdf_path: str | Path,
             title: str | None = None) -> None:
    """便捷方法：先发可选的标题文本，再发 PDF 文件。"""
    key = _normalize_key(bot_key)
    if title:
        send_text(key, title)
    media_id = upload_media(key, pdf_path)
    send_file(key, media_id)


if __name__ == "__main__":
    # 自检：python -m src.wecom_bot <bot_key> <pdf_path> [title]
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 3:
        print("用法: python -m src.wecom_bot <bot_key_or_url> <pdf_path> [title]")
        sys.exit(1)
    send_pdf(sys.argv[1], sys.argv[2],
             title=sys.argv[3] if len(sys.argv) > 3 else None)
    print("发送完成。")