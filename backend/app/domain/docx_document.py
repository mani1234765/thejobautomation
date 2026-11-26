"""Format-preserving DOCX reading and editing.

Every paragraph gets a stable *path id* in document order:

* ``b{n}``            — n-th body paragraph (outside tables)
* ``t{t}r{r}c{c}p{p}`` — paragraph p of cell (r, c) of body table t (nested tables are flattened
  into their parent cell with a ``.`` separator, e.g. ``t0r0c1p0.t0r0c0p2``)
* ``h{s}p{n}`` / ``f{s}p{n}`` — paragraphs of section s's header / footer

Editing works on a copy of the original package. Only the text of targeted paragraphs changes:
paragraph properties (style, numbering/bullets, spacing, alignment) are untouched and run
properties (fonts, sizes, colours, bold labels) are kept by reusing existing runs. Every other
part of the package — styles, numbering, headers/footers, section margins, columns, images —
is carried over unchanged.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass

from docx import Document
from docx.document import Document as DocumentT
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run


@dataclass(frozen=True)
class DocParagraph:
    id: str
    text: str
    style: str
    is_bold: bool
    is_list: bool
    font_size: float | None
    location: str  # body | table | header | footer


def _iter_block(parent_elm, parent) -> Iterator[Paragraph | Table]:
    for child in parent_elm.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)
        elif child.tag == qn("w:sdt"):  # content controls wrap paragraphs in some templates
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _iter_block(content, parent)


def _cell_paragraphs(table: Table, prefix: str) -> Iterator[tuple[str, Paragraph]]:
    seen_cells: set[int] = set()
    for r, row in enumerate(table.rows):
        for c, cell in enumerate(row.cells):
            if id(cell._tc) in seen_cells:  # merged cells repeat in python-docx
                continue
            seen_cells.add(id(cell._tc))
            p_i, t_i = 0, 0
            for block in _iter_block(cell._tc, cell):
                if isinstance(block, Paragraph):
                    yield f"{prefix}r{r}c{c}p{p_i}", block
                    p_i += 1
                else:
                    yield from _cell_paragraphs(block, f"{prefix}r{r}c{c}p{p_i}.t{t_i}")
                    t_i += 1


def iter_paragraphs(doc: DocumentT) -> Iterator[tuple[str, str, Paragraph]]:
    b_i, t_i = 0, 0
    for block in _iter_block(doc.element.body, doc):
        if isinstance(block, Paragraph):
            yield f"b{b_i}", "body", block
            b_i += 1
        else:
            for pid, p in _cell_paragraphs(block, f"t{t_i}"):
                yield pid, "table", p
            t_i += 1
    for s_i, section in enumerate(doc.sections):
        for kind, part in (("h", section.header), ("f", section.footer)):
            if part.is_linked_to_previous and s_i > 0:
                continue
            for p_i, p in enumerate(part.paragraphs):
                yield f"{kind}{s_i}p{p_i}", "header" if kind == "h" else "footer", p


def _runs(p: Paragraph) -> list[Run]:
    return [Run(r, p) for r in p._p.xpath("./w:r | ./w:hyperlink/w:r | ./w:ins/w:r")]


def _is_list(p: Paragraph) -> bool:
    ppr = p._p.pPr
    if ppr is not None and ppr.numPr is not None:
        return True
    style = (p.style.name if p.style is not None else "") or ""
    return "list" in style.lower() or p.text.lstrip()[:1] in "•▪●◦-*–·"


def _font_size(p: Paragraph) -> float | None:
    for r in _runs(p):
        if r.font.size is not None:
            return r.font.size.pt
    if p.style is not None and p.style.font is not None and p.style.font.size is not None:
        return p.style.font.size.pt
    return None


def read_paragraphs(data: bytes) -> list[DocParagraph]:
    doc = Document(io.BytesIO(data))
    out: list[DocParagraph] = []
    for pid, location, p in iter_paragraphs(doc):
        runs = _runs(p)
        text = "".join(r.text for r in runs)
        if not text.strip():
            continue
        text_runs = [r for r in runs if r.text.strip()]
        is_bold = bool(text_runs) and all(
            r.bold or (r.bold is None and p.style is not None and p.style.font.bold) for r in text_runs
        )
        out.append(
            DocParagraph(
                id=pid,
                text=text,
                style=(p.style.name if p.style is not None else "") or "",
                is_bold=bool(is_bold),
                is_list=_is_list(p),
                font_size=_font_size(p),
                location=location,
            )
        )
    return out


def _replace_text(p: Paragraph, new_text: str) -> None:
    runs = _runs(p)
    if not runs:
        p.add_run(new_text)
        return
    texts = [r.text for r in runs]
    # Keep leading runs whose text is an unchanged prefix (e.g. a bold "Skills:" label) ...
    head, consumed = 0, 0
    while head < len(runs) - 1 and texts[head] and new_text.startswith(texts[head], consumed):
        consumed += len(texts[head])
        head += 1
    # ... and trailing runs that are an unchanged suffix (e.g. right-aligned dates after a tab).
    tail, tail_len = len(runs), 0
    while (
        tail - 1 > head
        and texts[tail - 1]
        and new_text[: len(new_text) - tail_len].endswith(texts[tail - 1])
        and len(new_text) - tail_len - len(texts[tail - 1]) >= consumed
    ):
        tail_len += len(texts[tail - 1])
        tail -= 1
    middle = new_text[consumed : len(new_text) - tail_len]
    if head == tail:  # every run was preserved; extend the last preserved run
        runs[head - 1].text = texts[head - 1] + middle
        return
    runs[head].text = middle
    for r in runs[head + 1 : tail]:
        r.text = ""


class ParagraphNotFound(KeyError):
    pass


def apply_edits(data: bytes, edits: dict[str, str]) -> bytes:
    """Return a new DOCX with the given paragraph ids' text replaced. The input is not modified."""
    doc = Document(io.BytesIO(data))
    remaining = dict(edits)
    for pid, _, p in iter_paragraphs(doc):
        if pid in remaining:
            _replace_text(p, remaining.pop(pid))
    if remaining:
        raise ParagraphNotFound(", ".join(sorted(remaining)))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
