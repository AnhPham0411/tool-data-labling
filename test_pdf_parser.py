"""
test_pdf_parser.py — Test script cho pdf_parser.py

Cách dùng:
    python test_pdf_parser.py <đường_dẫn_file.pdf>
    python test_pdf_parser.py <đường_dẫn_file.pdf> --output result.txt
"""

import sys
import io
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from modules.pdf_parser import parse_article, _detect_citation_format, _detect_heading_size


def format_result(article: dict, cit_format: str, heading_size: float) -> str:
    lines = []

    lines.append("=" * 60)
    lines.append(f"TIÊU ĐỀ   : {article['title']}")
    lines.append(f"DOMAIN    : {article['domain_key']} — {article['domain_name']}")
    lines.append(f"CIT FORMAT: {cit_format}")
    lines.append(f"HEADING sz: {heading_size:.1f}pt")
    lines.append(f"SECTIONS  : {len(article['sections'])}")
    lines.append(f"CLAIMS    : {article['claims_count']}")
    lines.append("=" * 60)

    for sec in article["sections"]:
        lines.append(f"\n## {sec['heading']}")
        for i, para in enumerate(sec["paragraphs"], 1):
            cits = para["citations"]
            lines.append(f"\n[Đoạn {i}] citations={cits}")
            lines.append(para["text"])

    lines.append("\n" + "=" * 60)
    lines.append("HEADINGS:")
    for h in article["headings"]:
        lines.append(f"  - {h}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Test pdf_parser.py")
    parser.add_argument("pdf", help="Đường dẫn file PDF")
    parser.add_argument("--output", "-o", help="Lưu kết quả ra file txt")
    args = parser.parse_args()

    print(f"Đang đọc: {args.pdf}")

    cit_format   = _detect_citation_format(args.pdf)
    heading_size = _detect_heading_size(args.pdf)
    article      = parse_article(args.pdf)

    result = format_result(article, cit_format, heading_size)
    print(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result + "\n")
        print(f"\n→ Đã lưu vào: {args.output}")


if __name__ == "__main__":
    main()
