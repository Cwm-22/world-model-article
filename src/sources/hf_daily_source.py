"""HuggingFace Daily Papers 数据源。

人类策展的每日精选论文，来源：
  https://huggingface.co/api/daily_papers
返回最近若干天（默认 max_days）由社区点赞/收录的论文，每项含：
  paper.id（arXiv id）/ paper.title / paper.summary / paper.upvotes /
  paper.githubRepo / paper.ai_keywords / publishedAt
按关键词过滤后输出标准化 dict。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import requests

from .base import (
    make_arxiv_url,
    normalize_arxiv_id,
    parse_date,
    simplify,
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
)


class HFDailySource:
    """HuggingFace Daily Papers 检索源。"""

    name = "hf_daily"

    def __init__(
        self,
        keywords: list[str],
        since_date: str,
        max_days: int = 14,
        max_papers: int = 40,
        proxy: str | None = None,
    ) -> None:
        self.keywords = keywords
        self.since_date = since_date
        self.max_days = max_days
        self.max_papers = max_papers

    def _keyword_hit(self, text: str) -> bool:
        """关键词命中检测（放宽：关键词出现在标题或摘要任意位置）。

        keywords 为空时视为命中（让上层做评估）。
        """
        if not self.keywords:
            return True
        lower = (text or "").lower()
        for kw in self.keywords:
            k = kw.lower().strip()
            if k and k in lower:
                return True
        return False

    def fetch(self) -> list[dict[str, Any]]:
        url = "https://huggingface.co/api/daily_papers"
        params = {"date": datetime.now().strftime("%Y-%m-%d")}
        try:
            r = requests.get(
                url,
                params=params,
                headers={"User-Agent": _UA},
                timeout=25,
            )
            if r.status_code != 200 or not r.content:
                print(f"[hf_daily] 接口异常 status={r.status_code}")
                return []
            data = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[hf_daily] 抓取失败: {e}")
            return []

        if not isinstance(data, list):
            print("[hf_daily] 返回非列表，跳过")
            return []

        # 限制回看天数
        cutoff = (datetime.now() - timedelta(days=self.max_days)).strftime("%Y-%m-%d")
        since_num = self.since_date  # YYYYMMDD

        results: list[dict[str, Any]] = []
        for item in data:
            paper = item.get("paper") or {}
            aid = paper.get("id") or ""
            if not aid:
                continue
            title = simplify(paper.get("title") or item.get("title") or "")
            summary = simplify(paper.get("summary") or item.get("summary") or "")

            # 关键词过滤（标题 + 摘要）
            if not self._keyword_hit(f"{title}\n{summary}"):
                continue

            published = paper.get("publishedAt") or item.get("publishedAt") or ""
            submitted = parse_date(published)
            # 日期下限
            if since_num and submitted.replace("-", "") < since_num:
                continue
            if cutoff and submitted < cutoff:
                continue

            authors = [
                a.get("name") for a in (paper.get("authors") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            upvotes = paper.get("upvotes") or item.get("upvotes") or 0
            github = paper.get("githubRepo") or ""

            results.append({
                "arxiv_id": normalize_arxiv_id(aid),
                "title": title,
                "summary": summary,
                "authors": authors,
                "arxiv_url": make_arxiv_url(aid),
                "submitted_date": submitted,
                "source": self.name,
                "upvotes": int(upvotes) if isinstance(upvotes, (int, float)) else 0,
                "project_page": "",  # HF 不直接给项目主页
                "github": github,
                "keywords": paper.get("ai_keywords") or [],
                "venue": "HuggingFace Daily",
            })
            if len(results) >= self.max_papers:
                break

        print(f"[hf_daily] 命中 {len(results)} 篇（关键词过过滤后）")
        return results