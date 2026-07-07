"""通过 SMTP 把 PDF 作为附件发送到指定邮箱。

适用场景：GitHub Actions 跑完流水线后，把当天的推文 PDF 作为邮件附件
推送到你自己邮箱；手机邮箱 App 收到推送后点开即可看完整 PDF。
电脑关机也能收到（云端 Actions 发送）。

支持常见邮箱的 SMTP：
  - QQ 邮箱：smtp.qq.com:465 (SSL)，授权码在 设置->账户->开启 SMTP 后生成
  - 163 邮箱：smtp.163.com:465 (SSL)
  - Gmail：smtp.gmail.com:465 (SSL)，需应用专用密码
  - 其它邮箱同理，指定对应 host/port 即可

配置项（环境变量）：
  SMTP_HOST       SMTP 服务器地址  例：smtp.qq.com
  SMTP_PORT       端口，默认 465（SSL）
  SMTP_USER       登录账号（一般就是邮箱地址）
  SMTP_PASS       SMTP 授权码（注意不是邮箱登录密码）
  SMTP_FROM       发件人地址，默认同 SMTP_USER
  SMTP_TO         收件人地址（可逗号分隔多个）

公共 API：
    send_pdf(pdf_path, subject=None, body=None)
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


def _list(v: str | Iterable[str]) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v)


def send_pdf(pdf_path: str | Path,
             subject: str | None = None,
             body: str | None = None,
             *, host: str | None = None, port: int | None = None,
             user: str | None = None, pwd: str | None = None,
             to: str | Iterable[str] | None = None) -> None:
    """把 PDF 作为附件发送。

    参数缺省时从环境变量读取：
      SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM / SMTP_TO
    """
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(p)

    host = host or os.getenv("SMTP_HOST", "").strip()
    port = int(port or os.getenv("SMTP_PORT", "465"))
    user = user or os.getenv("SMTP_USER", "").strip()
    pwd = pwd or os.getenv("SMTP_PASS", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip() or user
    to_list = _list(to) if to else _list(os.getenv("SMTP_TO", ""))
    if not (host and user and pwd and sender and to_list):
        raise RuntimeError(
            "SMTP 配置不完整。请设置环境变量：SMTP_HOST / SMTP_USER / "
            "SMTP_PASS / SMTP_TO（FROM 自动同 USER，可用 SMTP_FROM 覆盖）。"
        )

    subject = subject or f"世界模型论文日报 · {p.stem}"
    body = body or (
        "附件为今日世界模型论文 Top3 推文 PDF，可在手机邮箱 App 直接打开查看。\n"
        "由 GitHub Actions 自动生成并发送。"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    msg.set_content(body)

    data = p.read_bytes()
    msg.add_attachment(
        data,
        maintype="application",
        subtype="pdf",
        filename=p.name,
    )

    log.info("通过 SMTP %s:%d 发送 %s (%.1f KB) 给 %s ...",
             host, port, p.name, len(data) / 1024, to_list)

    # 465 用 SMTP_SSL；587 用 STARTTLS；其它端口先试 SSL。
    if port == 587:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.ehlo(); s.starttls(context=ctx); s.ehlo()
            s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(host, port, timeout=60) as s:
            s.login(user, pwd)
            s.send_message(msg)

    log.info("邮件已发送。")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("用法: python -m src.email_sender <pdf_path> [subject]")
        print("请先设置环境变量 SMTP_HOST/SMTP_USER/SMTP_PASS/SMTP_TO")
        sys.exit(1)
    send_pdf(sys.argv[1],
             subject=sys.argv[2] if len(sys.argv) > 2 else None)
    print("发送完成。")