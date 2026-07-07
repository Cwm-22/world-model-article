"""通过 wxauto 控制已登录的 PC 微信，把文件发给指定对象。

典型用途：把每天生成的推文 PDF 发给「文件传输助手」，
以便手机端微信直接打开查看。

前置条件：
- Windows 上已安装并登录 PC 版微信（窗口保持前台或最小化均可，
  但不能完全退出/锁定）。本项目验证版本：3.9.10.27。
- 已 pip 安装 wxauto（GitHub 发行版：pip install git+https://github.com/cluic/wxauto.git）。

公共 API：
    send_files_to_wechat(file_paths, target="文件传输助手")
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

# 默认收件人：文件传输助手（手机端微信自带，最稳）
DEFAULT_TARGET = "文件传输助手"


def send_files_to_wechat(
    file_paths: list[str | Path] | str | Path,
    target: str = DEFAULT_TARGET,
    retry: int = 2,
    send_interval: float = 3.0,
    init_timeout: float = 15.0,
) -> list[Path]:
    """把一个或多个文件通过 wxauto 发送到微信指定对象。

    Args:
        file_paths: 单个路径或路径列表。
        target: 收件人备注/昵称，默认「文件传输助手」。
        retry: wxauto 初始化失败时的重试次数。
        send_interval: 多文件之间发送间隔（秒），避免微信挤兑。
        init_timeout: 等待微信窗口出现的总时长，用于重试间隔综合。

    Returns:
        成功发送的 PDF 路径列表。

    Raises:
        RuntimeError: 微信未登录 / wxauto 初始化失败。
    """
    if isinstance(file_paths, (str, Path)):
        file_paths = [file_paths]
    paths = [Path(p) for p in file_paths]
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(f"待发送文件不存在: {p}")

    # 延迟导入：缺少 wxauto 时也能 import 本模块（仅调用才报错）
    try:
        from wxauto import WeChat
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "未安装 wxauto。请执行："
            "pip install git+https://github.com/cluic/wxauto.git"
        ) from e

    # 初始化微信连接（带重试，给用户一点反应时间）
    wx = None
    last_err: Exception | None = None
    per_try_wait = max(init_timeout / max(retry, 1), 1.5)
    for attempt in range(1, retry + 1):
        try:
            log.info("wxauto 初始化中（第 %d/%d 次）...", attempt, retry)
            wx = WeChat()
            break
        except Exception as e:  # wxauto 抛各种异常
            last_err = e
            log.warning("微信连接失败（%s），%.1fs 后重试", e, per_try_wait)
            time.sleep(per_try_wait)

    if wx is None:
        raise RuntimeError(
            f"无法连接微信客户端，请确认微信已登录且窗口未最小化到托盘。"
            f"最后错误：{last_err}"
        )

    sent: list[Path] = []
    for i, p in enumerate(paths):
        try:
            log.info("发送文件到「%s」: %s", target, p)
            # wxauto 3.9.x：SendFiles(filepath, who)
            wx.SendFiles(str(p), target)
            sent.append(p)
            if i < len(paths) - 1:
                time.sleep(send_interval)
        except Exception as e:
            log.error("发送失败: %s，错误: %s", p, e)
            raise RuntimeError(f"发送文件失败: {p}") from e

    log.info("已发送 %d 个文件到「%s」", len(sent), target)
    return sent


if __name__ == "__main__":
    # 简单自检：发一个测试 PDF 到文件传输助手
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("用法: python -m src.wechat_sender <file_path> [target]")
        sys.exit(1)
    target = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TARGET
    send_files_to_wechat(sys.argv[1], target)
    print("发送完成。")