"""轻量 Markdown → Qt 富文本转换（无第三方依赖）。

只覆盖大模型回答里真正会出现的语法：围栏代码块、行内代码、粗斜体、
标题、有序/无序列表、引用、分隔线、链接。输入一律先 HTML 转义，
所以模型输出里带的 <script> 之类不会变成可执行内容。
"""
from __future__ import annotations

import html
import re

_FENCE = re.compile(r"```([\w+-]*)\n?(.*?)```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
_ITALIC = re.compile(r"(?<![\*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\*\w])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_ULIST = re.compile(r"^\s*[-*+]\s+(.*)$")
_OLIST = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_HR = re.compile(r"^\s*([-*_])\s*\1\s*\1[\s\-\*_]*$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")

_CODE_WRAP = 100  # 代码块软换行宽度，避免 QLabel 横向撑破


def _wrap_code(text: str) -> str:
    out = []
    for line in text.split("\n"):
        while len(line) > _CODE_WRAP:
            out.append(line[:_CODE_WRAP] + "↩")
            line = line[_CODE_WRAP:]
        out.append(line)
    return "\n".join(out)


def _inline(escaped: str, p: dict) -> str:
    """行内语法；入参必须是已转义过的文本。"""
    parts = re.split(r"(`[^`\n]+`)", escaped)
    buf = []
    for seg in parts:
        if len(seg) > 2 and seg.startswith("`") and seg.endswith("`"):
            buf.append(f'<code style="color:{p["code_fg"]};background:{p["code_bg"]};'
                       f'padding:1px 4px;border-radius:4px;font-family:Consolas,monospace;">'
                       f'{seg[1:-1]}</code>')
            continue
        seg = _LINK.sub(r'<a href="\2" style="color:{0};">\1</a>'.format(p["link"]), seg)
        seg = _BOLD.sub(lambda m: f'<b>{m.group(1) or m.group(2)}</b>', seg)
        seg = _ITALIC.sub(lambda m: f'<i>{m.group(1)}</i>', seg)
        seg = _STRIKE.sub(lambda m: f'<s>{m.group(1)}</s>', seg)
        buf.append(seg)
    return "".join(buf)


def _soft_break(line: str) -> str:
    """行尾两个空格 = markdown 硬换行。"""
    return re.sub(r"\s{2,}$", "<br>", line)


def to_html(md: str, p: dict) -> str:
    """把 markdown 文本转成可直接塞进 QLabel 的 HTML 片段。p 是配色字典。"""
    if not md:
        return ""
    blocks: list[str] = []

    def stash(body: str) -> str:
        blocks.append(body)
        return f"\x00{len(blocks) - 1}\x00"

    def _fence(m: re.Match) -> str:
        lang = (m.group(1) or "").strip()
        tag = f'<div style="color:{p["faint"]};font-size:11px;">{html.escape(lang)}</div>' if lang else ""
        return stash(
            f'<div style="background:{p["code_bg"]};border:1px solid {p["border"]};'
            f'border-radius:8px;padding:8px 10px;">{tag}'
            f'<pre style="font-family:Consolas,monospace;font-size:12px;'
            f'color:{p["code_fg"]};margin:0;">{html.escape(_wrap_code(m.group(2).rstrip()))}</pre></div>')

    text = _FENCE.sub(_fence, md)

    out: list[str] = []
    list_kind = ""          # "ul" / "ol"，记录当前打开的列表
    para: list[str] = []

    def close_list():
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = ""

    def flush_para():
        if para:
            out.append('<p style="margin:0 0 6px 0;">' + "<br>".join(para) + "</p>")
            para.clear()

    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            close_list()
            continue
        if re.fullmatch(r"\x00\d+\x00", line.strip()):
            # 代码块占位符独占一行：不能塞进 <p>，块级元素嵌套会被 Qt 丢掉
            flush_para()
            close_list()
            out.append(line.strip())
            continue
        if _HR.match(line):
            flush_para()
            close_list()
            out.append(f'<hr style="border:none;border-top:1px solid {p["border"]};">')
            continue
        m = _HEADING.match(line)
        if m:
            flush_para()
            close_list()
            size = {1: 19, 2: 17, 3: 15, 4: 14}[len(m.group(1))]
            out.append(f'<p style="font-size:{size}px;font-weight:700;color:{p["hi"]};'
                       f'margin:8px 0 4px 0;">{_inline(html.escape(m.group(2)), p)}</p>')
            continue
        m = _QUOTE.match(line)
        if m:
            flush_para()
            close_list()
            out.append(f'<div style="border-left:3px solid {p["accent"]};padding-left:8px;'
                       f'color:{p["soft"]};">{_inline(html.escape(m.group(1)), p)}</div>')
            continue
        m = _ULIST.match(line)
        if m:
            flush_para()
            if list_kind != "ul":
                close_list()
                out.append('<ul style="margin:0 0 4px 0; padding-left:22px;">')
                list_kind = "ul"
            out.append(f"<li>{_inline(html.escape(_soft_break(m.group(1))), p)}</li>")
            continue
        m = _OLIST.match(line)
        if m:
            flush_para()
            if list_kind != "ol":
                close_list()
                out.append('<ol style="margin:0 0 4px 0; padding-left:22px;">')
                list_kind = "ol"
            out.append(f"<li>{_inline(html.escape(_soft_break(m.group(2))), p)}</li>")
            continue
        close_list()
        para.append(_inline(html.escape(_soft_break(line)), p))

    flush_para()
    close_list()
    body = "\n".join(out) or f'<p style="margin:0;">{_inline(html.escape(text), p)}</p>'
    for i, block in enumerate(blocks):
        body = body.replace(f"\x00{i}\x00", block)
    return body


def plain_or_markdown(md: str) -> bool:
    """判断一段文本是否值得按 markdown 渲染（避免把纯文本里的 * 号当语法）。"""
    if not md:
        return False
    if "```" in md:
        return True
    for line in md.split("\n"):
        if _HEADING.match(line) or _ULIST.match(line) or _OLIST.match(line) or _QUOTE.match(line):
            return True
        if re.search(r"\*\*[^*\n]+\*\*", line) or _LINK.search(line):
            return True
    return False
