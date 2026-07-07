"""检索聚合器：统一调度多数据源，做去重 + Semantic Scholar 富化 + 截断。

`search_papers()` 是后台流程入口，替代原 src/arxiv_search.search_papers：
  1. 并行/串行调用各启用源；
  2. 跨源去重（按 arXiv id 优先，没 id 走标题）；
  3. 对带 arXiv id 的论文用 S2 批量补 venue / citationCount；
  4. 按 submitted_date 倒序、HF upvotes 二级排序，截断至 max_papers；
  5. 返回标准化论文 dict 列表。

`fetch_by_ids()` 保留对老入口的兼容：按 arXiv id 直接拉取（用于再生推文）。
"""
from __future__ import annotations

from typing import Any

from .config import Config
from .sources import (
    ArxivSource,
    HFDailySource,
    OpenReviewSource,
    SemanticScholar,
    dedupe_papers,
    normalize_arxiv_id,
)


def _build_sources() -> list:
    """根据 Config 构建启用的源实例。"""
    srcs: list = []

    if Config.enable_arxiv:
        srcs.append(ArxivSource(
            categories=Config.arxiv_categories,
            keywords=Config.search_keywords,
            since_date=Config.since_date,
            max_papers=Config.max_papers_per_day,
        ))
    if Config.enable_hf_daily:
        srcs.append(HFDailySource(
            keywords=Config.search_keywords,
            since_date=Config.since_date,
            max_days=Config.hf_lookback_days,
            max_papers=Config.max_papers_per_day * 2,
        ))
    if Config.enable_openreview:
        srcs.append(OpenReviewSource(
            keywords=Config.search_keywords,
            since_year=Config.openreview_since_year,
            max_per_keyword=Config.max_papers_per_day * 2,
        ))
    if Config.enable_semantic_scholar and Config.enable_s2_search:
        # 默认 S2 关闭关键词检索模式（命中过宽，且易限流），
        # 仅保留 batch 富化（按 arXiv id 反查 venue/citation）。
        srcs.append(SemanticScholar(
            keywords=Config.search_keywords,
            since_year=Config.semantic_scholar_since_year,
            max_per_keyword=Config.max_papers_per_day * 2,
            api_key=Config.semantic_scholar_api_key,
        ))
    return srcs


def _sort_key(p: dict[str, Any]) -> tuple:
    """排序键：日期降序、upvotes 降序、再按 citation 降序。"""
    date_num = (p.get("submitted_date") or "").replace("-", "")
    try:
        date_num = int(date_num) if date_num else 0
    except ValueError:
        date_num = 0
    return (
        date_num,
        -int(p.get("upvotes") or 0),
        -int(p.get("citation_count") or 0),
    )


def search_papers() -> list[dict[str, Any]]:
    """聚合执行：多源检索 -> 去重 -> S2 富化 -> 截断。"""
    raw: list[dict[str, Any]] = []
    for src in _build_sources():
        print(f"[aggregator] 调用源: {src.name}")
        try:
            items = src.fetch()
            print(f"  -> {src.name} 返回 {len(items)} 篇")
            raw.extend(items)
        except Exception as e:  # noqa: BLE001
            print(f"[aggregator] 源 {src.name} 异常: {e}")

    # 1. 去重
    deduped = dedupe_papers(raw)
    print(f"[aggregator] 去重后 {len(deduped)} 篇")

    # 2. Semantic Scholar 富化（默认启用，可关闭）
    if Config.enable_semantic_scholar:
        aids = [normalize_arxiv_id(p.get("arxiv_id", ""))
                for p in deduped if normalize_arxiv_id(p.get("arxiv_id", ""))]
        if aids:
            print(f"[aggregator] 调 Semantic Scholar 富化 {len(aids)} 篇 ...")
            s2 = SemanticScholar(
                keywords=[],
                api_key=Config.semantic_scholar_api_key,
            )
            try:
                enrichment = s2.enrich_by_arxiv_ids(aids)
                for p in deduped:
                    aid = normalize_arxiv_id(p.get("arxiv_id", ""))
                    extra = enrichment.get(aid)
                    if not extra:
                        continue
                    # venue / citation_count：base 没有时补
                    if extra.get("venue") and not p.get("venue"):
                        p["venue"] = extra["venue"]
                    if extra.get("citation_count") and \
                            (not p.get("citation_count")
                             or p["citation_count"] == 0):
                        p["citation_count"] = extra["citation_count"]
                    if extra.get("influential_citation_count") and \
                            not p.get("influential_citation_count"):
                        p["influential_citation_count"] = \
                            extra["influential_citation_count"]
                    if extra.get("doi") and not p.get("doi"):
                        p["doi"] = extra["doi"]
                    # OpenReview 反查补回 arXiv id（罕见，留作扩展）
                print(f"[aggregator] 完成 S2 富化：命中 {len(enrichment)} 篇")
            except Exception as e:  # noqa: BLE001
                print(f"[aggregator] S2 富化失败: {e}")

    # 3. 过滤无必要字段的（arxiv id 缺失且无 pdf_url 的 OpenReview 项仍保留，
    #    评估与推文可正常进行；post-gen 时再回退联网补 arXiv）

    # 4. 排序 + 截断
    deduped.sort(key=_sort_key, reverse=True)
    max_n = Config.max_papers_per_day
    items = deduped[:max_n] if max_n > 0 else deduped

    print(f"[aggregator] 候选论文数 (按日期倒序截断 {max_n}): {len(items)}")
    return items


# ---------------- 兼容老入口 ----------------
def fetch_by_ids(arxiv_ids: list[str]) -> list[dict[str, Any]]:
    """向后兼容入口：按 arXiv id 拉详情，再走 S2 富化补 venue/citation。"""
    from .arxiv_search import fetch_by_ids as _arxiv_fetch

    papers = _arxiv_fetch(arxiv_ids)
    if not papers:
        return []

    if Config.enable_semantic_scholar:
        aids = [normalize_arxiv_id(p.get("arxiv_id", ""))
                for p in papers if normalize_arxiv_id(p.get("arxiv_id", ""))]
        s2 = SemanticScholar(keywords=[], api_key=Config.semantic_scholar_api_key)
        try:
            extra = s2.enrich_by_arxiv_ids(aids)
            for p in papers:
                aid = normalize_arxiv_id(p.get("arxiv_id", ""))
                e = extra.get(aid)
                if e:
                    if e.get("venue") and not p.get("venue"):
                        p["venue"] = e["venue"]
                    if e.get("citation_count"):
                        p["citation_count"] = e["citation_count"]
        except Exception as e:  # noqa: BLE001
            print(f"[fetch_by_ids] S2 富化失败: {e}")
    return papers


if __name__ == "__main__":
    for p in search_papers()[:5]:
        print(
            p.get("submitted_date"),
            p.get("arxiv_id"),
            (p.get("venue") or "-"),
            (p.get("citation_count") or 0),
            "⊕", p.get("sources"),
            p.get("title")[:60],
        )