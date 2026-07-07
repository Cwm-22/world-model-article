"""Markdown -> PDF 转换。

流程：markdown 库把 md 转 HTML -> 内联 CSS（中文字体）-> xhtml2pdf 渲染。

中文支持要点（xhtml2pdf 对 @font-face 的 .ttc/.ttf 加载不稳）：
1. 用 reportlab 全局注册单文件 TTF 字体（SimHei）；
2. 把该字体加进 xhtml2pdf.default.DEFAULT_FONT 映射；
3. CSS 里直接 font-family: SimHei，不走 @font-face。

支持：表格、引用块、代码块、分割线、加粗/斜体、列表。每条推文之间 hr 自动分页。

用法：
    from src.md2pdf import convert_md_to_pdf
    convert_md_to_pdf(md_text, "out.pdf")

    # 直接转文件
    python -m src.md2pdf input.md output.pdf

    # 自测：转换项目里现有的推文 md
    python -m src.md2pdf
"""
from __future__ import annotations

import sys
from pathlib import Path

import markdown
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xhtml2pdf import default as _xhtml_default
from xhtml2pdf import pisa


# ---------------- 中文字体注册（仅一次） ----------------
def _register_cjk_font() -> str:
    """注册一个可用的中文 TTF 字体并返回字体名。"""
    from os.path import exists

    candidates = [
        # Windows 自带字体
        ("SimHei", r"C:\Windows\Fonts\simhei.ttf"),
        ("Deng", r"C:\Windows\Fonts\Deng.ttf"),
        # Linux 常见 CJK 字体（Ubuntu apt: fonts-noto-cjk / fonts-wqy-zenhei）
        ("NotoSansCJK", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        ("NotoSansCJKsc", "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
        ("WenQuanYiZenHei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        ("WenQuanYiMicroHei", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    ]
    for name, path in candidates:
        if exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                # 注册到 xhtml2pdf 的字体映射表（小写名 -> reportlab 名）
                _xhtml_default.DEFAULT_FONT[name.lower()] = name
                _xhtml_default.DEFAULT_FONT[name.lower() + "-bold"] = name
                _xhtml_default.DEFAULT_FONT[name.lower() + "-oblique"] = name
                _xhtml_default.DEFAULT_FONT[
                    name.lower() + "-boldoblique"
                ] = name
                return name
            except Exception:
                continue
    raise RuntimeError(
        "未找到可用的中文 TTF 字体。Windows 需 simhei.ttf；Linux 需 "
        "fonts-noto-cjk 或 fonts-wqy-zenhei（apt install 之一）。"
    )


_CJK_FONT: str = _register_cjk_font()


# ---------------- CSS ----------------
_CSS = f"""
@page {{
    size: A4;
    margin: 1.8cm 1.6cm;
}}
* {{
    font-family: {_CJK_FONT};
}}
body {{
    font-family: {_CJK_FONT};
    font-size: 10.5pt;
    line-height: 1.7;
    color: #222;
}}
h1 {{
    font-size: 18pt;
    color: #1a1a1a;
    border-bottom: 2px solid #333;
    padding-bottom: 6px;
    margin-top: 0;
}}
h2 {{
    font-size: 14pt;
    color: #2b2b2b;
    margin-top: 18px;
}}
h3 {{
    font-size: 12pt;
    color: #3a3a3a;
}}
blockquote {{
    border-left: 4px solid #c0c0c0;
    margin: 8px 0;
    padding: 4px 12px;
    color: #555;
    background: #fafafa;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 10px 0;
    font-size: 9.5pt;
}}
th, td {{
    border: 1px solid #bbb;
    padding: 6px 8px;
    text-align: left;
    word-wrap: break-word;
}}
th {{
    background: #f0f0f0;
    font-weight: bold;
}}
hr {{
    border: none;
    border-top: 1px dashed #ccc;
    margin: 18px 0;
    /* 推文之间强制分页 */
    page-break-after: always;
}}
code {{
    background: #f5f5f5;
    padding: 1px 4px;
    color: #c7254e;
    font-size: 9.5pt;
}}
pre {{
    background: #f5f5f5;
    padding: 8px;
    border: 1px solid #e0e0e0;
    white-space: pre-wrap;
    word-wrap: break-word;
    font-size: 9pt;
}}
a {{
    color: #1e6bb8;
    text-decoration: none;
}}
strong {{
    color: #000;
}}
ul, ol {{
    margin: 6px 0 6px 20px;
    padding-left: 6px;
}}
"""


def _md_to_html(md_text: str) -> str:
    """md -> 完整 HTML 文档（含内联 CSS）。"""
    extensions = [
        "tables",          # GFM 表格
        "fenced_code",     # ``` 代码块
        "extra",           # 缩写、脚注、属性、定义列表
        "sane_lists",      # 更合理的列表解析
        "nl2br",           # 单换行转 <br>
        "admonition",
    ]
    html_body = markdown.markdown(
        md_text,
        extensions=extensions,
        output_format="html5",
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
{_CSS}
</style>
</head>
<body>
{html_body}
</body>
</html>"""


def convert_md_to_pdf(md_text: str, pdf_path: str | Path) -> Path:
    """把 Markdown 文本渲染为 PDF。

    Args:
        md_text: Markdown 源内容。
        pdf_path: 输出 PDF 路径。

    Returns:
        生成的 PDF 路径（Path）。
    """
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    html = _md_to_html(md_text)

    with open(pdf_path, "wb") as out:
        result = pisa.CreatePDF(html, dest=out, encoding="utf-8")

    if result.err:
        raise RuntimeError(
            f"xhtml2pdf 渲染失败，错误数={result.err}；"
            f"请检查 markdown 内容或字体路径。"
        )
    return pdf_path


def convert_md_file_to_pdf(
    md_path: str | Path, pdf_path: str | Path | None = None
) -> Path:
    """直接把 md 文件转 PDF。"""
    md_path = Path(md_path)
    if pdf_path is None:
        pdf_path = md_path.with_suffix(".pdf")
    return convert_md_to_pdf(md_path.read_text(encoding="utf-8"), pdf_path)


# ---------------- 自测 ----------------
if __name__ == "__main__":
    if len(sys.argv) >= 3:
        convert_md_file_to_pdf(sys.argv[1], sys.argv[2])
        print(f"已生成: {sys.argv[2]}")
    else:
        from .config import Config

        sample = next(Config.output_dir.glob("*_top3_推文.md"), None)
        if sample is None:
            print("未找到可测试的 *_top3_推文.md")
            sys.exit(1)
        out = sample.with_suffix(".pdf")
        convert_md_file_to_pdf(sample, out)
        print(f"已生成: {out}")