"""全局配置加载。

所有可配置项从环境变量（或 .env 文件）读取，未设置时使用合理默认值，
保证本地与 GitHub Actions 云端行为一致。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录：本文件位于 <root>/src/config.py，故父目录的父目录即根目录
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# 加载 .env（存在时）。CI 环境通常通过 secrets 注入，无 .env 文件也无妨。
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    """读环境变量，空字符串视为未设置，返回默认值。

    GitHub Actions 把未配置的 Secret 注入为空串而非 unset，
    直接 int(os.getenv(...)) 会抛 ValueError，故统一走本函数兜底。
    """
    v = os.getenv(name, "")
    return v.strip() or default


def _int(name: str, default: int) -> int:
    """读环境变量为 int，空串/非法值回退到默认。"""
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool = True) -> bool:
    """读环境变量开关：'1'/'true'/'yes' 为真，'0'/'false'/'no' 为假。"""
    raw = _env(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


class Config:
    """集中存放运行期配置。"""

    # ---- GLM / OpenAI 兼容 API ----
    # 智谱 GLM 提供兼容 OpenAI 的接口
    api_key: str = _env("GLM_API_KEY") or _env("OPENAI_API_KEY")
    base_url: str = _env(
        "GLM_BASE_URL",
        "https://open.bigmodel.cn/api/coding/paas/v4",
    )
    model: str = _env("GLM_MODEL", "glm-5.2")

    # ---- arXiv 检索 ----
    # 目标分类
    arxiv_categories: list[str] = _env(
        "ARXIV_CATEGORIES", "cs.CV,cs.AI,cs.RO,cs.LG,cs.RO"
    ).split(",")
    # 检索关键词（放宽：覆盖世界模型大领域内的多个语义子集，任一命中即可）
    DEFAULT_KEYWORDS = (
        # 核心：世界模型族
        "world model,world models,world simulator,world simulation,"
        # 生成式 3D / 场景
        "3D scene generation,scene synthesis,generative 3D world,"
        "explorable world,3D world modeling,scene completion,"
        # 视频 / 未来预测
        "video world model,video prediction,future prediction,"
        "action-conditioned video,dreamer,"
        # 具身 / 机器人 / 仿真
        "embodied world simulation,embodied world model,"
        "robot world model,interactive environment,"
        "physics-based simulation,robot learning simulator,"
        # 前向动力学 / 潜空间动力学
        "forward dynamics,latent dynamics,planet model,"
        "recurrent state space model,action-conditioned prediction"
    )
    search_keywords: list[str] = [
        kw.strip() for kw in _env("SEARCH_KEYWORDS", DEFAULT_KEYWORDS).split(",")
        if kw.strip()
    ]
    # 仅保留此日期之后提交的论文（YYYYMMDD，arXiv 日期格式）
    since_date: str = _env("SINCE_DATE", "20260101")
    # 每日拉取数量上限
    max_papers_per_day: int = _int("MAX_PAPERS_PER_DAY", 20)

    # ---- 多数据源开关 ----
    enable_arxiv: bool = _bool("ENABLE_ARXIV", True)
    enable_hf_daily: bool = _bool("ENABLE_HF_DAILY", True)
    enable_openreview: bool = _bool("ENABLE_OPENREVIEW", True)
    enable_semantic_scholar: bool = _bool("ENABLE_SEMANTIC_SCHOLAR", True)
    # Semantic Scholar 关键词检索模式：默认**关闭**。
    # 原因：S2 全文检索命中过宽（"world model" 一词能命 220k 篇），易冲淡候选。
    # S2 仍保留 batch 富化能力（按 arXiv id 反查 venue/citationCount），
    # 这部分由 source_aggregator 在多源去重后自动调用，不依赖本开关。
    enable_s2_search: bool = _bool("ENABLE_S2_SEARCH", False)
    # 各源参数
    hf_lookback_days: int = _int("HF_LOOKBACK_DAYS", 14)
    openreview_since_year: int = _int("OPENREVIEW_SINCE_YEAR", 2024)
    semantic_scholar_since_year: int = _int("SEMANTIC_SCHOLAR_SINCE_YEAR", 2024)
    # 可选的 Semantic Scholar API Key（用于提升配额，无 Key 也能用）
    semantic_scholar_api_key: str | None = _env("SEMANTIC_SCHOLAR_API_KEY") or None

    # ---- 路径 ----
    data_dir: Path = BASE_DIR / "data"
    output_dir: Path = BASE_DIR / "output"
    processed_db_path: Path = data_dir / "processed_papers.json"

    # ---- 评估 ----
    top_k: int = _int("TOP_K", 3)
    # 候选池保留：已评估且未推送的论文，多久内仍参与下次 Top3 选举。
    # 设 None 表示永久保留；设 30 表示最近 30 天评估过的论文才有候选资格。
    # 默认 30：既允许跨天补选好论文，又避免候选池无限膨胀。
    candidate_max_age_days: int | None = _int("CANDIDATE_MAX_AGE_DAYS", 30) or None

    @classmethod
    def ensure_dirs(cls) -> None:
        """确保数据/输出目录存在。"""
        cls.data_dir.mkdir(parents=True, exist_ok=True)
        cls.output_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate(cls) -> None:
        """校验关键配置。缺 API Key 时给出明确提示。"""
        if not cls.api_key:
            raise RuntimeError(
                "未检测到 API Key，请在 .env 或环境变量中设置 GLM_API_KEY。"
                "可参考 .env.example。"
            )


# 启动即确保目录存在
Config.ensure_dirs()
