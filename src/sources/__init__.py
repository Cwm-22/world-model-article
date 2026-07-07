"""多数据源检索子包。

每个源实现 `fetch()` 返回标准化论文 dict 列表，键沿用 src/arxiv_search.py 的
Schema：arxiv_id / title / summary / authors / arxiv_url / submitted_date，
并额外补充 venue / citation_count / upvotes / project_page / github / source 等。
"""
from .arxiv_source import ArxivSource
from .base import (
    PaperItem,
    count_real_text,
    dedupe_papers,
    make_arxiv_url,
    normalize_arxiv_id,
    normalize_title,
    parse_date,
    simplify,
)
from .hf_daily_source import HFDailySource
from .openreview_source import OpenReviewSource
from .semantic_scholar import SemanticScholar

__all__ = [
    "ArxivSource",
    "HFDailySource",
    "OpenReviewSource",
    "SemanticScholar",
    "PaperItem",
    "normalize_arxiv_id",
    "normalize_title",
    "parse_date",
    "simplify",
    "count_real_text",
    "make_arxiv_url",
    "dedupe_papers",
]