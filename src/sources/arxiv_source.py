"""arXiv 数据源：复用原 src/arxiv_search.py 的检索逻辑。

作为多源检索的一员，方法签名统一为 `fetch()`，返回标准化论文 dict。
"""
from __future__ import annotations

from typing import Any

from ..arxiv_search import normalize_entry, _build_query


class ArxivSource:
    """arXiv 官方 API 检索源（按分类 + 关键词 + 日期过滤）。"""

    name = "arxiv"

    def __init__(self, categories: list[str], keywords: list[str],
                 since_date: str, max_papers: int) -> None:
        self.categories = categories
        self.keywords = keywords
        self.since_date = since_date
        self.max_papers = max_papers

    def fetch(self) -> list[dict[str, Any]]:
        import arxiv

        query = _build_query(self.categories, self.keywords)
        fetch_n = max(self.max_papers * 3, 40)

        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=fetch_n,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        results: list[dict[str, Any]] = []

        try:
            for entry in client.results(search):
                item = normalize_entry(entry)
                if self.since_date and item["submitted_date"].replace("-", "") \
                        < self.since_date:
                    continue
                # 接入统一字段（venue 留空，由 S2 兜底）
                item["source"] = self.name
                results.append(item)
                if len(results) >= self.max_papers:
                    break
        except Exception as e:  # noqa: BLE001
            print(f"[arxiv] 检索异常: {e}（已获取 {len(results)} 篇）")
        return results