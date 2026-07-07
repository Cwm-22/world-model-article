"""arXiv 检索模块。

使用 `arxiv` PyPI 包（底层走 arXiv 公共 API）按分类 + 关键词检索，
并对结果做日期过滤与标准化。
"""
from __future__ import annotations

import time
from typing import Any

import arxiv

from .config import Config


def _build_query(categories: list[str], keywords: list[str]) -> str:
    """构造 arXiv 查询串。

    arXiv 查询语法：
    - cat:cs.CV  按分类
    - abs:"xxx"  按摘要关键词
    - AND / OR / TI (优先级用括号)
    """
    # 分类：cat:cs.CV OR cat:cs.AI OR cat:cs.RO
    cat_part = " OR ".join(f"cat:{c.strip()}" for c in categories)
    # 关键词：abs:"k1" OR abs:"k2" ...
    kw_part = " OR ".join(f'abs:"{k}"' for k in keywords)
    if kw_part:
        return f"({cat_part}) AND ({kw_part})"
    return cat_part


def _parse_arxiv_date(date_str: str) -> str | None:
    """arXiv 返回的 submittedDate 形如 '20260705123456'，转 'YYYY-MM-DD'。"""
    if not date_str or len(date_str) < 8:
        return None
    try:
        return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
    except (ValueError, IndexError):
        return None


def normalize_entry(entry: Any) -> dict[str, Any]:
    """将 arxiv.Result 标准化为内部 dict 结构。

    供 search_papers 与 fetch_by_ids 复用，保证字段一致。
    """
    raw_date = entry.published.strftime("%Y%m%d")
    # comment 字段常含项目主页 / 代码链接信息
    comment = getattr(entry, "comment", "") or ""
    return {
        "arxiv_id": entry.entry_id.rsplit("/", 1)[-1],  # 2507.12345 或 2507.12345v1
        "title": (entry.title or "").strip().replace("\n", " "),
        "summary": (entry.summary or "").strip().replace("\n", " "),
        "authors": [a.name for a in entry.authors],
        "arxiv_url": entry.entry_id,
        "submitted_date": _parse_arxiv_date(raw_date) or "",
        "comment": comment,
    }


def search_papers() -> list[dict[str, Any]]:
    """检索最新论文并标准化。

    Returns:
        标准化论文 dict 列表，每项含字段：
        - arxiv_id / title / summary / authors / arxiv_url / submitted_date / comment
    """
    query = _build_query(Config.arxiv_categories, Config.search_keywords)
    # 略多于目标数量，以备日期过滤后仍充足
    fetch_n = max(Config.max_papers_per_day * 3, 40)

    client = arxiv.Client()
    search = arxiv.Search(
        query=query,
        max_results=fetch_n,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    results: list[dict[str, Any]] = []
    since = Config.since_date  # 'YYYYMMDD'

    def _collect(entries):
        for entry in entries:
            item = normalize_entry(entry)
            if since and item["submitted_date"].replace("-", "") < since:
                continue
            results.append(item)
            if len(results) >= Config.max_papers_per_day:
                break

    try:
        _collect(client.results(search))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] arXiv 检索异常：{e}（已获取 {len(results)} 篇）")

    # 轻量重试：完全为空时等几秒再来一次
    if not results:
        print("[info] 首次无结果，5 秒后重试一次 ...")
        time.sleep(5)
        try:
            _collect(client.results(search))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 重试仍失败：{e}")

    return results


def fetch_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    """按 arXiv ID 列表拉取论文，用于复盘/重新生成推文。

    Args:
        ids: 形如 ["2607.05352v1", "2607.02865v1"]，顺序会被保持。
    """
    client = arxiv.Client()
    search = arxiv.Search(id_list=ids)
    got: dict[str, dict[str, Any]] = {}
    try:
        for entry in client.results(search):
            item = normalize_entry(entry)
            # 用去掉版本号的 id 作为 key，方便匹配
            key = item["arxiv_id"].split("v")[0]
            got[key] = item
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 按 ID 拉取异常：{e}")

    # 按传入顺序返回
    ordered: list[dict[str, Any]] = []
    for _id in ids:
        key = _id.split("v")[0]
        if key in got:
            ordered.append(got[key])
    return ordered


if __name__ == "__main__":
    # 独立调试入口
    for p in search_papers()[:3]:
        print(p["arxiv_id"], p["submitted_date"], p["title"][:60])