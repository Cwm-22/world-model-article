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
    """读取已评估论文记录。文件不存在/损坏时按空 dict 处理，不致崩溃。

    顺带做一次性数据迁移：旧字段 `processed_at` 自动复制到 `evaluated_at`
    （若 `evaluated_at` 缺失），保证候选池时间过滤不会误删历史条目。
    """
    path = Config.processed_db_path
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    dirty = False
    for aid, rec in data.items():
        if not isinstance(rec, dict):
            continue
        if not rec.get("evaluated_at") and rec.get("processed_at"):
            rec["evaluated_at"] = rec["processed_at"]
            dirty = True
    if dirty:
        try:
            save_processed(data)
        except OSError:
            pass  # 迁移失败也不阻塞读取
    return data


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


def remove_from_pool(paper_id: str) -> bool:
    """从候选池中**彻底删除**一篇论文（连同评估记录）。

    仅当该论文尚未进过推文（未推送）时才允许删除，避免误删历史归档。
    用于 Top1 保障换血循环中"删除评分最低候选"。

    Returns:
        True 表示已删除；False 表示未找到/已推送/不允许删除。
    """
    data = load_processed()
    rec = data.get(paper_id)
    if not rec or rec.get("pushed_at"):
        return False
    data.pop(paper_id, None)
    save_processed(data)
    return True


def candidate_pool(
    *,
    exclude_ids: list[str] | None = None,
    max_age_days: int | None = None,
) -> list[dict[str, Any]]:
    """返回候选池：所有已评估且**未推送**的论文，按 score 降序。

    Args:
        exclude_ids: 临时排除的 arXiv id 列表
        max_age_days: 仅保留最近 N 天评估过的论文；None 则不限（避免池无限膨胀）

    时间字段兼容：优先用 `evaluated_at`，缺失则回退到旧字段 `processed_at`
    （后者是早期版本写入的，避免历史数据被时间过滤误删）。
    """
    data = load_processed()
    exclude = set(exclude_ids or [])
    cutoff = None
    if max_age_days is not None:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

    items: list[dict[str, Any]] = []
    for aid, rec in data.items():
        if aid in exclude:
            continue
        if rec.get("pushed_at"):
            continue
        # 时间过滤：兼容旧字段 processed_at
        ts = rec.get("evaluated_at") or rec.get("processed_at") or ""
        if cutoff and ts and ts < cutoff:
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


def prune_pool(
    *,
    min_score: int = 60,
    min_remove: int = 5,
    max_size: int = 30,
) -> dict[str, Any]:
    """清理候选池。被剔除的论文从 processed_papers.json 中**彻底删除**
    （不再保留为"已评估"记录），为每日新论文腾出空间。

    流程（按顺序执行）：
    1) 取所有未推送的候选，按 score 升序排列；
    2) 第一阶段：剔除所有 score < min_score 的候选；统计剔除数 A；
    3) 第二阶段：若 A < min_remove，从剩余最低分候选继续往下剔，直至
       累计剔除数 >= min_remove 或候选被清空；
    4) 第三阶段：若仍剩 > max_size 篇候选，继续剔除最低分直至 <= max_size。

    注意：已推送论文（pushed_at 有值）不会被本函数触碰——它们留作历史归档。

    Returns:
        {
            "removed_ids": [...],    被剔除的 arxiv_id 列表（升序打分）
            "removed_low_score": A,  因低于 min_score 剔除
            "removed_floor": B,      因 min_remove 兜底剔除
            "removed_overflow": C,  因超额（> max_size）剔除
            "removed_count": A+B+C,
            "remaining_count": 剔除后候选数,
        }
    """
    data = load_processed()
    if not data:
        return {
            "removed_ids": [], "removed_low_score": 0,
            "removed_floor": 0, "removed_overflow": 0,
            "removed_count": 0, "remaining_count": 0,
        }

    # 收集未推送候选（按 score 升序），低分在前
    candidates: list[tuple[str, int]] = []
    for aid, rec in data.items():
        if rec.get("pushed_at"):
            continue
        score = int(rec.get("score") or 0)
        candidates.append((aid, score))
    candidates.sort(key=lambda x: x[1])

    to_remove: set[str] = set()
    floor_count = 0

    # 阶段 1：剔除所有低于 min_score 的
    low_score_ids = [aid for aid, s in candidates if s < min_score]
    to_remove.update(low_score_ids)
    a = len(low_score_ids)

    # 阶段 2：若剔除数 < min_remove，从剩余最低分继续剔
    b = 0
    if a < min_remove:
        for aid, _s in candidates:
            if aid in to_remove:
                continue
            to_remove.add(aid)
            b += 1
            if (a + b) >= min_remove:
                break

    # 阶段 3：若仍超 max_size，继续剔除最低分
    remaining_after_min = [c for c in candidates if c[0] not in to_remove]
    c_count = 0
    if len(remaining_after_min) > max_size:
        for aid, _s in remaining_after_min:
            to_remove.add(aid)
            c_count += 1
            if (len(remaining_after_min) - c_count) <= max_size:
                break

    removed_low_score = a
    removed_floor = b
    removed_overflow = c_count
    total_removed = removed_low_score + removed_floor + removed_overflow

    # 真正从 db 中删除（彻底不保留）
    for aid in to_remove:
        data.pop(aid, None)
    save_processed(data)

    return {
        "removed_ids": list(to_remove),
        "removed_low_score": removed_low_score,
        "removed_floor": removed_floor,
        "removed_overflow": removed_overflow,
        "removed_count": total_removed,
        "remaining_count": len(data) - sum(
            1 for r in data.values() if r.get("pushed_at")
        ),
    }


# ---------------- 兼容旧 API 名（避免破坏老调用方） ----------------
# 旧名 mark_processed 仍可用，等价于登记为已评估（含分数），不改变是否已推送。

def mark_processed(paper_id: str, *, title: str,
                   score: int | float | None = None) -> None:
    """旧名兼容，等价于 mark_evaluated（保持向后兼容）。"""
    mark_evaluated(paper_id, title=title, score=score)