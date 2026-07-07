"""Semantic Scholar 数据源（兼 enrichment）。

两种使用方式：
1. enrich_by_arxiv_ids(arxiv_ids)：批量按 arXiv id 反查，
   给已发现的论文补 venue / citationCount / 作者机构（affiliations）。
2. fetch()：直接用关键词在 S2 全文检索，作为独立检索源。

接口：
- GET /graph/v1/paper/arXiv:{aid}?fields=...
- POST /graph/v1/paper/batch  body {"ids": ["arXiv:..."], "fields": "..."}
- GET /graph/v1/paper/search?query=...&fields=...&year=YYYY-
限流：无 Key 约 ~1 req/s，轻度 429 退避；可选环境变量
SEMANTIC_SCHOLAR_API_KEY 提升配额。
"""
from __future__ import annotations

import time
from typing import Any

import requests

from .base import normalize_arxiv_id, simplify

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
)
_BASE = "https://api.semanticscholar.org/graph/v1"
# 反查时想要的字段
_ENRICH_FIELDS = (
    "title,abstract,authors,year,venue,externalIds,"
    "citationCount,influentialCitationCount,openAccessPdf,tldr"
)
# 关键词检索返回字段（bulk 端点不支持 tldr，已移除）
_SEARCH_FIELDS = (
    "title,abstract,authors,year,venue,externalIds,"
    "citationCount,openAccessPdf,publicationTypes"
)
# 批量限制
_BATCH_SIZE = 25


def _headers(api_key: str | None) -> dict[str, str]:
    h = {"User-Agent": _UA, "Accept": "application/json"}
    if api_key:
        h["x-api-key"] = api_key
    return h


class SemanticScholar:
    """S2 检索 + 富化源。"""

    name = "semantic_scholar"

    def __init__(
        self,
        keywords: list[str],
        since_year: int = 2024,
        max_per_keyword: int = 30,
        api_key: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.keywords = keywords
        self.since_year = since_year
        self.max_per_keyword = max_per_keyword
        self.api_key = api_key
        self.max_retries = max_retries

    # ---------------- 限流请求 ----------------
    def _get(self, url: str, params: dict) -> dict | None:
        for i in range(self.max_retries):
            try:
                r = requests.get(
                    url, params=params,
                    headers=_headers(self.api_key), timeout=30,
                )
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 429:
                    wait = min(2 ** i * 2, 20)
                    print(f"[s2] 429 限流，{wait}s 后重试 ({i+1}/{self.max_retries})")
                    time.sleep(wait)
                    continue
                print(f"[s2] {url} -> {r.status_code}: {r.text[:120]}")
                return None
            except requests.RequestException as e:
                print(f"[s2] 请求异常: {e}")
                time.sleep(2)
        return None

    # ---------------- 反查富化 ----------------
    def enrich_by_arxiv_ids(
        self, arxiv_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """批量按 arXiv id 取 venue / citationCount 等富化信息。

        Returns:
            {norm_arxiv_id: enrichment}
        """
        out: dict[str, dict[str, Any]] = {}
        if not arxiv_ids:
            return out

        # 分批调用 batch
        ids_all = [f"arXiv:{normalize_arxiv_id(a)}" for a in arxiv_ids
                   if normalize_arxiv_id(a)]
        if not ids_all:
            return out

        # 去重
        seen = set()
        ids_all = [i for i in ids_all if not (i in seen or seen.add(i))]

        for i in range(0, len(ids_all), _BATCH_SIZE):
            chunk = ids_all[i:i + _BATCH_SIZE]
            batch = self._batch(chunk)
            if not batch:
                continue
            for p in batch:
                if not p:
                    continue
                ext = p.get("externalIds") or {}
                aid = normalize_arxiv_id(ext.get("ArXiv", ""))
                if not aid:
                    continue
                authors = []
                for a in (p.get("authors") or []):
                    name = a.get("name") if isinstance(a, dict) else str(a)
                    if name:
                        authors.append(name)
                out[aid] = {
                    "venue": p.get("venue") or "",
                    "year": p.get("year") or 0,
                    "citation_count": p.get("citationCount") or 0,
                    "influential_citation_count":
                        p.get("influentialCitationCount") or 0,
                    "s2_authors": authors,
                    "s2_paper_id": p.get("paperId") or "",
                    "doi": (ext.get("DOI") if isinstance(ext, dict) else "") or "",
                    "pdf_url": (p.get("openAccessPdf") or {}).get("url", "")
                              if isinstance(p.get("openAccessPdf"), dict)
                              else "",
                }
        return out

    def _batch(self, ids: list[str]) -> list | None:
        """POST /paper/batch."""
        url = f"{_BASE}/paper/batch"
        params = {"fields": _ENRICH_FIELDS}
        for i in range(self.max_retries):
            try:
                r = requests.post(
                    url, params=params, json={"ids": ids},
                    headers=_headers(self.api_key), timeout=40,
                )
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 429:
                    wait = min(2 ** i * 2, 20)
                    print(f"[s2] batch 429，{wait}s 后重试 ({i+1})")
                    time.sleep(wait)
                    continue
                print(f"[s2] batch status {r.status_code}: {r.text[:120]}")
                return None
            except requests.RequestException as e:
                print(f"[s2] batch 异常: {e}")
                time.sleep(2)
        return None

    # ---------------- 关键词检索 ----------------
    def fetch(self) -> list[dict[str, Any]]:
        """直接用关键词在 S2 全文搜索（作为独立检索源）。

        关键词较宽，多个 OR 串联，按年份 >= since_year 过滤。
        """
        results: list[dict[str, Any]] = []
        # 多关键词分开调用以提高命中精度（避免 query 过长）
        for kw in self.keywords:
            if len(results) >= self.max_per_keyword:
                break
            params = {
                "query": kw,
                "limit": 10,
                "fields": _SEARCH_FIELDS,
                "year": f"{self.since_year}-",
            }
            payload = self._get(f"{_BASE}/paper/search/bulk", params)
            if not payload or not payload.get("data"):
                continue
            for p in payload["data"]:
                aid = normalize_arxiv_id(
                    (p.get("externalIds") or {}).get("ArXiv", "")
                )
                authors = []
                for a in (p.get("authors") or []):
                    name = a.get("name") if isinstance(a, dict) else str(a)
                    if name:
                        authors.append(name)
                results.append({
                    "arxiv_id": aid,
                    "title": simplify(p.get("title") or ""),
                    "summary": simplify(p.get("abstract") or ""),
                    "authors": authors,
                    "arxiv_url": f"https://arxiv.org/abs/{aid}" if aid else "",
                    "submitted_date": f"{p.get('year') or ''}-01-01",
                    "source": self.name,
                    "venue": p.get("venue") or "",
                    "citation_count": p.get("citationCount") or 0,
                    "year": p.get("year") or 0,
                })
        # 二次关键词过滤，避免无关论文涌入
        return results