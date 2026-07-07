"""OpenReview 数据源：搜索 ICLR / NeurIPS / COLM 等顶会稿件。

OpenReview 现代接口（api2.openreview.net）：
  GET /notes/search?content=all&source=forum&query=...&type=terms&limit=N

返回 notes，每条 note 的 content 形如 {field: {"value": ...}}。
我们抽取 title / abstract / authors / venue / keywords，并用关键词二次过滤。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from .base import simplify

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
)


def _val(content: dict, field: str) -> Any:
    """从 OpenReview content 中取 value。"""
    v = content.get(field)
    if isinstance(v, dict):
        return v.get("value")
    return v


class OpenReviewSource:
    """OpenReview 关键词搜索源。"""

    name = "openreview"

    def __init__(
        self,
        keywords: list[str],
        since_year: int = 2024,
        max_per_keyword: int = 50,
    ) -> None:
        self.keywords = keywords
        self.since_year = since_year
        self.max_per_keyword = max_per_keyword

    def _keyword_hit(self, text: str, keyword: str | None = None) -> bool:
        lower = (text or "").lower()
        if keyword:
            return keyword.lower().strip() in lower
        for kw in self.keywords:
            k = kw.lower().strip()
            if k and k in lower:
                return True
        return False

    def fetch(self) -> list[dict[str, Any]]:
        base = "https://api2.openreview.net/notes/search"
        # 把多关键词 OR 串成一个简短查询串，依赖 OpenReview 的搜索结果相关性
        query = " OR ".join(self.keywords)
        if len(query) > 200:
            query = query[:200]

        params = {
            "content": "all",
            "source": "forum",
            "query": query,
            "type": "terms",
            "limit": 100,
            # 仅论文主贴：通过 source=forum 已经限定；OpenReview 默认按相关性返回
        }

        try:
            r = requests.get(
                base, params=params,
                headers={"User-Agent": _UA}, timeout=30,
            )
            if r.status_code != 200 or not r.content:
                print(f"[openreview] 接口异常 status={r.status_code}")
                return []
            payload = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[openreview] 抓取失败: {e}")
            return []

        notes = payload.get("notes") or []
        print(f"[openreview] 原始命中 {len(notes)} 条，开始过滤 ...")

        results: list[dict[str, Any]] = []
        seen_id: set[str] = set()
        for note in notes:
            content = note.get("content") or {}
            title = simplify(_val(content, "title") or "")
            abstract = simplify(_val(content, "abstract") or "")
            if not title:
                continue
            # 二次关键词过滤：标题或摘要应命中至少一个关键词
            if not self._keyword_hit(f"{title}\n{abstract}"):
                continue

            venue = _val(content, "venue") or _val(content, "venueid") or ""
            authors = _val(content, "authors") or []
            if isinstance(authors, str):
                authors = [authors]
            keywords = _val(content, "keywords") or []
            if isinstance(keywords, str):
                keywords = [keywords]

            # 年份：从 venue 字符串里提取
            year = 0
            m = __import__("re").search(r"(\d{4})", venue or "")
            if m:
                year = int(m.group(1))
            if year and year < self.since_year:
                continue

            paper_id = note.get("id") or note.get("forum") or ""
            if paper_id in seen_id:
                continue
            seen_id.add(paper_id)

            pdf = _val(content, "pdf") or ""
            pdf_url = ""
            if pdf:
                pdf_url = (
                    pdf if pdf.startswith("http")
                    else f"https://api2.openreview.net{pdf}"
                )

            results.append({
                "arxiv_id": "",  # OpenReview 通常无 arXiv id，依赖 dedupe 用标题
                "title": title,
                "summary": abstract,
                "authors": list(authors),
                "arxiv_url": "",  # 可能后续由 S2 反查补 arXiv id
                "submitted_date": f"{year}-01-01" if year else "",
                "source": self.name,
                "venue": venue,
                "keywords": list(keywords),
                "github": _val(content, "github") or "",
                "project_page": "",
                "pdf_url": pdf_url,
                "openreview_id": paper_id,
            })
            if len(results) >= self.max_per_keyword:
                break

        print(f"[openreview] 过滤后 {len(results)} 篇")
        return results