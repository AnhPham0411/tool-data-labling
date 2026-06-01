# Quy trình kỹ thuật — Vivipedia Annotation Tool

Tài liệu mô tả chính xác từng bước pipeline thực hiện. Dùng để đối chiếu khi debug lỗi.

---

## 1. Chuẩn bị

Chạy `python login_claude.py` để mở Chrome với remote debugging (port 9222).  
Đăng nhập vào Claude.ai một lần. **Không đóng Chrome** trong suốt phiên làm việc.

---

## 2. Pipeline xử lý (sau khi bấm RUN)

### Bước 1 — Validate đầu vào

Tool kiểm tra theo thứ tự trước khi bắt đầu:

1. **Annotator ID** — bắt buộc, khuyến nghị `ANT-xx`
2. **File bài viết** — tồn tại, đúng PDF magic bytes, không mã hóa, không rỗng
3. **File Ref PDF** — tùy chọn, nếu thiếu tool hỏi xác nhận
4. **Không chọn cùng 1 file** cho cả 2 drop zone
5. **Chrome CDP** — ping `localhost:9222` trước khi chạy

Khi kéo thả file bài viết, tool đọc trước trên thread riêng để hiện tiêu đề + số claims ngay (không block UI).

---

### Bước 2 — Parse PDF bài viết (`pdf_parser.py`)

**Detect heading size tự động (không hardcode):**
- Duyệt toàn bộ span → thu thập tần suất font size
- `body_size` = font size xuất hiện nhiều nhất
- `heading_size` = size phổ biến nhất trong các size ≥ body × 1.3

**Detect citation format:**
- Quét raw text toàn PDF
- `[src_vinmec_com_000]` → format **med** (0-based index)
- `[1]`, `[2,3]` → format **law** (1-based số thứ tự)
- Không tìm thấy → `none`

**Trích xuất section:**
- Bỏ qua nội dung trước heading "Tóm tắt nhanh" / "Tóm tắt"
- Nhóm paragraph theo heading
- Gom block vào buffer, flush khi bracket `[...]` đóng đủ
- Dừng khi gặp footer "Vivipedia" hoặc heading kết thúc ("Nguồn tham khảo", "SEO")

**Flush paragraph:**
- **Med**: extract số cuối mỗi `src_xxx_NNN` (+1 → 1-based), strip `[src_...]` khỏi text
- **Law**: extract số nguyên, strip citation ở cuối đoạn

**Lọc claim:** Chỉ giữ paragraph có ít nhất 1 citation — đây là claim.

**Detect domain:** Keyword scoring từ title + 5 heading đầu + 2 paragraph đầu mỗi section → 13 domain.

**Kết quả:** `{title, sections, claims_count, domain_key, domain_name, headings, content}`

---

### Bước 3 — Parse Ref PDF (`ref_parser.py`)

- Dùng `page.get_links()` của PyMuPDF — lấy hyperlink annotation thật
- Bỏ domain nội bộ/mạng xã hội: `vivipedia.vn`, `facebook.com`, `youtube.com`, v.v.
- Dedup, giữ thứ tự xuất hiện, strip dấu câu trailing
- Check HTTP status song song (6 workers, timeout 3s/URL)

**Kết quả:** `{urls, url_count, url_status: {url: "OK (200)" | "HTTP_4xx" | "KHÔNG TRUY CẬP"}}`

> Không dùng regex hay pdfplumber — tránh bắt nhầm URL inline trong text.

---

### Bước 4 — Build Prompt (`prompt_builder.py`)

**Per-claim mode** — 3 loại prompt:

**System prompt** — toàn bộ `rule-{domain}.md` (rubric, schema, hướng dẫn fact-check).

**Header prompt** (gửi 1 lần sau system):
- Tiêu đề bài + domain + tổng số claim
- Schema JSON 1 claim + quy trình 7 bước
- Yêu cầu Claude ack trước khi nhận claim

**Claim prompt** (gửi N lần, 1 claim/lần):
- `[Claim i/N]` + text đầy đủ (không cắt)
- URL nguồn **chỉ của claim đó** (lọc từ citations, có đánh dấu URL lỗi)

**Footer prompt** (gửi 1 lần cuối):
- Summary tất cả claim đã xong (status + risk_level)
- Yêu cầu trả article-level JSON: domain, sub_domain, rel, comp, single_source_bias

**`get_cited_urls(article, ref)`** — lấy URL thực sự được cite (không lấy toàn bộ ref), fallback 15 URL đầu nếu không có citation.

---

### Bước 5 — Gửi Claude qua Chrome (`claude_automation.py`)

**Kết nối:**
- Playwright CDP connect vào Chrome ở `localhost:9222`
- Tìm tab `claude.ai` đang mở hoặc tạo tab mới
- Navigate đến `claude.ai/new` → `wait_for_selector('[contenteditable]')` thay vì sleep cứng

**Gửi text qua clipboard:**
- Ghi file tạm UTF-8 BOM → PowerShell đọc → `Set-Clipboard`
- Paste `Ctrl+V` → Claude.ai nhận diện URL và trigger web fetch
- Clipboard thay vì `insert_text()` vì chỉ clipboard mới trigger URL detection

**Phát hiện Claude xong (event-driven):**
- `page.wait_for_function()` với JS condition poll ~100ms
- Condition: không còn Stop button **VÀ** element `[data-is-streaming]` mới xuất hiện (so với `prev_count`) với `data-is-streaming="false"` **VÀ** đủ text
- `prev_count` đếm trước mỗi lần gửi để tránh lấy response cũ
- Detect error toast → raise `ConnectionError` sớm, không chờ timeout

**Luồng per-claim trong 1 conversation:**
```
Gửi system_prompt → ack (timeout 60s)
Gửi header_prompt → ack (timeout 60s)
Lặp i = 1..N:
    Gửi claim_prompt[i] → chờ JSON 1 claim (timeout 180s)
    Nếu lỗi/rỗng → retry 2 lần → ghi placeholder, tiếp tục
    Callback on_claim_done(i, raw) → parse + update progress bar
Gọi footer_prompt_fn() để build footer (có context cc)
Gửi footer_prompt → chờ article JSON (timeout 120s)
```

---

### Bước 6 — Parse JSON per-claim (`response_parser.py`)

**`extract_single_claim_json(raw)`** — parse 1 claim:
- Dùng lại 4 strategy của `extract_json` (direct → fence → brace scan → regex)
- Unwrap nếu Claude wrap trong `{"claims": [...]}` hoặc `{"claim_data": ...}`
- Validate đủ 9 field bắt buộc

**`normalize_claim(claim)`:**
- `fact_check_status` → chuẩn hóa (underscore → space, typo variants)
- SF/SC/HR/SQ → ép kiểu float

**`make_error_claim(idx, para, reason)`:**
- Placeholder khi claim fail sau retry
- `fact_check_status = "ERROR"`, notes ghi `TOOL_ERROR: {reason}`
- Pipeline tiếp tục, claim khác không bị ảnh hưởng

**`extract_article_json(raw)`:**
- Parse footer response → article-level fields
- Unwrap `{"article": {...}}` nếu cần
- Validate field: domain_key, sub_domain, rel, comp

**Validate claim (cảnh báo, không dừng):**
- `fact_check_status` phải là 1 trong 8 giá trị hợp lệ
- SF, SC, HR, SQ ∈ [0.0, 1.0]
- `fact_check_source_url` bắt đầu bằng `http` nếu có

---

### Bước 7 — Ghi Excel (`excel_writer.py`)

**Merge dữ liệu:**
- Flatten paragraph từ tất cả section → danh sách claim theo thứ tự
- Map 1-1 với `cc` (claim list từ Claude)
- Ghép metadata: title, domain, subdomain, annotator ID, ngày

**Cấu trúc Excel:**

| Dòng | Nội dung | Màu |
|------|----------|-----|
| 1 | Title bar merge A1:O1 | Navy `FF1F3864` |
| 2 | Nhãn nhóm cột (IDENTITY / FACT-CHECK / METRICS / ANNOTATION INFO) | Theo nhóm |
| 3 | Tên từng cột | Theo nhóm |
| 4 | Dòng template hướng dẫn | Vàng nhạt |
| 5+ | Dữ liệu claim | Trắng xanh nhạt |

Freeze pane tại `C5`. Dòng cần review (status ERROR hoặc metric bất thường) tô vàng cam.

**Append mode:** Load workbook cũ → append từ dòng tiếp theo. Nếu file đang khóa (Excel đang mở) → lưu backup với timestamp.

---

## 3. Các điểm hay bị lỗi

| Vấn đề | Nguyên nhân | Dấu hiệu | Cách xử lý |
|--------|-------------|----------|------------|
| 0 claims | PDF scan ảnh, không có text layer | Log: "Không trích xuất được claim" | Dùng PDF có text layer |
| Chrome không kết nối | Chưa chạy `login_claude.py` | Log: "port 9222" | Chạy lại `login_claude.py` |
| DOM text len = 0 sau paste | Input box chưa ready | Log: paste thành công nhưng Claude không nhận | Thường tự xử lý qua `wait_for_selector` |
| Claim placeholder ERROR | Claim timeout hoặc parse lỗi | Log: `⚠ Claim X: parse lỗi` | Các claim khác vẫn chạy, kiểm tra log |
| Footer parse lỗi | Claude trả sai format | Log: `⚠ Footer parse lỗi` | article-level dùng fallback từ pdf_parser |
| PermissionError Excel | File đang mở trong Excel | Log: "file đang mở" | Đóng Excel, chạy lại |
| `fact_check_status` không hợp lệ | Claude typo hoặc dùng variant | Cảnh báo trong log | Thêm vào `_STATUS_NORMALIZE` trong response_parser.py |
| `debug_screenshot.png` sinh ra | Timeout trong `_wait_response` | File xuất hiện ở root | Xem ảnh, kiểm tra Claude UI |
