"""论文元信息增强模块：联网抓取 arXiv，提取作者单位、项目主页、代码仓库。

数据获取策略（按可用性逐级回退）：
1. PDF 全文（arxiv.org/pdf/<id>）：用 PyMuPDF 抽取首页/前两页文本，
   arXiv 论文 PDF 首页通常含完整作者 + 脚注单位，格式最规整。
2. arXiv 官方自动生成的 HTML 全文页（arxiv.org/html/<id>）：
   fallback 用，HTML 中单位常以上标编号形式呈现。
3. abs 摘要页：仅作链接抽取补充（一般无 affiliation）。

抽取到的原始文本会一并提供给 LLM（在 post_generator 中）解析单位，
本模块只负责"联网取数"，不做单位字符串的最终清洗——交给更鲁棒的模型。
GitHub / 项目主页链接由正则从文本中可靠提取。
全部失败时返回空字段，由上层在推文中写"暂无"——绝不写"见论文"。
"""
from __future__ import annotations

import re
from typing import Any

import requests

# 伪装为浏览器，避免被 arXiv 简单拦截
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}


def _strip_tags(html: str) -> str:
    """HTML -> 纯文本（去脚本/样式/标签/注释）。"""
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
    html = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


# arXiv 自动生成 HTML 页面顶部常见的模板导航文字（用于定位跳过）
_ARXIV_NAV_MARKERS = (
    "Report GitHub Issue", "Title: Content selection saved.",
    "arXiv is now an independent nonprofit", "Why HTML?", "Back to Abstract",
    "Download PDF Abstract",
)


def _focus_text_after_title(text: str, keep: int = 5000) -> str:
    """把 strip 后的全文定位到论文标题正文位置，跳过 arXiv 模板导航段。

    arXiv HTML 页正文通常是：
        <标题> Report GitHub Issue ... Download PDF Abstract <目录> <标题 再次出现> <作者+单位> ...
    本函数找到最后一次出现的、且后面紧跟"Abstract"或目录的"标题位置"
    后的文本，确保抓到作者+单位。
    """
    if not text:
        return ""

    # 找"Back to Abstract ... Abstract"这种导航结束位置
    idx = -1
    for marker in ("Back to Abstract Download PDF Abstract",
                   "Download PDF Abstract Abstract",
                   "Download PDF Abstract Introduction"):
        i = text.find(marker)
        if i != -1:
            idx = i + len(marker)
            break

    if idx != -1:
        focused = text[idx:].strip()
        return focused[:keep] if focused else text[:keep]
    # 兜底：找 "Abstract Introduction" 这种从摘要开始的位置
    i = text.find("Abstract Introduction")
    if i != -1:
        return text[i: i + keep]
    return text[:keep]


def _try_fetch(url: str, timeout: int = 25) -> str | None:
    """抓取 url，返回原始响应体文本/二进制（失败返回 None）。"""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        if r.status_code == 200 and r.content:
            return r.text
    except requests.RequestException as e:
        print(f"[meta] 抓取失败 {url}: {e}")
    return None


def _try_fetch_bytes(url: str, timeout: int = 30) -> bytes | None:
    """抓取二进制（用于 PDF）。"""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        if r.status_code == 200 and r.content:
            return r.content
    except requests.RequestException as e:
        print(f"[meta] 抓取 PDF 失败 {url}: {e}")
    return None


def _pdf_head_text(pdf_bytes: bytes, pages: int = 2, char_limit: int = 4000) -> str:
    """用 PyMuPDF 抽取 PDF 前几页文本。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("[meta] 未安装 PyMuPDF（pymupdf），跳过 PDF 解析。")
        return ""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        print(f"[meta] 打开 PDF 失败：{e}")
        return ""
    out: list[str] = []
    total = 0
    for i in range(min(pages, doc.page_count)):
        text = doc.load_page(i).get_text("text") or ""
        out.append(text)
        total += len(text)
        if total >= char_limit:
            break
    doc.close()
    return re.sub(r"\s+\n", "\n", "\n".join(out)).strip()[:char_limit]


def _extract_links_from_text(text: str) -> tuple[str, str]:
    """从纯文本中抽取 github 仓库链接与项目主页链接。"""
    github = ""
    project = ""
    seen_github: set[str] = set()
    for m in re.findall(r'https?://[^\s)"\'<>，。；,]+', text):
        low = m.lower()
        # github 仓库形如 https://github.com/org/repo
        if "github.com" in low and re.search(r"github\.com/[^/]+/[^/\s]+", low):
            clean = m.rstrip(".,);]")
            if clean not in seen_github:
                seen_github.add(clean)
                if not github:
                    github = clean
        # 项目主页常见特征词
        elif any(k in low for k in (
            "project-page", "huggingface.co/spaces", ".github.io",
            "sites.google", "vercel.app", "web-version", "projectpage",
        )):
            if not project:
                project = m.rstrip(".,);]")
    return github, project


def enrich_paper(paper: dict[str, Any]) -> dict[str, Any]:
    """为单篇论文抓取并补充：作者单位原文片段/项目主页/GitHub 链接。

    Returns:
        在原 paper 上追加字段：
        - web_text: str        抓到的原文片段（供 LLM 抽取单位）
        - institutions: list   原始抽取结果（可能为空，最终以 LLM 为准）
        - project_page: str    项目主页链接（无则空串）
        - github: str          GitHub 仓库链接（无则空串）
        - meta_source: str     "html" | "pdf" | ""  信息来源
    """
    arxiv_id = paper.get("arxiv_id", "")
    aid = arxiv_id.split("v")[0]
    abs_url = paper.get("arxiv_url") or (f"https://arxiv.org/abs/{aid}" if aid else "")
    html_url = f"https://arxiv.org/html/{arxiv_id}" if arxiv_id else ""
    pdf_url = paper.get("pdf_url") or (f"https://arxiv.org/pdf/{aid}" if aid else "")

    web_text = ""
    # 优先复用上游源已经提供的真实 github / project_page（避免重复抓取）
    github = paper.get("github") or ""
    project = paper.get("project_page") or paper.get("projectPage") or ""
    source = ""

    # 1) 优先 PDF 首页（单位信息最规整，作者脚注就在标题下方）
    if pdf_url:
        pdf = _try_fetch_bytes(pdf_url)
        if pdf:
            web_text = _pdf_head_text(pdf)
            source = "pdf"
    # 2) HTML 全文页 fallback
    if not web_text and html_url:
        html = _try_fetch(html_url)
        if html:
            web_text = _focus_text_after_title(_strip_tags(html))
            source = "html"
    # 3) abs 页兜底
    if not web_text and abs_url:
        abs_html = _try_fetch(abs_url)
        if abs_html:
            web_text = _focus_text_after_title(_strip_tags(abs_html))[:3500]
            source = "abs"

    # 从原文片段抽取 github / 项目主页链接，补齐缺失项
    if web_text:
        g2, p2 = _extract_links_from_text(web_text)
        github = github or g2
        project = project or p2
    # abs comments 字段里也常含项目链接，单独再扫一遍
    comment = paper.get("comment") or ""
    if comment:
        g3, p3 = _extract_links_from_text(comment)
        github = github or g3
        project = project or p3

    enriched = dict(paper)
    enriched["web_text"] = web_text
    enriched["institutions"] = []  # 由 LLM 在推文生成时抽取
    enriched["project_page"] = project
    enriched["github"] = github
    enriched["meta_source"] = source
    return enriched


def summarize_for_prompt(paper: dict[str, Any]) -> str:
    """把抓到的原文片段整理成给 LLM 的输入块，供其抽取单位/链接。"""
    web_text = paper.get("web_text", "")
    # 截断到 2500 字，避免 prompt 过长
    snippet = web_text[:2500] if web_text else "（联网未获取到论文页面，单位信息请填『暂无』）"
    project = paper.get("project_page") or ""
    github = paper.get("github") or ""
    return (
        f"【联网抓取到的真实信息（来源：{paper.get('meta_source') or '失败'}）】\n"
        f"已用正则提取到的 GitHub 链接：{github or '（未找到）'}\n"
        f"已用正则提取到的项目主页链接：{project or '（未找到）'}\n"
        f"论文首页原文片段（包含作者与单位，请从中抽取单位）：\n{snippet}\n"
    )


if __name__ == "__main__":
    # 独立调试：对三个 ID 测试
    for pid in ("2607.05352v1", "2607.02865v1", "2607.05238v1"):
        print("=" * 50)
        print(pid)
        r = enrich_paper({"arxiv_id": pid})
        print("来源:", r["meta_source"], "| 文本长度:", len(r["web_text"]))
        print("项目:", r["project_page"])
        print("代码:", r["github"])
        print("片段前300字:", r["web_text"][:300].replace("\n", " "))