"""
prompt_builder.py — Build prompt gửi Claude.

Per-claim mode (mặc định):
  build_article_header_prompt() — gửi 1 lần: tiêu đề + domain + tổng claim
  build_claim_prompt()          — gửi N lần: 1 claim + URL của claim đó
  build_article_footer_prompt() — gửi 1 lần cuối: yêu cầu article-level JSON

Legacy (giữ lại, không dùng nữa):
  build_article_prompt()        — toàn bộ bài 1 lần
"""
import os
import sys

def _base_dir() -> str:
    """Root dir chứa rule files — hoạt động cả khi chạy từ exe (PyInstaller) và Python."""
    if getattr(sys, "frozen", False):
        # PyInstaller folder mode: datas nằm trong _internal/ (sys._MEIPASS)
        return sys._MEIPASS
    return os.path.join(os.path.dirname(__file__), "..")

RULE_MAP = {
    "law": "rule-luat.md",
    "med": "rule-yte.md",
    "trv": "rule-dulich.md",
}
DEFAULT_RULE = "rule-xin.md"


def load_rules(domain_key: str = "") -> str:
    filename = RULE_MAP.get(domain_key, DEFAULT_RULE)
    path = os.path.join(_base_dir(), filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_system_prompt(domain_key: str = "") -> str:
    return load_rules(domain_key)


_DOMAIN_EXTRA = {
    "med": """Lưu ý domain Y tế:
- Mỗi claim phải có thêm dòng RISK=CRITICAL|STANDARD|GENERAL trong notes (6 dòng: RISK= SF= SC_original= SC_final= HR= SQ= TXT=)
- Claim CRITICAL (tên thuốc/liều, vaccine, thời gian vàng, chỉ định nhập viện) → HR tối đa 0.74 nếu chỉ verify qua nguồn thứ cấp
- fact_check_status có thêm giá trị OUTDATED khi thông tin đúng nhưng đã cũ
- Nguồn ưu tiên: moh.gov.vn > bệnh viện công (Bạch Mai, Chợ Rẫy) > Vinmec/Tâm Anh > báo y tế""",
}


def get_cited_urls(article: dict, ref: dict) -> list[str]:
    """Trả về danh sách URL thực sự được cite trong bài, theo thứ tự index."""
    all_urls = ref.get("urls", [])
    cited_indices = set()
    for sec in article.get("sections", []):
        for para in sec.get("paragraphs", []):
            if isinstance(para, dict):
                for c in para.get("citations", []):
                    if 1 <= c <= len(all_urls):
                        cited_indices.add(c - 1)
    if cited_indices:
        return [all_urls[i] for i in sorted(cited_indices)]
    return all_urls[:15]


def build_article_prompt(article: dict, ref: dict,
                          domain_key: str = "", subdomain: str = "") -> str:
    """
    Build article prompt ngắn gọn.

    article: từ pdf_parser — {title, sections, domain_key, domain_name, ...}
    ref:     từ ref_parser — {urls, url_count}
    domain_key:  script detect (có thể rỗng nếu chưa detect)
    subdomain:   user chọn trong UI (gợi ý, Claude có thể sửa)
    """
    title       = article.get("title", "")
    sections    = article.get("sections", [])
    d_key       = article.get("domain_key") or domain_key or "?"
    d_name      = article.get("domain_name") or d_key
    all_urls    = ref.get("urls", [])
    urls        = get_cited_urls(article, ref)

    # ── Block domain gợi ý ───────────────────────────────────────────────────
    domain_hint = (
        f"Script tự detect domain: [{d_key}] {d_name}"
        + (f" | Sub-domain gợi ý: {subdomain}" if subdomain else "")
        + "\n→ Xác nhận hoặc sửa lại trong JSON output (domain_key, domain, sub_domain, sub_domain_id)"
    )

    # ── Block claims (script đã trích xuất) ─────────────────────────────────
    claim_lines = []
    claim_idx   = 0
    for sec in sections:
        claim_lines.append(f"\n## {sec['heading']}")
        for para in sec.get("paragraphs", []):
            claim_idx += 1
            text = para["text"] if isinstance(para, dict) else para
            cits = para.get("citations", []) if isinstance(para, dict) else []
            cite_str = f"  [cite: {', '.join(str(c) for c in cits)}]" if cits else ""
            snippet = text
            claim_lines.append(f"[Claim {claim_idx}]{cite_str} {snippet}")

    claims_block = "\n".join(claim_lines) if claim_lines else "(không trích xuất được claim)"
    total_claims = claim_idx

    # ── Block URL nguồn ──────────────────────────────────────────────────────
    url_status = ref.get("url_status", {})

    if urls:
        # Mỗi URL trên 1 dòng riêng biệt — Claude.ai nhận diện URL standalone để fetch
        url_lines_list = []
        for u in urls:
            status = url_status.get(u, "")
            if status and not status.startswith("OK"):
                url_lines_list.append(f"{u}  ← [{status}]")
            else:
                url_lines_list.append(u)
        url_lines = "\n".join(url_lines_list)
        url_section = f"""URL NGUỒN ({len(urls)} URL — hãy mở và đọc từng URL trước khi fact-check):

{url_lines}

Lưu ý:
- URL đánh dấu [KHÔNG TRUY CẬP] hoặc [HTTP_xxx] → không đọc được, cần tìm thêm nguồn thay thế
- Chỉ dùng URL từ danh sách trên cho fact_check_source_url — KHÔNG tự bịa URL khác
- Khi tính SQ: đánh giá nguồn tốt nhất tìm được (bao gồm nguồn tìm thêm qua search), ghi tên miền vào notes"""
    else:
        url_section = """URL NGUỒN: (không có)
Đặt fact_check_source_url = "" and fact_check_status = "KHONG TIM THAY" cho các claim không verify được."""

    domain_extra = _DOMAIN_EXTRA.get(d_key, "")
    domain_extra_block = f"\n---\n{domain_extra}" if domain_extra else ""

    return f"""TIÊU ĐỀ BÀI: {title}

{domain_hint}

---
DANH SÁCH CLAIM ĐÃ TRÍCH XUẤT ({total_claims} claim — dùng đúng danh sách này, KHÔNG trích xuất lại):
{claims_block}

---
{url_section}{domain_extra_block}

---
NHIỆM VỤ: Dựa trên nội dung đã đọc từ các URL, trả về JSON theo schema — {total_claims} claim, đúng thứ tự.
Không markdown. Không giải thích. Chỉ JSON thuần."""


# ─────────────────────────────────────────────────────────────────────────────
# Per-claim mode — 3 hàm dùng trong luồng mới
# ─────────────────────────────────────────────────────────────────────────────

def build_article_header_prompt(article: dict, ref: dict,
                                 domain_key: str = "") -> str:
    """
    Gửi 1 lần sau system prompt — giới thiệu bài, domain, tổng số claim.
    Claude ack rồi mới nhận từng claim.
    """
    title    = article.get("title", "")
    d_key    = article.get("domain_key") or domain_key or "?"
    d_name   = article.get("domain_name") or d_key
    sections = article.get("sections", [])
    total    = sum(len(s.get("paragraphs", [])) for s in sections)
    all_urls = ref.get("urls", [])
    n_urls   = len(all_urls)

    return f"""BÀI VIẾT CẦN ANNOTATION:
Tiêu đề : {title}
Domain  : [{d_key}] {d_name}
Tổng    : {total} claim, {n_urls} URL nguồn trong Ref PDF

Tôi sẽ gửi từng claim một. Với mỗi claim bạn phải:
1. Phân loại risk (CRITICAL / STANDARD / GENERAL)
2. Mở tất cả URL gắn kèm claim đó — đọc nội dung thực tế
3. Web search nếu CRITICAL (≥2 lần) hoặc STANDARD (≥1 lần)
4. Fact-check → chọn 1 trong 8 status
5. Chấm SF, SC, HR, SQ theo đúng rubric
6. Ghi notes đủ 6 dòng: RISK= SF= SC= HR= SQ= TXT=
7. Trả về JSON **chỉ 1 claim** theo schema (object, không phải array)

Schema JSON 1 claim:
{{
  "claim": "nội dung claim nguyên văn",
  "risk_level": "CRITICAL|STANDARD|GENERAL",
  "fact_check_status": "XAC NHAN|LECH|MAU THUAN|OUTDATED|KHONG TIM THAY|KHONG TIM THAY + ESCALATE|BO QUA|ERROR",
  "fact_check_source_url": "https://url1\\nhttps://url2 (URL RAG + web search nếu dùng; nhiều URL thì mỗi URL 1 dòng)",
  "source_fidelity": 0.00,
  "source_coverage": 0.00,
  "hallucination_rate": 0.00,
  "source_quality": 0.00,
  "notes": "RISK=...: ...\\nSF=...: ...\\nSC=...: ...\\nHR=...: ...\\nSQ=...: ...\\nTXT=...: ..."
}}

Quy tắc bắt buộc:
1. ĐỘC LẬP: Mỗi claim xử lý hoàn toàn độc lập — KHÔNG tái sử dụng notes, status, SF/SC/HR/SQ của claim trước.
2. SF & SC: chấm dựa trên URL RAG gắn kèm claim. Nếu RAG không chứa nội dung claim → SF thấp, SC thấp.
3. SQ: chấm theo tên miền URL RAG, không phải tên miền nguồn web search.
4. Web search: chỉ dùng cho FACT-CHECK status và HR. KHÔNG thay thế nguồn RAG khi chấm SF/SC/SQ.
5. fact_check_source_url: ghi TẤT CẢ URL thực sự dùng để verify, mỗi URL 1 dòng nếu có nhiều.
6. XAC NHAN: chỉ khi TẤT CẢ chi tiết và con số trong claim đều được xác nhận đầy đủ. Bất kỳ chi tiết nào lệch → LECH.

Không markdown. Không giải thích. Chỉ JSON thuần mỗi lần.
Xác nhận bạn đã hiểu quy trình."""


def build_claim_prompt(claim_idx: int, total_claims: int,
                       para: dict, all_urls: list[str],
                       url_status: dict) -> str:
    """
    Gửi 1 claim — bao gồm text đầy đủ + tất cả URL của claim đó.

    claim_idx : 1-based index
    para      : {"text": str, "citations": [int, ...]}  (citations là 1-based)
    all_urls  : toàn bộ URL từ ref_parser
    url_status: {url: "OK (200)" | "HTTP_404" | ...}
    """
    text = para.get("text", "")
    cits = para.get("citations", [])  # 1-based indices vào all_urls

    # Lấy URL thực của claim này
    claim_urls = []
    for c in cits:
        if 1 <= c <= len(all_urls):
            url = all_urls[c - 1]
            status = url_status.get(url, "")
            if status and not status.startswith("OK"):
                claim_urls.append(f"{url}  ← [{status}]")
            else:
                claim_urls.append(url)

    # Đếm URL lỗi để quyết định có cần nhắc search không
    n_bad = sum(1 for u in claim_urls if "←" in u)
    all_bad = len(claim_urls) > 0 and n_bad == len(claim_urls)

    if claim_urls:
        url_block = "URL RAG của claim này:\n" + "\n".join(claim_urls)
        url_block += "\n→ SF, SC, SQ phải phản ánh URL RAG trên (không phải URL web search)."
        if all_bad:
            url_block += "\n\n⚠️ Tất cả URL RAG không truy cập được — BẮT BUỘC web search để verify. SF=0.05, SC=0.05 vì RAG không hỗ trợ; SQ vẫn chấm theo tên miền URL RAG."
        else:
            url_block += "\nNếu URL RAG không chứa nội dung claim → SF thấp, SC thấp; dùng web search để verify fact-check và chấm HR."
    else:
        url_block = "URL RAG: (không có) — BẮT BUỘC web search để tìm nguồn verify claim. SF=0.05, SC=0.05."

    return f"""⚠️ CLAIM ĐỘC LẬP {claim_idx}/{total_claims} — KHÔNG tái sử dụng kết quả claim trước.
{text}

{url_block}

Bắt buộc: mở URL + web search (nếu CRITICAL/STANDARD) TRƯỚC khi trả JSON. Chỉ JSON."""


def build_article_footer_prompt(article: dict, claims_summary: list[dict]) -> str:
    """
    Gửi sau khi tất cả claim xong — yêu cầu Claude trả article-level JSON
    (domain, sub_domain, rel, comp, single_source_bias).

    claims_summary: list status của từng claim để Claude có context tổng thể.
    """
    title  = article.get("title", "")
    d_key  = article.get("domain_key", "?")
    d_name = article.get("domain_name", d_key)

    status_lines = "\n".join(
        f"  Claim {i+1}: {c.get('fact_check_status','?')} | risk={c.get('risk_level','?')}"
        for i, c in enumerate(claims_summary)
    )

    return f"""Tất cả {len(claims_summary)} claim đã annotation xong.

Tóm tắt kết quả:
{status_lines}

Bây giờ trả về JSON cấp bài (article-level). Không có claims — chỉ phần article:
{{
  "title": "{title}",
  "domain_key": "{d_key}",
  "domain": "{d_name}",
  "sub_domain": "Tên sub-domain",
  "sub_domain_id": "med_01",
  "single_source_bias": false,
  "rel": 0.00,
  "rel_band": "Good",
  "rel_reason": "2-3 câu lý do",
  "comp": 0.00,
  "comp_band": "Good",
  "comp_reason": "2-3 câu lý do"
}}

Không markdown. Chỉ JSON thuần."""
