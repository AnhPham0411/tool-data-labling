"""
prompt_builder.py — Build prompt gửi Claude.

Prompt ngắn gọn (~2000-3000 ký tự):
- Tiêu đề + domain gợi ý (script detect, Claude xác nhận/sửa)
- Danh sách claim đã trích xuất (script làm, Claude không cần extract lại)
- Danh sách URL đã check status
- Yêu cầu JSON output
"""
import os

RULE_MAP = {
    "law": "rule-luat.md",
    "med": "rule-yte.md",
    "trv": "rule-dulich.md",
}
DEFAULT_RULE = "rule-xin.md"


def load_rules(domain_key: str = "") -> str:
    filename = RULE_MAP.get(domain_key, DEFAULT_RULE)
    path = os.path.join(os.path.dirname(__file__), "..", filename)
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

    # Lấy citation numbers từ tất cả paragraph để lọc URL liên quan
    cited_indices = set()
    for sec in sections:
        for para in sec.get("paragraphs", []):
            if isinstance(para, dict):
                for c in para.get("citations", []):
                    if 1 <= c <= len(all_urls):
                        cited_indices.add(c - 1)  # 0-based

    # Lấy tất cả URL được cite — không cap cứng
    # Nếu không có citation → gửi tối đa 15 URL đầu
    if cited_indices:
        urls = [all_urls[i] for i in sorted(cited_indices)]
    else:
        urls = all_urls[:15]

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
            # Giới hạn mỗi claim 400 ký tự để prompt không phình to
            snippet = text[:400] + ("..." if len(text) > 400 else "")
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
