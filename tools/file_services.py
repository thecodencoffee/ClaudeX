"""Local file extraction and formatted PDF export services for ClaudeX."""

from __future__ import annotations

import html
import io
import json
import mimetypes
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .lazy_import import lazy_import

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_EXTRACTED_CHARS = 48_000
MAX_ZIP_UNCOMPRESSED = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 2_000
MAX_FILE_COUNT = 5

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".css", ".sql", ".sh", ".toml", ".ini", ".log", ".tex", ".java",
    ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".swift",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS


def _check_zip_budget(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES or sum(entry.file_size for entry in entries) > MAX_ZIP_UNCOMPRESSED:
                raise ValueError("This Office file expands beyond the safe processing limit.")
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded Office document is invalid or damaged.") from exc


def _extract_image(data: bytes) -> str:
    PILImage = lazy_import("PIL.Image")
    subprocess = lazy_import("subprocess")

    try:
        with PILImage.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:
        raise ValueError("The image is invalid or uses an unsupported encoding.") from exc
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "6"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace").strip()[:MAX_EXTRACTED_CHARS]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def extract_upload(filename: str, data: bytes) -> dict[str, Any]:
    """Extract safe, bounded text. Images also retain bytes for visual-capable models."""
    name = Path(filename.replace("\\", "/")).name.strip()
    suffix = Path(name).suffix.lower()
    if not name or suffix not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type. Supported extensions: {supported}")
    if not data:
        raise ValueError(f"{name} is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"{name} exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB per-file limit.")

    image_data: bytes | None = None
    if suffix in TEXT_EXTENSIONS:
        decoded = data.decode("utf-8-sig", errors="replace").replace("\x00", "")
        if suffix in {".html", ".htm", ".xml"}:
            BeautifulSoup = lazy_import("bs4").BeautifulSoup

            soup = BeautifulSoup(decoded, "html.parser")
            for node in soup(["script", "style", "noscript"]):
                node.decompose()
            text = soup.get_text("\n", strip=True)
        elif suffix in {".csv", ".tsv"}:
            text = decoded
        elif suffix == ".json":
            try:
                text = json.dumps(json.loads(decoded), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                text = decoded
        else:
            text = decoded
    elif suffix == ".pdf":
        try:
            PdfReader = lazy_import("pypdf").PdfReader

            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("The PDF is password protected. Remove its password before attaching it.")
            if len(reader.pages) > 120:
                raise ValueError("PDFs are limited to 120 pages per upload.")
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
            if not text.strip():
                text = "No selectable text was found in this PDF; it may be a scanned document."
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Could not read this PDF. It may be damaged or unsupported.") from exc
    elif suffix == ".docx":
        _check_zip_budget(data)
        Document = lazy_import("docx").Document

        document = Document(io.BytesIO(data))
        parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            parts.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
        text = "\n".join(parts)
    elif suffix == ".xlsx":
        _check_zip_budget(data)
        load_workbook = lazy_import("openpyxl").load_workbook

        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        cell_count = 0
        for sheet in workbook.worksheets:
            parts.append(f"## Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cell_count += len(row)
                if cell_count > 40_000:
                    parts.append("[Content truncated at the 40,000-cell safety limit]")
                    break
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    parts.append("\t".join(values))
            if cell_count > 40_000:
                break
        workbook.close()
        text = "\n".join(parts)
    elif suffix == ".pptx":
        _check_zip_budget(data)
        Presentation = lazy_import("pptx").Presentation

        presentation = Presentation(io.BytesIO(data))
        parts = []
        for index, slide in enumerate(presentation.slides, start=1):
            parts.append(f"## Slide {index}")
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text.strip():
                    parts.append(shape.text.strip())
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        parts.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(parts)
    else:
        image_data = data
        text = _extract_image(data)
        if not text:
            text = "No text could be read by local OCR. Select Nemotron 3.5 Super VL for direct visual analysis."

    text = text.strip()[:MAX_EXTRACTED_CHARS]
    return {
        "filename": name[:240],
        "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
        "size_bytes": len(data),
        "extracted_text": text,
        "image_data": image_data,
    }


_MATH = re.compile(r"\$\$(.+?)\$\$|(?<!\\)\$(?!\s)(.+?)(?<!\s)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
_ALLOWED_HTML = {
    "p", "br", "strong", "em", "b", "i", "u", "s", "h1", "h2", "h3", "h4",
    "ul", "ol", "li", "blockquote", "pre", "code", "hr", "table", "thead",
    "tbody", "tr", "th", "td", "a",
}


def _math_image(formula: str, math_dir: Path, index: int) -> str:
    math_to_image = lazy_import("matplotlib.mathtext").math_to_image
    PILImage = lazy_import("PIL.Image")

    path = math_dir / f"math-{index}.png"
    math_to_image(f"${formula.strip()}$", str(path), dpi=180, format="png", color="#202020")
    with PILImage.open(path) as image:
        width, height = image.size
    width_pt = width * 72 / 180
    height_pt = height * 72 / 180
    return f'<img src="{html.escape(str(path), quote=True)}" width="{width_pt:.2f}" height="{height_pt:.2f}" valign="middle"/>'


def _inline(node: Any, math_dir: Path, math_counter: list[int]) -> str:
    if isinstance(node, NavigableString):
        text = str(node)
        chunks = []
        cursor = 0
        for match in _MATH.finditer(text):
            chunks.append(html.escape(text[cursor:match.start()]))
            formula = next(group for group in match.groups() if group is not None)
            math_counter[0] += 1
            try:
                chunks.append(_math_image(formula, math_dir, math_counter[0]))
            except Exception:
                chunks.append(f'<font name="Courier">{html.escape(match.group(0))}</font>')
            cursor = match.end()
        chunks.append(html.escape(text[cursor:]))
        return "".join(chunks)
    tag_map = {"strong": "b", "em": "i", "code": "font"}
    tag = tag_map.get(node.name, node.name)
    content = "".join(_inline(child, math_dir, math_counter) for child in node.children)
    if tag == "font":
        return f'<font name="Courier">{content}</font>'
    if tag == "a":
        href = node.get("href", "")
        return f'<link href="{html.escape(href, quote=True)}">{content}</link>' if href else content
    if tag in {"b", "i", "u", "s", "sup", "sub", "br"}:
        return "<br/>" if tag == "br" else f"<{tag}>{content}</{tag}>"
    return content


def _pdf_flowables(parent: Any, styles: dict[str, Any], math_dir: Path, math_counter: list[int]) -> list[Any]:
    result: list[Any] = []
    for node in parent.children:
        if isinstance(node, NavigableString) or not isinstance(node, Tag):
            continue
        name = node.name.lower()
        if name in {"h1", "h2", "h3", "h4"}:
            result.append(Paragraph("".join(_inline(child, math_dir, math_counter) for child in node.children), styles[f"doc_{name}"]))
            result.append(Spacer(1, 3 * mm))
        elif name == "p":
            result.append(Paragraph("".join(_inline(child, math_dir, math_counter) for child in node.children), styles["body"]))
            result.append(Spacer(1, 2.4 * mm))
        elif name in {"ul", "ol"}:
            items = []
            for item in node.find_all("li", recursive=False):
                inline_children = [child for child in item.children if not (isinstance(child, Tag) and child.name in {"ul", "ol"})]
                para = Paragraph("".join(_inline(child, math_dir, math_counter) for child in inline_children), styles["body"])
                nested = []
                for child in item.find_all(["ul", "ol"], recursive=False):
                    nested.extend(_pdf_flowables(BeautifulSoup(str(child), "html.parser"), styles, math_dir, math_counter))
                items.append(ListItem([para, *nested], leftIndent=10))
            if items:
                result.append(ListFlowable(items, bulletType="1" if name == "ol" else "bullet", start="1", leftIndent=19, bulletFontName="Helvetica", bulletFontSize=8, spaceAfter=5))
        elif name == "pre":
            code = html.escape(node.get_text().rstrip())
            result.append(Preformatted(code, styles["code"]))
            result.append(Spacer(1, 3 * mm))
        elif name == "blockquote":
            quote = Paragraph("".join(_inline(child, math_dir, math_counter) for child in node.children), styles["quote"])
            result.append(quote)
            result.append(Spacer(1, 2 * mm))
        elif name == "table":
            rows = []
            for row in node.find_all("tr"):
                cells = [Paragraph("".join(_inline(child, math_dir, math_counter) for child in cell.children), styles["table_header" if cell.name == "th" else "table_cell"]) for cell in row.find_all(["th", "td"], recursive=False)]
                if cells:
                    rows.append(cells)
            if rows:
                table = Table(rows, repeatRows=1, hAlign="LEFT")
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef5")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b8c0ca")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                result.extend([table, Spacer(1, 4 * mm)])
        elif name == "hr":
            result.extend([HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#9ba4af")), Spacer(1, 4 * mm)])
        else:
            result.extend(_pdf_flowables(node, styles, math_dir, math_counter))
    return result


def create_markdown_pdf(title: str, content: str, destination: Path) -> None:
    """Create a formatted PDF. Common LaTeX mathtext expressions become inline images."""
    global bleach, markdown, BeautifulSoup, NavigableString, Tag
    global colors, TA_LEFT, A4, ParagraphStyle, getSampleStyleSheet, mm
    global HRFlowable, ListFlowable, ListItem, Paragraph, Preformatted
    global SimpleDocTemplate, Spacer, Table, TableStyle

    bleach = lazy_import("bleach")
    markdown = lazy_import("markdown")
    bs4 = lazy_import("bs4")
    BeautifulSoup, NavigableString, Tag = bs4.BeautifulSoup, bs4.NavigableString, bs4.Tag
    colors = lazy_import("reportlab.lib.colors")
    TA_LEFT = lazy_import("reportlab.lib.enums").TA_LEFT
    A4 = lazy_import("reportlab.lib.pagesizes").A4
    styles_module = lazy_import("reportlab.lib.styles")
    ParagraphStyle, getSampleStyleSheet = styles_module.ParagraphStyle, styles_module.getSampleStyleSheet
    mm = lazy_import("reportlab.lib.units").mm
    platypus = lazy_import("reportlab.platypus")
    HRFlowable, ListFlowable, ListItem = platypus.HRFlowable, platypus.ListFlowable, platypus.ListItem
    Paragraph, Preformatted, SimpleDocTemplate = platypus.Paragraph, platypus.Preformatted, platypus.SimpleDocTemplate
    Spacer, Table, TableStyle = platypus.Spacer, platypus.Table, platypus.TableStyle

    title = re.sub(r"\s+", " ", title).strip()[:180] or "ClaudeX document"
    content = content.strip()
    if not content or len(content) > 100_000:
        raise ValueError("PDF content must contain 1–100,000 characters.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="pdf_title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=21, leading=26, textColor=colors.HexColor("#14253a"), alignment=TA_LEFT, spaceAfter=10 * mm))
    for level, size in ((1, 17), (2, 14), (3, 12), (4, 11)):
        styles.add(ParagraphStyle(name=f"doc_h{level}", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=size, leading=size + 4, textColor=colors.HexColor("#183453"), spaceBefore=5 * mm, spaceAfter=2 * mm, keepWithNext=True))
    styles.add(ParagraphStyle(name="body", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=15, textColor=colors.HexColor("#222222"), alignment=TA_LEFT, splitLongWords=True))
    styles.add(ParagraphStyle(name="quote", parent=styles["body"], leftIndent=12, borderColor=colors.HexColor("#78a9dc"), borderWidth=2, borderPadding=7, textColor=colors.HexColor("#444444")))
    styles.add(ParagraphStyle(name="code", fontName="Courier", fontSize=8, leading=11, backColor=colors.HexColor("#f2f4f6"), borderPadding=7, leftIndent=7, rightIndent=7, spaceBefore=3, spaceAfter=7, splitLongWords=True))
    styles.add(ParagraphStyle(name="table_cell", parent=styles["body"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="table_header", parent=styles["table_cell"], fontName="Helvetica-Bold"))
    rendered = markdown.markdown(content, extensions=["fenced_code", "tables", "sane_lists"])
    rendered = bleach.clean(rendered, tags=_ALLOWED_HTML, attributes={"a": ["href"]}, protocols={"http", "https", "mailto"}, strip=True)
    soup = BeautifulSoup(rendered, "html.parser")
    with tempfile.TemporaryDirectory(prefix="claudex-math-") as temporary:
        math_dir = Path(temporary)
        story: list[Any] = [Paragraph(html.escape(title), styles["pdf_title"])]
        story.extend(_pdf_flowables(soup, styles, math_dir, [0]))
        doc = SimpleDocTemplate(str(destination), pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm, topMargin=20 * mm, bottomMargin=18 * mm, title=title, author="ClaudeX")

        def page(canvas, document):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#d6dce3"))
            canvas.line(20 * mm, 14 * mm, A4[0] - 20 * mm, 14 * mm)
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#697586"))
            canvas.drawString(20 * mm, 9 * mm, "Created with ClaudeX")
            canvas.drawRightString(A4[0] - 20 * mm, 9 * mm, str(document.page))
            canvas.restoreState()

        doc.build(story, onFirstPage=page, onLaterPages=page)
