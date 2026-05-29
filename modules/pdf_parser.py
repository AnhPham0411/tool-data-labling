"""
pdf_parser.py — Trích xuất nội dung bài viết từ PDF.

Logic lấy từ script3.py (e:\\a\\toolVsf):
- detect_heading_size: tần suất font size → tự tìm ngưỡng heading
- extract_sections: bỏ "Tóm tắt nhanh", nhóm paragraph theo heading
- extract_article_title: span đầu size >= 14pt trang 1
- detect_domain: keyword scoring từ tiêu đề + heading
"""
import re
from collections import Counter
import fitz  # PyMuPDF

HEADING_RATIO = 1.3       # heading phải lớn hơn body ít nhất bao nhiêu lần
SKIP_SECTIONS = {"Tóm tắt nhanh", "Tóm tắt"}
SKIP_SECTIONS_MED = {"Lưu ý y tế", "Lưu ý", "Disclaimer"}  # disclaimer đầu/cuối bài y tế
CONTENT_MARKERS  = ["Nội dung bài viết"]        # inline start marker (CMS export)
END_MARKERS      = {"Nguồn tham khảo", "Tài liệu tham khảo", "SEO"}

# Regex citation theo từng format
_RE_CIT_LAW = re.compile(r"\[(\d+)\]")
# Med: match cả nhóm [...] chứa ít nhất 1 src_xxx, vd: [src_vinmec_com_000, src_canhgiac_013]
_RE_CIT_MED_GROUP = re.compile(r"\[src_[^\]]+\]")
# Extract từng số cuối trong 1 nhóm med, vd "000" "004" từ "[src_vinmec_com_000, src_x_004]"
_RE_CIT_MED_NUM   = re.compile(r"src_[a-z0-9_]+_(\d+)")
# Strip toàn bộ nhóm citation med khỏi text
_RE_CIT_MED_STRIP = re.compile(r"\s*\[src_[^\]]+\]")

DOMAIN_MAP = {
    "law": "Pháp luật",
    "med": "Y tế & Sức khỏe",
    "trv": "Du lịch",
    "fin": "Tài chính & Kinh tế",
    "gov": "Chính trị & Hành chính",
    "edu": "Giáo dục",
    "sci": "Khoa học & Công nghệ",
    "biz": "Kinh doanh & Quản trị",
    "cul": "Văn hóa & Xã hội",
    "his": "Lịch sử & Địa lý",
    "re":  "Bất động sản & Xây dựng",
    "env": "Môi trường & Tài nguyên",
    "ent": "Thể thao & Giải trí",
}

_DOMAIN_KEYWORDS = {
    "law": ["luật","nghị định","thông tư","quyết định","pháp lý","pháp luật",
            "khiếu nại","tố cáo","xử phạt","thủ tục hành chính","hồ sơ",
            "giấy phép","công chứng","hải quan","tư pháp",
            "điều khoản","hiệu lực","văn bản pháp luật","vi phạm","chế tài"],
    "med": ["bệnh","triệu chứng","điều trị","thuốc","vaccine","tiêm chủng",
            "bác sĩ","bệnh viện","y tế","sức khỏe","phòng ngừa","chẩn đoán",
            "dược","liều dùng","phẫu thuật","xét nghiệm","ung thư","tiểu đường",
            "huyết áp","tim mạch","nhi khoa","sản khoa","khớp","nội khoa"],
    "trv": ["du lịch","điểm đến","tham quan","khách sạn","tour","lữ hành",
            "visa","hộ chiếu","đặt phòng","ẩm thực địa phương",
            "đặc sản","di tích","danh lam thắng cảnh","resort","lịch trình du lịch",
            "check-in","homestay","cáp treo"],
    "fin": ["tài chính","ngân hàng","lãi suất","đầu tư","chứng khoán",
            "cổ phiếu","tín dụng","vay vốn","bảo hiểm","kinh tế vĩ mô",
            "thuế","kế toán","kiểm toán","tiết kiệm","quỹ đầu tư","crypto"],
    "gov": ["chính phủ","ủy ban nhân dân","hội đồng nhân dân","chính sách công",
            "thủ tướng","bộ trưởng","ngoại giao","quan hệ quốc tế",
            "cải cách hành chính","bầu cử","đảng","nhà nước"],
    "edu": ["giáo dục","học sinh","sinh viên","trường","giảng viên","giáo viên",
            "chương trình học","đại học","cao đẳng","tuyển sinh","học bổng",
            "kỹ năng","hướng nghiệp","sư phạm","đào tạo"],
    "sci": ["khoa học","công nghệ","nghiên cứu","phần mềm","lập trình","trí tuệ nhân tạo",
            "ai","dữ liệu","robot","kỹ thuật","công nghiệp","nông nghiệp","sinh học",
            "vũ trụ","vật lý","hóa học","điện tử"],
    "biz": ["kinh doanh","doanh nghiệp","startup","khởi nghiệp","marketing",
            "thương hiệu","quản trị","nhân sự","chiến lược","thị trường",
            "doanh thu","lợi nhuận","quản lý dự án","bán hàng"],
    "cul": ["văn hóa","phong tục","tín ngưỡng","lễ hội","nghệ thuật","âm nhạc",
            "điện ảnh","văn học","ngôn ngữ","dân tộc","tôn giáo","triết học",
            "truyền thống","bản sắc"],
    "his": ["lịch sử","địa lý","địa danh","di sản","di tích lịch sử","chiến tranh",
            "triều đại","thời kỳ","vương quốc","địa hình","sông núi","dân số"],
    "re":  ["bất động sản","nhà đất","căn hộ","chung cư","quy hoạch","đô thị",
            "xây dựng","kiến trúc","nội thất","hạ tầng","mặt bằng","sàn giao dịch"],
    "env": ["môi trường","khí hậu","ô nhiễm","năng lượng","rừng","biển","sinh thái",
            "tái chế","chất thải","biến đổi khí hậu","đa dạng sinh học","tài nguyên"],
    "ent": ["thể thao","bóng đá","game","esports","giải trí","phim","ca sĩ",
            "vận động viên","giải đấu","huy chương","sân khấu","nghệ sĩ"],
}


# ─────────────────────────────────────────────────────────────────────────────

def _detect_heading_size(pdf_path: str) -> float:
    """
    Tìm font size của section heading dựa trên tần suất:
    1. body_size = size xuất hiện nhiều nhất
    2. Trong các size >= body * HEADING_RATIO, lấy cái xuất hiện nhiều nhất
       → là section heading (lặp lại nhiều lần), không phải title (1-2 lần)
    Cách này bền với mọi mức zoom/print vì dùng tỉ lệ thay vì threshold cứng.
    """
    doc = fitz.open(pdf_path)
    sizes: list[float] = []
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    if not t or ord(t[0]) > 0xE000:
                        continue
                    sizes.append(round(span["size"], 1))
    doc.close()
    if not sizes:
        return 10.0
    body_size = Counter(sizes).most_common(1)[0][0]
    threshold = body_size * HEADING_RATIO
    large_sizes = [s for s in sizes if s >= threshold]
    if not large_sizes:
        return threshold
    return Counter(large_sizes).most_common(1)[0][0]


def _extract_article_title(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    page = doc[0]
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for line in b["lines"]:
            for span in line["spans"]:
                if span["size"] >= 14 and span["text"].strip():
                    doc.close()
                    return span["text"].strip()
    doc.close()
    return "Untitled"


def _detect_citation_format(pdf_path: str) -> str:
    """
    Đọc raw text toàn PDF, detect format citation đang dùng.
    Trả về 'med' nếu có [src_xxx_NNN], 'law' nếu có [N], 'none' nếu không có gì.
    """
    doc = fitz.open(pdf_path)
    text = "".join(page.get_text() for page in doc)
    doc.close()
    if _RE_CIT_MED_GROUP.search(text):
        return "med"
    if _RE_CIT_LAW.search(text):
        return "law"
    return "none"


def _extract_sections(pdf_path: str) -> list[dict]:
    cit_format = _detect_citation_format(pdf_path)
    heading_size = _detect_heading_size(pdf_path)

    def is_heading(span):
        return abs(span["size"] - heading_size) < heading_size * 0.08

    def is_footer(text):
        return bool(re.match(r"^Vivipedia\s", text))

    def _buffer_closed(buf: str) -> bool:
        """
        Paragraph kết thúc khi buffer kết thúc bằng ] và không còn [ nào chưa đóng.
        Dùng chung cho cả law và med — đơn giản đếm [ và ] toàn buffer.
        """
        stripped = buf.strip()
        if not stripped.endswith("]"):
            return False
        return stripped.count("[") == stripped.count("]")

    skip = SKIP_SECTIONS | (SKIP_SECTIONS_MED if cit_format == "med" else set())

    doc = fitz.open(pdf_path)
    raw_blocks = []
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            block_text, block_is_heading = "", False
            for line in b["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    if not t or ord(t[0]) > 0xE000:
                        continue
                    if is_heading(span):
                        block_is_heading = True
                    block_text += t + " "
            block_text = block_text.strip()
            if block_text:
                raw_blocks.append({"text": block_text, "is_heading": block_is_heading})
    doc.close()

    start_idx = 0
    for i, b in enumerate(raw_blocks):
        if b["is_heading"] and b["text"].strip() in skip:
            start_idx = i + 1
            break

    sections, cur_heading, cur_paras = [], None, []
    para_buffer = ""

    def flush():
        nonlocal para_buffer
        raw = para_buffer.strip()
        if not raw:
            para_buffer = ""
            return
        if cit_format == "med":
            # Extract tất cả số từ mọi nhóm [...], cộng +1 (0-based → 1-based)
            citations = [int(n) + 1 for n in _RE_CIT_MED_NUM.findall(raw)]
            # Strip toàn bộ nhóm [src_...] kể cả ở giữa đoạn
            clean = _RE_CIT_MED_STRIP.sub("", raw).strip()
            clean = re.sub(r"\s{2,}", " ", clean)
        else:
            citations = [int(m) for m in _RE_CIT_LAW.findall(raw)]
            # Law: strip citation ở cuối đoạn (dạng [1][2] hoặc [1,2])
            clean = re.sub(r"(\s*\[\d+(?:,\s*\d+)*\])+\s*$", "", raw).strip()
        if clean:
            cur_paras.append({"text": clean, "citations": citations})
        para_buffer = ""

    for b in raw_blocks[start_idx:]:
        if is_footer(b["text"]):
            flush()
            break
        # Skip disclaimer heading ở bất kỳ vị trí nào trong bài
        if b["is_heading"] and b["text"].strip() in skip:
            flush()
            # Bỏ qua luôn các block body tiếp theo cho đến heading kế
            continue
        if b["is_heading"]:
            flush()
            if cur_heading is not None:
                sections.append({"heading": cur_heading, "paragraphs": cur_paras})
            cur_heading = b["text"].strip()
            cur_paras   = []
            para_buffer = ""
        else:
            # Gom block vào buffer — dùng chung cho cả law và med
            para_buffer = (para_buffer + " " + b["text"]).strip()
            if _buffer_closed(para_buffer):
                flush()

    flush()
    if cur_heading is not None and cur_paras:
        sections.append({"heading": cur_heading, "paragraphs": cur_paras})

    # Chỉ giữ paragraph có citation
    for sec in sections:
        sec["paragraphs"] = [p for p in sec["paragraphs"] if p.get("citations")]
    sections = [s for s in sections if s["paragraphs"]]

    return sections


def _detect_domain(title: str, sections: list[dict]) -> str:
    # Dùng title + heading + 2 paragraph đầu mỗi section để có đủ signal
    text = title.lower()
    for sec in sections[:5]:
        text += " " + sec["heading"].lower()
        for para in sec.get("paragraphs", [])[:2]:
            t = para["text"] if isinstance(para, dict) else para
            text += " " + t.lower()
    scores = {dk: 0 for dk in _DOMAIN_KEYWORDS}
    for dk, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[dk] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "law"


# ─────────────────────────────────────────────────────────────────────────────

def parse_article(pdf_path: str) -> dict:
    """
    Parse bài viết chính từ PDF.
    Trả về:
      title       — tiêu đề bài
      sections    — [{heading, paragraphs: [{text, citations}]}]
      claims_count — tổng số paragraph (= số claim)
      domain_key  — law / med / trv / ...
      domain_name — tên hiển thị domain
      headings    — danh sách tiêu đề heading
    """
    title    = _extract_article_title(pdf_path)
    sections = _extract_sections(pdf_path)
    domain_key = _detect_domain(title, sections)

    headings = [s["heading"] for s in sections]
    claims_count = sum(len(s["paragraphs"]) for s in sections)
    if claims_count == 0:
        claims_count = len(headings) if headings else 1

    return {
        "title":        title,
        "sections":     sections,
        "claims_count": claims_count,
        "domain_key":   domain_key,
        "domain_name":  DOMAIN_MAP.get(domain_key, domain_key),
        "headings":     headings,
        # full text cho legacy prompt builder
        "content":      "\n".join(
            s["heading"] + "\n" + "\n".join(p["text"] for p in s["paragraphs"])
            for s in sections
        ),
    }
