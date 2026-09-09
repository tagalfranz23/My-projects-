"""Generate the research reviewer PDF from SYSTEM_RESEARCH_DOCUMENTATION.md."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "SYSTEM_RESEARCH_DOCUMENTATION.md"
OUTPUT = ROOT / "Barangay_Minante_1_Integrated_Management_System_Complete_Reviewer.pdf"

NAVY = colors.HexColor("#082F5B")
NAVY_DARK = colors.HexColor("#052343")
GOLD = colors.HexColor("#D4A62A")
PALE_BLUE = colors.HexColor("#EFF5FB")
PALE_GOLD = colors.HexColor("#FFF8E5")
SLATE = colors.HexColor("#334155")
LIGHT_BORDER = colors.HexColor("#CBD5E1")


def register_fonts() -> tuple[str, str, str, str]:
    candidates = {
        "body": ("Arial", Path("C:/Windows/Fonts/arial.ttf")),
        "bold": ("Arial-Bold", Path("C:/Windows/Fonts/arialbd.ttf")),
        "italic": ("Arial-Italic", Path("C:/Windows/Fonts/ariali.ttf")),
        "mono": ("Consolas", Path("C:/Windows/Fonts/consola.ttf")),
    }
    fallbacks = {"body": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique", "mono": "Courier"}
    result: dict[str, str] = {}
    for key, (name, path) in candidates.items():
        if path.exists():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            result[key] = name
        else:
            result[key] = fallbacks[key]
    return result["body"], result["bold"], result["italic"], result["mono"]


BODY_FONT, BOLD_FONT, ITALIC_FONT, MONO_FONT = register_fonts()


class ResearchDocument(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=20 * mm,
            bottomMargin=18 * mm,
            title="Barangay Minante 1 Integrated Management System Complete Reviewer",
            author="Barangay Minante 1 Integrated Management System",
            subject="Research reviewer, technical documentation, and code explanation",
        )
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates(PageTemplate(id="research", frames=[frame], onPage=self.draw_page))

    def draw_page(self, canvas, doc):
        canvas.saveState()
        width, height = A4
        if doc.page > 1:
            canvas.setStrokeColor(GOLD)
            canvas.setLineWidth(1.2)
            canvas.line(18 * mm, height - 13 * mm, width - 18 * mm, height - 13 * mm)
            canvas.setFont(BODY_FONT, 7.5)
            canvas.setFillColor(SLATE)
            canvas.drawString(18 * mm, height - 10 * mm, "BARANGAY MINANTE 1 INTEGRATED MANAGEMENT SYSTEM")
            canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {doc.page}")
            canvas.drawString(18 * mm, 10 * mm, "Complete System Reviewer • September 2026")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in {"Chapter", "Section"}:
            level = 0 if flowable.style.name == "Chapter" else 1
            text = flowable.getPlainText()
            key = f"heading-{level}-{self.seq.nextf('heading')}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
            self.notify("TOCEntry", (level, text, self.page, key))


def make_styles():
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=9.2,
            leading=13.4,
            textColor=SLATE,
            spaceAfter=6,
        ),
        "chapter": ParagraphStyle(
            "Chapter",
            parent=base["Heading1"],
            fontName=BOLD_FONT,
            fontSize=18,
            leading=22,
            textColor=NAVY,
            spaceAfter=10,
            keepWithNext=True,
        ),
        "section": ParagraphStyle(
            "Section",
            parent=base["Heading2"],
            fontName=BOLD_FONT,
            fontSize=13,
            leading=16,
            textColor=NAVY_DARK,
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "subsection": ParagraphStyle(
            "Subsection",
            parent=base["Heading3"],
            fontName=BOLD_FONT,
            fontSize=10.5,
            leading=13,
            textColor=NAVY,
            spaceBefore=6,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "callout": ParagraphStyle(
            "Callout",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=9,
            leading=13,
            textColor=NAVY_DARK,
        ),
        "code": ParagraphStyle(
            "Code",
            parent=base["Code"],
            fontName=MONO_FONT,
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#172033"),
            leftIndent=4,
            rightIndent=4,
        ),
        "table": ParagraphStyle(
            "TableText",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=7.4,
            leading=9.5,
            textColor=SLATE,
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            parent=base["BodyText"],
            fontName=BOLD_FONT,
            fontSize=7.5,
            leading=9.5,
            textColor=colors.white,
        ),
    }


STYLES = make_styles()


def inline_markup(value: str, mono: bool = True) -> str:
    value = html.escape(value.strip())
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"<i>\1</i>", value)
    if mono:
        value = re.sub(r"`(.+?)`", rf'<font name="{MONO_FONT}" color="#7C2D12">\1</font>', value)
    return value


def paragraph(text: str, style="body") -> Paragraph:
    return Paragraph(inline_markup(text), STYLES[style])


def callout(text: str) -> Table:
    cell = Paragraph(inline_markup(text), STYLES["callout"])
    table = Table([[cell]], colWidths=[174 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_GOLD),
                ("BOX", (0, 0), (-1, -1), 0.8, GOLD),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def markdown_table(lines: list[str]) -> Table:
    raw_rows = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in lines]
    if len(raw_rows) > 1 and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in raw_rows[1]):
        raw_rows.pop(1)
    columns = max(len(row) for row in raw_rows)
    for row in raw_rows:
        row.extend([""] * (columns - len(row)))
    data = []
    for row_no, row in enumerate(raw_rows):
        style = STYLES["table_head"] if row_no == 0 else STYLES["table"]
        data.append([Paragraph(inline_markup(cell), style) for cell in row])
    available = 174 * mm
    widths = [available / columns] * columns
    if columns == 2:
        widths = [available * 0.31, available * 0.69]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("GRID", (0, 0), (-1, -1), 0.35, LIGHT_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_BLUE]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def cover_story() -> list:
    title = Paragraph(
        "BARANGAY MINANTE 1<br/>INTEGRATED MANAGEMENT SYSTEM",
        ParagraphStyle(
            "CoverTitle", fontName=BOLD_FONT, fontSize=26, leading=31, alignment=TA_CENTER, textColor=NAVY
        ),
    )
    subtitle = Paragraph(
        "Complete System Reviewer, Technical Documentation,<br/>and Code Explanation",
        ParagraphStyle(
            "CoverSubtitle", fontName=BODY_FONT, fontSize=15, leading=21, alignment=TA_CENTER, textColor=SLATE
        ),
    )
    research = Paragraph(
        "<b>Development of an Integrated Barangay Permit, Event Approval, Blotter Management System with Notification and Scheduling Features for Barangay Minante 1, Cauayan City</b>",
        ParagraphStyle(
            "CoverResearch", fontName=BODY_FONT, fontSize=11, leading=16, alignment=TA_CENTER, textColor=NAVY_DARK
        ),
    )
    label = Table(
        [[Paragraph("RESEARCH • SYSTEM DEFENSE • TECHNICAL REFERENCE • MAINTENANCE", STYLES["table_head"])]],
        colWidths=[165 * mm],
    )
    label.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    footer = Paragraph(
        "Barangay Minante 1, Cauayan City, Isabela<br/><br/>Document Version 2.1 • September 9, 2026<br/><br/><i>Prepared from direct inspection of the current working source code and database schema.</i>",
        ParagraphStyle("CoverFooter", fontName=BODY_FONT, fontSize=10, leading=15, alignment=TA_CENTER, textColor=SLATE),
    )
    return [Spacer(1, 22 * mm), title, Spacer(1, 8 * mm), subtitle, Spacer(1, 15 * mm), label, Spacer(1, 18 * mm), research, Spacer(1, 24 * mm), footer, PageBreak()]


def parse_markdown(source: str) -> list:
    lines = source.splitlines()
    start = next(i for i, value in enumerate(lines) if value.startswith("## 1. Executive Summary"))
    lines = lines[start:]
    story: list = []
    i = 0
    chapter_seen = False
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped or stripped == "---":
            i += 1
            continue
        if stripped.startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i].rstrip())
                i += 1
            i += 1
            code = Preformatted("\n".join(code_lines), STYLES["code"])
            box = Table([[code]], colWidths=[174 * mm])
            box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")), ("BOX", (0, 0), (-1, -1), 0.5, LIGHT_BORDER), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
            story.extend([box, Spacer(1, 5)])
            continue
        if stripped.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            story.extend([markdown_table(table_lines), Spacer(1, 7)])
            continue
        if stripped.startswith("## "):
            if chapter_seen:
                story.append(PageBreak())
            chapter_seen = True
            story.append(Paragraph(inline_markup(stripped[3:]), STYLES["chapter"]))
            i += 1
            continue
        if stripped.startswith("### "):
            story.append(Paragraph(inline_markup(stripped[4:]), STYLES["section"]))
            i += 1
            continue
        if stripped.startswith("#### "):
            story.append(Paragraph(inline_markup(stripped[5:]), STYLES["subsection"]))
            i += 1
            continue
        if stripped.startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip()[1:].strip())
                i += 1
            story.extend([callout(" ".join(quote_lines)), Spacer(1, 6)])
            continue
        if re.match(r"^- ", stripped):
            items = []
            while i < len(lines) and re.match(r"^- ", lines[i].strip()):
                items.append(ListItem(paragraph(lines[i].strip()[2:]), leftIndent=10))
                i += 1
            story.append(ListFlowable(items, bulletType="bullet", bulletFontName=BODY_FONT, bulletFontSize=7, leftIndent=16, bulletColor=GOLD))
            story.append(Spacer(1, 4))
            continue
        if re.match(r"^\d+\. ", stripped):
            items = []
            first = int(re.match(r"^(\d+)\.", stripped).group(1))
            while i < len(lines) and re.match(r"^\d+\. ", lines[i].strip()):
                item_text = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                items.append(ListItem(paragraph(item_text), leftIndent=12))
                i += 1
            story.append(ListFlowable(items, bulletType="1", start=str(first), leftIndent=22, bulletFontName=BOLD_FONT, bulletFontSize=8))
            story.append(Spacer(1, 4))
            continue
        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            candidate = lines[i].strip()
            if not candidate or candidate == "---" or candidate.startswith(("#", "|", ">", "```")) or re.match(r"^(- |\d+\. )", candidate):
                break
            paragraph_lines.append(candidate)
            i += 1
        story.append(paragraph(" ".join(paragraph_lines)))
    return story


def build_pdf():
    source = SOURCE.read_text(encoding="utf-8")
    story = cover_story()
    story.append(Paragraph("Table of Contents", STYLES["chapter"]))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("TOC1", fontName=BOLD_FONT, fontSize=9.5, leading=13, leftIndent=0, firstLineIndent=0, textColor=NAVY, spaceBefore=3),
        ParagraphStyle("TOC2", fontName=BODY_FONT, fontSize=8, leading=10.5, leftIndent=12, firstLineIndent=0, textColor=SLATE),
    ]
    story.extend([toc, PageBreak()])
    story.extend(parse_markdown(source))
    document = ResearchDocument(str(OUTPUT))
    document.multiBuild(story)
    print(f"Generated {OUTPUT}")


if __name__ == "__main__":
    build_pdf()
