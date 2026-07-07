"""多源检索公共工具：标准化结构、arXiv ID/标题归一化、跨源去重。

设计要点：每个源返回的 dict 字段一致，便于聚合与下游评估。
"""
from __future__ import annotations

import re
from typing import Any, Protocol

# 标准化论文字段集合：
# 强制: arxiv_id | title | summary | authors | arxiv_url | submitted_date | source
# 可选: venue | citation_count | upvotes | project_page | github | keywords


class PaperItem(Protocol):
    """标准化论文项的形状（仅用于类型说明，便于 IDE）。"""

    arxiv_id: str
    title: str
    summary: str
    authors: list
    arxiv_url: str
    submitted_date: str
    source: str


def normalize_arxiv_id(aid: Any) -> str:
    """把 '2607.05352v1' / 'arXiv:2607.05352v1' 归一为 '2607.05352'。"""
    if not aid:
        return ""
    s = str(aid).strip()
    s = s.replace("arXiv:", "").replace("arxiv:", "")
    # 去掉版本后缀 vN
    s = re.sub(r"v\d+$", "", s)
    # 修正可能的尾部字符
    s = s.strip()
    return s


def make_arxiv_url(aid: str) -> str:
    """根据 arXiv id 构造 abs 链接。"""
    base = normalize_arxiv_id(aid)
    return f"https://arxiv.org/abs/{base}" if base else ""


def normalize_title(t: str) -> str:
    """标题归一化（小写 + 仅保留字母数字），用作去重 fallback 键。"""
    s = (t or "").lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def simplify(text: str, limit: int = 4000) -> str:
    """压平空白，按需截断。"""
    if not text:
        return ""
    s = re.sub(r"\s+", " ", str(text)).strip()
    return s if limit <= 0 else s[:limit]


def count_real_text(text: str) -> int:
    """真实文本长度（去空白），用于排序 tie-break。"""
    return len(re.sub(r"\s", "", text or ""))


def parse_date(date_str: str, fallback: str = "") -> str:
    """尽量把各种日期字符串转成 'YYYY-MM-DD'，无法解析返回 fallback。"""
    if not date_str:
        return fallback
    s = date_str.strip()
    # ISO: 2026-07-07T20:00:00.000Z
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # arXiv: 20260707
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return fallback


def merge_field(a: Any, b: Any) -> Any:
    """合并字段：优先非空值；list 取并集（去重保序）。"""
    if isinstance(a, list) or isinstance(b, list):
        la = a if isinstance(a, list) else ([a] if a else [])
        lb = b if isinstance(b, list) else ([b] if b else [])
        out: list = []
        seen: set = set()
        for x in la + lb:
            key = str(x).strip().lower() if isinstance(x, str) else str(x)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(x)
        return out
    av = str(a or "").strip()
    bv = str(b or "").strip()
    return av or bv


def dedupe_papers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨源去重并以 arxiv_id 为主键合并字段。

    策略：
    1. 优先按归一化后的 arxiv_id 分组合并（留更长摘要 + 合并 venue/github/upvotes）。
    2. 没有 arxiv_id 的按归一化标题分组合并（多源门钥匙）。
    3. 一条记录里：sources 是来源列表；首次出现为准的来源记 primary_source。
    """
    by_aid: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    consumed_aid: set[str] = set()
    consumed_title: set[str] = set()

    for p in items:
        # 复制避免修改输入
        cur: dict[str, Any] = dict(p)
        cur.setdefault("sources", [])

        aid = normalize_arxiv_id(cur.get("arxiv_id", ""))
        title_key = normalize_title(cur.get("title", ""))

        if cur.get("source") and cur["source"] not in cur["sources"]:
            cur["sources"].append(cur["source"])

        bucket: dict[str, Any] | None = None
        if aid and aid in by_aid:
            bucket = by_aid[aid]
        elif (not aid) and title_key and title_key in by_title:
            bucket = by_title[title_key]

        if bucket is not None:
            # 合并字段
            _merge_paper(bucket, cur)
            continue

        # 新条目
        out.append(cur)
        if aid:
            by_aid[aid] = cur
            consumed_aid.add(aid)
        elif title_key:
            by_title[title_key] = cur
            consumed_title.add(title_key)

    # 清理（去重输出已通过引用 dict 完成），返回顺序保持
    return out


def _merge_paper(base: dict[str, Any], other: dict[str, Any]) -> None:
    """把 other 的字段并入 base，已存在的非空字段保留 base。"""
    # 基础文本字段：留更长/更优
    for key in ("title", "summary", "arxiv_url", "submitted_date",
                "venue", "project_page", "github", "arxiv_id"):
        cur = base.get(key) or ""
        nxt = other.get(key) or ""
        # 摘要取更长；arxiv_id 若 base 没有则补
        if key == "summary":
            if len(str(nxt)) > len(str(cur)):
                base[key] = nxt
        elif key == "arxiv_id":
            if not normalize_arxiv_id(cur) and normalize_arxiv_id(nxt):
                base["arxiv_id"] = nxt
        else:
            if not str(cur).strip() and str(nxt).strip():
                base[key] = nxt

    # 数值/列表合并
    for key in ("authors", "keywords"):
        base[key] = merge_field(base.get(key, []), other.get(key, []))

    # 来源累加
    srcs = base.setdefault("sources", [])
    for s in (other.get("sources") or [other.get("source")] if other.get("source") else []):
        if s and s not in srcs:
            srcs.append(s)

    # 数值最大值
    for key in ("citation_count", "upvotes"):
        a = base.get(key) or 0
        b = other.get(key) or 0
        try:
            base[key] = max(int(a), int(b))
        except (TypeError, ValueError, AttributeError):
            base[key] = a or b

    # reference 数量取较大
    if "references_count" in other or "references_count" in base:
        a = base.get("references_count") or other.get("references_count") or 0
        try:
            base["references_count"] = int(a)
        except (TypeError, ValueError):
            pass