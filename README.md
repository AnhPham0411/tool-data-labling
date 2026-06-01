# Vivipedia Annotation Tool

Tool tự động annotation dữ liệu RAG tiếng Việt cho Vivipedia. Nhận 2 file PDF đầu vào, điều khiển Claude.ai qua trình duyệt Chrome thật, trả về file Excel chuẩn với đầy đủ các trường annotation.

---

## Yêu cầu hệ thống

- Python 3.10+
- Google Chrome (bản desktop thường)
- Tài khoản Claude.ai (Pro hoặc Free)
- Windows 10/11

---

## Cài đặt

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Cách chạy

### Bước 1 — Mở Chrome với remote debugging (làm 1 lần mỗi phiên)

```bash
python login_claude.py
```

Cửa sổ Chrome bật lên. Đăng nhập vào [claude.ai](https://claude.ai) nếu chưa. **Không đóng cửa sổ này** trong suốt phiên làm việc.

### Bước 2 — Khởi động giao diện chính

```bash
python main.py
```

### Bước 3 — Sử dụng giao diện

1. Kéo thả (hoặc click chọn) **file PDF bài viết chính** vào ô drop zone bên trái  
   → Tool tự đọc tiêu đề, đếm claim, gợi ý domain ngay lập tức
2. Kéo thả **file Ref PDF** vào ô drop zone bên phải  
   → Tool trích xuất danh sách URL nguồn từ hyperlink
3. Điền **Annotator ID** (định dạng `ANT-xx`)
4. Kiểm tra **Domain** được detect tự động (có thể override)
5. Bấm **▶ RUN ANNOTATION**

Kết quả ghi vào `outputs/annotation_output.xlsx`. Windows Explorer tự mở đến file sau khi xong.

---

## Cấu trúc thư mục

```
tool-data-labling/
├── main.py                    # GUI chính (Tkinter)
├── login_claude.py            # Mở Chrome với remote debug port 9222
├── rule-yte.md                # System prompt domain Y tế & Sức khỏe
├── rule-luat.md               # System prompt domain Pháp luật
├── rule-dulich.md             # System prompt domain Du lịch
├── rule-xin.md                # System prompt domain mặc định
├── requirements.txt
├── workflow.md                # Tài liệu kỹ thuật pipeline
├── outputs/                   # File Excel kết quả (gitignored)
└── modules/
    ├── pdf_parser.py          # Trích xuất tiêu đề, section, claim, citation
    ├── ref_parser.py          # Trích xuất URL từ Ref PDF (PyMuPDF hyperlink)
    ├── prompt_builder.py      # Build prompt per-claim gửi Claude
    ├── claude_automation.py   # Điều khiển Chrome qua Playwright CDP
    ├── response_parser.py     # Parse + normalize JSON từ Claude response
    └── excel_writer.py        # Ghi Excel v10 (4 dòng header, freeze C5)
```

---

## Đầu vào / Đầu ra

| | Chi tiết |
|---|---|
| **File bài viết** | PDF có text layer, có citation `[1]`, `[2]`... hoặc `[src_xxx_000]` |
| **File Ref PDF** | PDF chứa hyperlink annotation đến URL nguồn |
| **Đầu ra** | `outputs/annotation_output.xlsx` — append mỗi lần chạy |
| **Format Excel** | 4 dòng header, freeze pane tại C5, 15 cột A–O |

### Cột Excel (A–O)

| Cột | Tên | Nguồn |
|-----|-----|-------|
| A | # (STT) | Tự tăng |
| B | Article Title | pdf_parser |
| C | Domain | Claude xác nhận |
| D | Sub-domain | Claude xác nhận |
| E | Sub-domain ID | Claude xác nhận |
| F | Claim | pdf_parser (paragraph có citation) |
| G | Fact-check Status | Claude |
| H | Fact-check Source URL | Claude |
| I | Source Fidelity (SF) | Claude |
| J | Source Coverage (SC) | Claude |
| K | Hallucination Rate (HR) | Claude |
| L | Source Quality (SQ) | Claude |
| M | Annotator Notes | Claude |
| N | Annotator ID | User nhập |
| O | Date | Tự động |

---

## Domain hỗ trợ

| Key | Tên | Rule file |
|-----|-----|-----------|
| med | Y tế & Sức khỏe | rule-yte.md |
| law | Pháp luật | rule-luat.md |
| trv | Du lịch | rule-dulich.md |
| fin | Tài chính & Kinh tế | rule-xin.md |
| gov | Chính trị & Hành chính | rule-xin.md |
| edu | Giáo dục | rule-xin.md |
| sci | Khoa học & Công nghệ | rule-xin.md |
| biz | Kinh doanh & Quản trị | rule-xin.md |
| cul | Văn hóa & Xã hội | rule-xin.md |
| his | Lịch sử & Địa lý | rule-xin.md |
| re  | Bất động sản & Xây dựng | rule-xin.md |
| env | Môi trường & Tài nguyên | rule-xin.md |
| ent | Thể thao & Giải trí | rule-xin.md |

---

## Các lỗi thường gặp

| Lỗi | Nguyên nhân | Cách fix |
|-----|-------------|----------|
| Không kết nối Chrome (port 9222) | Chrome chưa mở hoặc mở sai cách | Chạy lại `login_claude.py` |
| 0 claims | PDF scan ảnh, không có text layer | Dùng PDF có text layer |
| Claim lỗi tool (placeholder) | Claim đó timeout hoặc parse lỗi | Kiểm tra log từng claim, các claim khác vẫn chạy bình thường |
| PermissionError khi ghi Excel | File đang mở trong Excel | Đóng Excel trước khi chạy |
| `debug_screenshot.png` sinh ra | Claude timeout hoặc UI lỗi | Xem ảnh để biết trạng thái trang |

---

## Ghi chú kỹ thuật

- Tool dùng **Playwright CDP** connect vào Chrome thật qua port 9222 — không tự động mở Chrome mới
- Text gửi vào Claude qua **clipboard** (Ctrl+V) thay vì `insert_text()` vì chỉ clipboard mới trigger URL detection của Claude UI
- Phát hiện Claude xong bằng **`data-is-streaming`** attribute — event-driven, không polling sleep
- Mỗi claim xử lý **độc lập** trong cùng 1 conversation, claim lỗi không ảnh hưởng claim khác
