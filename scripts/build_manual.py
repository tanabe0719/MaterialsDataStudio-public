"""Build self-contained English and Japanese HTML manuals.

This is documentation tooling only.  It deliberately uses the Python standard
library so the manual can be rebuilt without adding an application dependency.
The resulting HTML embeds screenshot PNGs and is suitable for browser PDF
printing.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "dist_manual"
SOURCE = ROOT / "docs" / "user_manual.md"
OUTPUT = OUTPUT_DIR / "Materials_Data_Studio_User_Manual.html"
MANUALS = (
    (
        ROOT / "docs" / "user_manual_en.md",
        OUTPUT_DIR / "Materials_Data_Studio_User_Manual_EN.html",
        "en",
        "Materials Data Studio User Manual",
    ),
    (
        ROOT / "docs" / "user_manual.md",
        OUTPUT_DIR / "Materials_Data_Studio_User_Manual.html",
        "ja",
        "Materials Data Studio ユーザーマニュアル",
    ),
    (
        ROOT / "docs" / "graph_viewer_manual_en.md",
        OUTPUT_DIR / "Materials_Data_Studio_Graph_Viewer_Manual_EN.html",
        "en", "Graph Viewer / Graph Editor Manual",
    ),
    (
        ROOT / "docs" / "graph_viewer_manual.md",
        OUTPUT_DIR / "Materials_Data_Studio_Graph_Viewer_Manual.html",
        "ja", "Graph Viewer / Graph Editor マニュアル",
    ),
)


def embed_image(source: str) -> str:
    path = (SOURCE.parent / source).resolve()
    docs_root = SOURCE.parent.resolve()
    if docs_root not in path.parents or not path.is_file():
        raise FileNotFoundError(f"Manual image does not exist: {source}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def render_inline(text: str) -> str:
    placeholders: list[str] = []

    def preserve(value: str) -> str:
        placeholders.append(value)
        return f"\x00{len(placeholders) - 1}\x00"

    full_window_prefixes = (
        *(f"screenshots/{index:02d}_" for index in range(1, 19)),
        "screenshots/23_",
        "screenshots/27_",
        "screenshots/28_",
        "screenshots/29_",
        "screenshots/34_",
        "screenshots/35_",
        "screenshots/36_",
        "screenshots/37_",
        "screenshots/39_",
        "screenshots/40_",
        "screenshots/48_",
        "screenshots/49_",
        "screenshots/52_",
        "screenshots/54_",
        "screenshots/55_",
        "screenshots/56_",
        "screenshots/57_",
        "screenshots/58_",
        "screenshots/59_",
        "screenshots/62_",
        "screenshots/85_",
        "screenshots/86_",
    )

    def render_image(match: re.Match[str]) -> str:
        css_class = (
            ' class="manual-full-window"'
            if match.group(2).startswith(full_window_prefixes)
            else ""
        )
        return preserve(
            f"<figure{css_class}><img src=\""
            + embed_image(match.group(2))
            + '" alt="'
            + html.escape(match.group(1), quote=True)
            + '"><figcaption>'
            + html.escape(match.group(1))
            + "</figcaption></figure>"
        )

    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        render_image,
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: preserve(
            '<a href="'
            + html.escape(match.group(2), quote=True)
            + '">'
            + html.escape(match.group(1))
            + "</a>"
        ),
        text,
    )
    text = re.sub(
        r"`([^`]+)`",
        lambda match: preserve("<code>" + html.escape(match.group(1)) + "</code>"),
        text,
    )
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    for index, value in enumerate(placeholders):
        escaped = escaped.replace(f"\x00{index}\x00", value)
    return escaped


def is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def heading_anchor(title: str) -> str:
    """Return a stable anchor for numbered manual headings."""
    plain = re.sub(r"[`*_]", "", title).strip()
    numbered = re.match(r"^(\d+(?:\.\d+)*)\.\s+", plain)
    if numbered:
        return "section-" + numbered.group(1).replace(".", "-")
    slug = re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-")
    return slug or "section"


def render_markdown(markdown: str) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            output.append(
                f'<pre data-language="{html.escape(language)}"><code>'
                + html.escape("\n".join(code))
                + "</code></pre>"
            )
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            title = render_inline(heading.group(2))
            anchor = heading_anchor(heading.group(2))
            output.append(f'<h{level} id="{anchor}">{title}</h{level}>')
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and (
                lines[index].strip().startswith(">") or not lines[index].strip()
            ):
                value = lines[index].strip()
                if value.startswith(">"):
                    quote_lines.append(value[1:].strip())
                index += 1
            paragraphs = [
                render_inline(part.strip())
                for part in "\n".join(quote_lines).split("\n\n")
                if part.strip()
            ]
            output.append("<blockquote>" + "".join(f"<p>{p}</p>" for p in paragraphs) + "</blockquote>")
            continue

        if "|" in stripped and index + 1 < len(lines) and is_table_separator(lines[index + 1]):
            header_cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            table = ["<table><thead><tr>"]
            table.extend(f"<th>{render_inline(cell)}</th>" for cell in header_cells)
            table.append("</tr></thead><tbody>")
            for row in rows:
                table.append("<tr>")
                table.extend(f"<td>{render_inline(cell)}</td>" for cell in row)
                table.append("</tr>")
            table.append("</tbody></table>")
            output.append("".join(table))
            continue

        list_match = re.match(r"^(\d+\.|-)\s+(.+)$", stripped)
        if list_match:
            ordered = list_match.group(1) != "-"
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < len(lines):
                item_match = re.match(r"^(\d+\.|-)\s+(.+)$", lines[index].strip())
                if not item_match or (item_match.group(1) != "-") != ordered:
                    break
                items.append(item_match.group(2))
                index += 1
                while index < len(lines) and lines[index].startswith("  ") and lines[index].strip():
                    continuation = lines[index].strip()
                    if re.match(r"^(\d+\.|-)\s+", continuation):
                        break
                    items[-1] += " " + continuation
                    index += 1
            output.append(
                f"<{tag}>" + "".join(f"<li>{render_inline(item)}</li>" for item in items) + f"</{tag}>"
            )
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if (
                not candidate
                or candidate.startswith("#")
                or candidate.startswith(">")
                or candidate.startswith("```")
                or re.match(r"^(\d+\.|-)\s+", candidate)
                or (
                    "|" in candidate
                    and index + 1 < len(lines)
                    and is_table_separator(lines[index + 1])
                )
            ):
                break
            paragraph.append(candidate)
            index += 1
        rendered = render_inline(" ".join(paragraph))
        if rendered.startswith("<figure") and rendered.endswith("</figure>"):
            output.append(rendered)
        else:
            output.append(f"<p>{rendered}</p>")

    return "\n".join(output)


def main() -> None:
    global SOURCE, OUTPUT
    css = """
    @page { size: A4; margin: 16mm 15mm 17mm; }
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    body {
      margin: 0 auto; max-width: 190mm; color: #17202a; background: white;
      font-family: "Yu Gothic UI", "Yu Gothic", "Meiryo", sans-serif;
      font-size: 10pt; line-height: 1.62;
    }
    h1 { color: #103d5b; font-size: 25pt; border-bottom: 4px solid #1597a8;
         padding: 22mm 0 5mm; margin: 0 0 10mm; }
    h2 { color: #103d5b; font-size: 18pt; border-bottom: 2px solid #81c7cf;
         margin: 11mm 0 4mm; padding-bottom: 1.5mm; break-after: avoid-page; }
    h3 { color: #155b70; font-size: 13.5pt; margin: 7mm 0 2.5mm;
         break-after: avoid-page; }
    p { margin: 0 0 3.2mm; }
    ul, ol { margin: 1.5mm 0 4mm 6mm; padding-left: 5mm; }
    li { margin: 0.8mm 0; }
    code { font-family: Consolas, monospace; background: #eef3f5; padding: 0.15em 0.35em;
           border-radius: 2px; font-size: 0.92em; }
    pre { background: #172633; color: #f4f8fa; padding: 4mm; border-radius: 4px;
          white-space: pre-wrap; break-inside: avoid; }
    pre code { background: transparent; padding: 0; }
    blockquote { margin: 5mm 0; padding: 4mm 5mm; background: #fff8dd;
                 border-left: 4px solid #e0a826; break-inside: avoid; }
    blockquote p:last-child { margin-bottom: 0; }
    h2, h3 { break-after: avoid; page-break-after: avoid; }
    figure { margin: 5mm 0 7mm; break-inside: avoid; page-break-inside: avoid;
             text-align: center; }
    figure.manual-full-window { break-before: auto; }
    figure img { display: block; margin: 0 auto; max-width: 100%; max-height: 175mm; object-fit: contain; }
    figure.manual-full-window img { display: block; width: 100%; height: auto;
                                    max-height: none; }
    figcaption { margin-top: 1.5mm; color: #51606a; font-size: 8.5pt; text-align: left; }
    table { width: 100%; border-collapse: collapse; margin: 4mm 0 6mm;
            font-size: 8.8pt; break-inside: auto; }
    tr { break-inside: avoid; }
    th { background: #dceff2; color: #103d5b; text-align: left; }
    th, td { border: 1px solid #9fb0b8; padding: 1.7mm 2mm; vertical-align: top; }
    a { color: #086b85; }
    .manual-toc { break-inside: avoid; page-break-inside: avoid; }
    .manual-toc ul { columns: 2; column-gap: 8mm; margin-left: 0; }
    .manual-toc li { break-inside: avoid; }
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for source, output, language, title in MANUALS:
        SOURCE = source
        OUTPUT = output
        body = render_markdown(SOURCE.read_text(encoding="utf-8"))
        body = re.sub(r'(<h2 id="(?:contents|section)">.*?</h2>\s*<ul>.*?</ul>)',
                      r'<section class="manual-toc">\1</section>', body, count=1, flags=re.DOTALL)
        document = f"""<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""
        OUTPUT.write_text(document, encoding="utf-8")
        print(
            f"Built {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size:,} bytes)"
        )


if __name__ == "__main__":
    main()
