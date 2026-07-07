"""本地存储与候选池。

processed_papers.json 结构（每条记录是"已评估过"的论文）：
{
  "arxiv_id": {
      "title": "...",
      "evaluated_at": "2026-07-08T...",     # 已评估（打过分）的时间
      "score": 88,                            # 总分（满分100）
      "scores": { ... },                      # 5 维度分（可选，便于复盘）
      "comment": "...",                       # 一句话评价（可选）
      "sources": ["arxiv", "hf_daily"],       # 来源列表
      "venue": "...",                         # 发表场所（若有）
      "pushed_at": "2026-07-08T...",           # 进推文的时间；**缺省=还在候选池未推送**
      "pushed_post": "2026-07-08_top3_推文.md" # 进了哪期推文（可选）
  },
  ...
}

核心语义（务必分清）：
- 已评估（evaluated_at 有值）：曾经用 GLM 打过分；下次不再重复评估以省 token
- 已推送（pushed_at 有值）：进过 Top3 推文；不再进入候选池
- **候选池**：已评估但还没进过推文的论文集合——每天的 Top3 都从这里选
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .config import Config


# ---------------- 基础 IO ----------------

def load_processed() -> dict[str, dict[str, Any]]:
    """读取已评估论文记录。文件不存在/损坏时按空 dict 处理，不致崩溃。"""
    path = Config.processed_db_path
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_processed(data: dict[str, dict[str, Any]]) -> None:
    """原子化写入记录文件。"""
    path = Config.processed_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


# ---------------- 评估相关 ----------------

def is_evaluated(paper_id: str) -> bool:
    """某 arXiv ID 是否已评估过。"""
    return paper_id in load_processed()


def filter_new(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从候选论文中过滤掉**已评估过**的论文（避免重复花 GLM token 评估）。"""
    done = load_processed()
    return [p for p in papers if p.get("arxiv_id") and p.get("arxiv_id") not in done]


def mark_evaluated(
    paper_id: str,
    *,
    title: str,
    score: int | float | None = None,
    scores: dict[str, int] | None = None,
    comment: str = "",
    venue: str = "",
    sources: list[str] | None = None,
) -> None:
    """把一篇论文登记为已评估（带分数 / 来源 / venue / 评语）。"""
    data = load_processed()
    cur = data.get(paper_id, {})
    cur.update({
        "title": title or cur.get("title", ""),
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    })
    if score is not None:
        cur["score"] = score
    if scores is not None:
        cur["scores"] = scores
    if comment is not None:
        cur["comment"] = comment
    if venue:
        cur["venue"] = venue
    if sources:
        cur["sources"] = sources
    # 不覆盖已有的 pushed_at / pushed_post
    data[paper_id] = cur
    save_processed(data)


# ---------------- 推送相关 ----------------

def is_pushed(paper_id: str) -> bool:
    """某 ID 是否已进过 Top3 推文。"""
    rec = load_processed().get(paper_id, {})
    return bool(rec.get("pushed_at"))


def mark_pushed(paper_ids: list[str], *, post_filename: str = "") -> None:
    """把一篇或多篇论文标记为"已进推文"，从候选池移除。"""
    if not paper_ids:
        return
    data = load_processed()
    now = datetime.now().isoformat(timespec="seconds")
    for aid in paper_ids:
        if aid in data:
            data[aid]["pushed_at"] = now
            if post_filename:
                data[aid]["pushed_post"] = post_filename
    save_processed(data)


def candidate_pool(
    *,
    exclude_ids: list[str] | None = None,
    max_age_days: int | None = None,
) -> list[dict[str, Any]]:
    """返回候选池：所有已评估且**未推送**的论文，按 score 降序。

    Args:
        exclude_ids: 临时排除的 arXiv id 列表
        max_age_days: 仅保留最近 N 天评估过的论文；None 则不限（避免池无限膨胀）
    """
    data = load_processed()
    exclude = set(exclude_ids or [])
    cutoff = None
    if max_age_days is not None:
        # 简化："今天往前 N 天"以日期字符串比较即可
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

    items: list[dict[str, Any]] = []
    for aid, rec in data.items():
        if aid in exclude:
            continue
        if rec.get("pushed_at"):
            continue
        if cutoff and rec.get("evaluated_at", "") < cutoff:
            continue
        item = dict(rec)
        item["arxiv_id"] = aid
        items.append(item)
    items.sort(key=lambda x: (x.get("score") or 0), reverse=True)
    return items


def pool_stats() -> dict[str, int]:
    """候选池统计（用于运行时打印）。"""
    data = load_processed()
    total = len(data)
    pushed = sum(1 for r in data.values() if r.get("pushed_at"))
    return {
        "evaluated": total,
        "pushed": pushed,
        "candidates": total - pushed,
    }


# ---------------- 兼容旧 API 名（避免破坏老调用方） ----------------
# 旧名 mark_processed 仍可用，等价于登记为已评估（含分数），不改变是否已推送。

def mark_processed(paper_id: str, *, title: str,
                   score: int | float | None = None) -> None:
    """旧名兼容，等价于 mark_evaluated（保持向后兼容）。"""
    mark_evaluated(paper_id, title=title, score=score)