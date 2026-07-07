"""一次性脚本：从候选池里选 Top3 重新生成推文。

适用场景：升级成"候选池"模式后，想把之前已经评估但没进推文的高分论文
重新拉出来生成新版推文（带关键词加粗 + 联网单位抓取）。

用法：
    python regen_posts.py
    python regen_posts.py --ids 2607.05352 2607.02865 ...   # 指定 ID
"""
from __future__ import annotations

import argparse

from src.arxiv_search import fetch_by_ids
from src.config import Config
from src.llm_client import LLMClient
from src.post_generator import generate_top_posts
from src.reporter import build_posts_md, save_text
from src.storage import candidate_pool


def _select_from_pool(top_n: int) -> list[dict]:
    """从候选池取分数最高的 N 篇。"""
    pool = candidate_pool(max_age_days=None)
    return pool[:top_n]


def main() -> int:
    parser = argparse.ArgumentParser(description="对候选池 Top3 重新生成推文")
    parser.add_argument(
        "--ids", nargs="*", default=None,
        help="手动指定 arXiv ID 列表；不传则从候选池按分数取 Top3",
    )
    parser.add_argument("--top", type=int, default=None, help="覆盖 TOP_K")
    args = parser.parse_args()

    Config.validate()
    top_n = args.top or Config.top_k

    if args.ids:
        ids = args.ids
        # 从候选池搜分数回填
        pool_map = {p["arxiv_id"]: p.get("score", 0)
                    for p in candidate_pool(max_age_days=None)}
        score_map = {i: pool_map.get(i.split("v")[0], 0) for i in ids}
        print(f"📋 手动指定 {len(ids)} 篇")
    else:
        selected = _select_from_pool(top_n)
        ids = [s["arxiv_id"] for s in selected]
        score_map = {s["arxiv_id"]: s.get("score", 0) for s in selected}
        print(f"📋 候选池 Top{len(ids)}：")
        for s in selected:
            print(f"   - [{s.get('score', 0)}分] {s['arxiv_id']}  "
                  f"{s.get('title','')[:50]}")

    if not ids:
        print("候选池为空且未指定 --ids，终止。")
        return 1

    # 用 arXiv API 拉取完整详情
    print(f"\n📥 拉取 {len(ids)} 篇完整论文信息 ...")
    papers = fetch_by_ids(ids)
    if not papers:
        print("拉取失败，终止。")
        return 1
    for p in papers:
        p["total_score"] = score_map.get(p.get("arxiv_id", ""), 0)

    # 联网增强并生成推文
    print("\n🤖 联网抓取元信息并生成推文 ...")
    llm = LLMClient()
    posts, enriched = generate_top_posts(llm, papers)

    post_name, post_md = build_posts_md(posts)
    path = save_text(post_name, post_md)
    print(f"\n✅ 新版推文已生成：{path}")
    print(f"   字节数：{path.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())