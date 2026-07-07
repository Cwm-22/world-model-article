"""每日自动任务总入口（可被 GitHub Actions / Windows 任务计划 / 命令行调用）。

流程：
  1. 运行 `python main.py`（拉取 arXiv -> 评估 -> 生成推文 md）
  2. 把当天 `output/YYYY-MM-DD_top3_推文.md` 转成同名 PDF
  3. 把 PDF 推送到指定渠道：
     - wecom：企业微信群机器人发 file 消息（推荐，电脑关机也能在
              企业微信 App 收到 PDF；通过 WECOM_BOT_KEY 配置）
     - wxauto：wxauto 控制 PC 微信发到『文件传输助手』（需电脑登录微信）
  4. 全过程写日志（控制台 + daily_job.log）

配置项（环境变量）：
  # 邮件推送（推荐，电脑关机也能在手机邮箱 App 收到 PDF 附件）
  SMTP_HOST         SMTP 服务器地址  例：smtp.qq.com
  SMTP_PORT         端口，默认 465（SSL）；587 会用 STARTTLS
  SMTP_USER         登录账号（通常即邮箱地址）
  SMTP_PASS         SMTP 授权码（QQ/163 在邮箱设置开启 SMTP 后生成）
  SMTP_FROM         发件人地址，缺省同 SMTP_USER
  SMTP_TO           收件人地址，逗号分隔多个
  # 企业微信群机器人（可选）
  WECOM_BOT_KEY     企业微信群机器人 Webhook key（或完整 URL）
  # wxauto 控制 PC 微信（可选，需电脑登录微信）
  WX_TARGET         wxauto 模式下微信收件人，默认「文件传输助手」
  WX_DISABLE        wxauto 模式下置 1 跳过发送

用法：
    # 完整流程，发送方式由环境变量 WECOM_BOT_KEY 决定（有则 wecom，否则 wxauto）
    python daily_job.py

    # 跳过发送，仅生成 PDF（调试用）
    python daily_job.py --no-send

    # 显式指定发送方式
    python daily_job.py --mode wecom
    python daily_job.py --mode wxauto --target "文件传输助手"

    # 不跑 main.py，直接转换某天已有 md（回测/补发）
    python daily_job.py --skip-main --date 2026-07-07
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent
LOG_PATH: Path = ROOT / "daily_job.log"

_fmt = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_fmt,
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("daily_job")


def _today(date_override: str | None) -> str:
    return date_override or datetime.now().strftime("%Y-%m-%d")


def run_main_py(skip: bool) -> bool:
    """运行主流水线。返回是否成功。"""
    if skip:
        log.info("已跳过 main.py（--skip-main）。")
        return True
    log.info("步骤 1/3：运行 main.py 流水线 ...")
    py = sys.executable
    start = time.time()
    try:
        run_log = open(ROOT / "run.log", "a", encoding="utf-8")
        proc = subprocess.run(
            [py, "main.py"],
            cwd=str(ROOT),
            stdout=run_log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        run_log.close()
    except Exception as e:
        log.exception("main.py 启动失败: %s", e)
        return False
    elapsed = time.time() - start
    log.info("main.py 退出码 %s，耗时 %.1fs", proc.returncode, elapsed)
    if proc.returncode != 0:
        log.error("main.py 执行失败，详见 run.log。")
        return False
    return True


def find_today_post(today: str) -> Path | None:
    md = ROOT / "output" / f"{today}_top3_推文.md"
    return md if md.exists() else None


def md_to_pdf(md_path: Path) -> Path:
    from src.md2pdf import convert_md_file_to_pdf

    pdf_path = md_path.with_suffix(".pdf")
    convert_md_file_to_pdf(md_path, pdf_path)
    size_kb = pdf_path.stat().st_size / 1024
    log.info("已生成 PDF: %s (%.1f KB)", pdf_path.name, size_kb)
    return pdf_path


def send_via_mail(pdf_path: Path, today: str) -> None:
    from src.email_sender import send_pdf

    log.info("步骤 3/3：通过 SMTP 邮件发送 PDF 附件 ...")
    send_pdf(
        pdf_path,
        subject=f"世界模型论文日报 · {today}（Top3 推文）",
    )
    log.info("邮件已发送。")


def send_via_wecom(pdf_path: Path, title: str | None) -> None:
    from src.wecom_bot import send_pdf

    key = os.getenv("WECOM_BOT_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "未配置 WECOM_BOT_KEY。请到企业微信群 -> 添加机器人 -> 复制 "
            "Webhook URL 的 key 部分，写入 Secret/环境变量。"
        )
    log.info("步骤 3/3：通过企业微信群机器人发送 PDF ...")
    send_pdf(key, pdf_path, title=title)
    log.info("已发送到企业微信群。")


def send_via_wxauto(pdf_path: Path, target: str) -> None:
    from src.wechat_sender import send_files_to_wechat

    log.info("步骤 3/3：通过 wxauto 发送 PDF 到微信「%s」 ...", target)
    send_files_to_wechat([pdf_path], target=target)
    log.info("已发送。")


def _smtp_configured() -> bool:
    """是否已配置 SMTP 必填项。"""
    return all(os.getenv(k, "").strip() for k in
               ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_TO"))


def _resolve_mode(explicit: str | None) -> str:
    """没有显式 --mode 时按配置自动判别：
    优先级 mail > wecom > wxauto。"""
    if explicit:
        return explicit
    if _smtp_configured():
        return "mail"
    if os.getenv("WECOM_BOT_KEY", "").strip():
        return "wecom"
    return "wxauto"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="世界模型日报 每日任务（main.py + 转PDF + 推送）"
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                         help="处理日期（默认今天）")
    parser.add_argument("--skip-main", action="store_true",
                         help="跳过 main.py，直接转换当天已有 md")
    parser.add_argument("--no-send", action="store_true",
                         help="只生成 PDF，不推送（调试用）")
    parser.add_argument("--mode", choices=["mail", "wecom", "wxauto"],
                         help="推送方式；不指定时按配置自动判别（mail>wecom>wxauto）")
    parser.add_argument("--target", default=None,
                         help="wxauto 模式下的微信收件人，默认『文件传输助手』")
    parser.add_argument("--title", default=None,
                         help="企业微信发送 PDF 前附加的标题文本")
    args = parser.parse_args()

    today = _today(args.date)
    log.info("=" * 60)
    log.info("世界模型日报 · 每日任务启动 · 日期=%s", today)
    log.info("=" * 60)

    if not run_main_py(args.skip_main):
        log.error("主流水线失败，今日任务中止。")
        return 1

    md_path = find_today_post(today)
    if md_path is None:
        log.info("今日 (%s) 无新增论文推文（未生成 md），任务正常结束。", today)
        return 0
    log.info("步骤 2/3：转换 %s -> PDF", md_path.name)
    try:
        pdf_path = md_to_pdf(md_path)
    except Exception as e:
        log.exception("MD 转 PDF 失败: %s", e)
        return 2

    if args.no_send:
        log.info("已指定 --no-send，跳过推送。PDF: %s", pdf_path)
        return 0

    mode = _resolve_mode(args.mode)
    title = args.title or f"世界模型论文日报 · {today}（共3篇推荐）"

    try:
        if mode == "mail":
            send_via_mail(pdf_path, today)
        elif mode == "wecom":
            send_via_wecom(pdf_path, title)
        else:
            target = args.target or os.getenv("WX_TARGET", "文件传输助手")
            if os.getenv("WX_DISABLE", "").strip() in ("1", "true", "yes"):
                log.info("WX_DISABLE=1，跳过 wxauto 发送。")
                return 0
            send_via_wxauto(pdf_path, target)
    except Exception as e:
        log.exception("推送失败: %s", e)
        return 3

    log.info("🎉 今日任务完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())