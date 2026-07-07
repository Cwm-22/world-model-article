"""结果输出与文件保存。

生成两类文件（按日期命名，便于回溯）：
- output/YYYY-MM-DD_每日论文汇总表.md  评估表格
- output/YYYY-MM-DD_top3_推文.md       推文合集
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config
from .paper_evaluator import DIMENSIONS

# 表格表头：中文名 -> json 字段
DIM_HEADERS: list[tuple[str, str]] = [
    ("创新", "method_innovation"),
    ("权威", "author_authority"),
    ("关联", "topic_relevance"),
    ("落地", "experiment_feasibility"),
    ("资源", "resource_completeness"),
]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _md_escape(text: str) -> str:
    """转义会破坏 Markdown 表格的竖线。"""
    return (text or "").replace("|", "\\|").replace("\n", " ")


def build_summary_md(
    today_evaluated: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    top_k: int,
) -> tuple[str, str]:
    """生成每日汇总表。

    Args:
        today_evaluated: 今日新评估打分过的论文列表
        candidates: 当前候选池全集（已评估未推送），已按分数降序
        top_k: 选取的 Top 数

    Returns:
        (文件名, markdown 内容)
    """
    today = _today()
    lines: list[str] = [
        f"# 世界模型论文日报 · {today}",
        "",
        f"> 多源聚合：arXiv · HF Daily · OpenReview · Semantic Scholar ｜ "
        f"今日新增评估 {len(today_evaluated)} 篇 ｜ 候选池 {len(candidates)} 篇",
        "",
    ]

    # ---- 节 1：今日新增论文打分表 ----
    if today_evaluated:
        lines += [
            "## 📊 今日新增论文评分（按总分降序）",
            "",
            "| 排名 | 标题 | 总分 | " + " | ".join(n for n, _ in DIM_HEADERS)
            + " | 会议/场所 | 被引 | 来源 | 一句话评价 |",
            "|:---:|:---|:---:|" + ":---:|" * len(DIM_HEADERS)
            + ":---|:---:|:---:|:---|",
        ]
        for i, p in enumerate(today_evaluated, 1):
            scores = p.get("scores", {})
            dim_cells = " | ".join(
                str(scores.get(k, 0)) for _, k in DIM_HEADERS
            )
            venue = _md_escape(p.get("venue") or "")
            cite = p.get("citation_count") or 0
            srcs = _md_escape(", ".join(p.get("sources") or []) or "")
            lines.append(
                f"| {i} | {_md_escape(p.get('title', ''))} | "
                f"**{p.get('total_score', 0)}** | {dim_cells} | "
                f"{venue} | {cite} | {srcs} | "
                f"{_md_escape(p.get('comment', ''))} |"
            )
        lines.append("")
    else:
        lines += ["## 📊 今日无新增论文评估", ""]
        lines += ["（仅从候选池既往评分中选取 Top）", ""]

    # ---- 节 2：候选池 Top 评分一览 ----
    lines += [
        f"## 🎯 候选池总览（{len(candidates)} 篇，已评估未推送，按分数降序）",
        "",
        "| 排名 | 总分 | arXiv ID | 标题 | 来源 | 会议/场所 | 是否今日新增 | 一句话评价 |",
        "|:---:|:---:|:---|:---|:---|:---|:---:|:---|",
    ]
    today_ids = {p.get("arxiv_id") for p in today_evaluated}
    for i, c in enumerate(candidates[: Config.top_k * 4], 1):
        venue = _md_escape(c.get("venue") or "")
        srcs = _md_escape(", ".join(c.get("sources") or []) or "")
        is_fresh = "🆕 是" if c.get("arxiv_id") in today_ids else "  否"
        star = "⭐" if i <= top_k else "  "
        lines.append(
            f"| {star} {i} | **{c.get('score', 0)}** | "
            f"`{c.get('arxiv_id', '')}` | "
            f"{_md_escape(c.get('title', ''))} | {srcs} | {venue} | "
            f"{is_fresh} | {_md_escape(c.get('comment', ''))} |"
        )
    lines.append("")

    # ---- 节 3：Top 推荐 ----
    top = candidates[:top_k]
    lines += [f"## 🏆 今日 Top {len(top)} 推荐", ""]
    for i, p in enumerate(top, 1):
        venue = p.get("venue") or ""
        cite = p.get("citation_count") or 0
        is_fresh = "🆕" if p.get("arxiv_id") in today_ids else "📌（候选池历史）"
        meta_extras: list[str] = [is_fresh]
        if venue:
            meta_extras.append(f"📍 {venue}")
        if cite:
            meta_extras.append(f"👥 被引 {cite}")
        lines.append(
            f"{i}. **[{p.get('score', 0)}分] {p.get('title', '')}**  \n"
            f"   {p.get('comment', '')}  \n"
            f"   {'·'.join(p.get('sources') or []) or 'arxiv'}"
            f"  {'  '.join(meta_extras)}  \n"
            f"   🔗 https://arxiv.org/abs/{(p.get('arxiv_id') or '').split('v')[0]}"
        )

    lines.append("")
    content = "\n".join(lines)
    filename = f"{today}_每日论文汇总表.md"
    return filename, content


def build_posts_md(posts: list[str]) -> tuple[str, str]:
    """生成 Top3 推文合集。"""
    today = _today()
    parts = [
        f"# 世界模型 Top 推文 · {today}",
        "",
        f"> 共 {len(posts)} 篇推荐推文，可直接复制至知识星球。",
        "",
        "---",
        "",
    ]
    for i, post in enumerate(posts, 1):
        parts.append(post)
        parts += ["", "---", ""]

    content = "\n".join(parts)
    filename = f"{today}_top3_推文.md"
    return filename, content


def save_text(filename: str, content: str) -> Path:
    """写入 output 目录并返回路径。"""
    Config.output_dir.mkdir(parents=True, exist_ok=True)
    path = Config.output_dir / filename
    path.write_text(content, encoding="utf-8")
    return path
