import time
import os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CLAUDE_URL = "https://claude.ai/new"
DEBUG_PORT = 9222  # Chrome phải chạy với --remote-debugging-port=9222

# Các selector Claude dùng cho assistant message
_ASSISTANT_SELECTORS = [
    '[data-testid="assistant-message"]',
    '[data-message-author-role="assistant"]',
    '.font-claude-message',
]

# JS lấy text response cuối của Claude
_JS_GET_LAST_RESPONSE = """
() => {
    // 1. Thử lấy từ block code JSON cuối cùng
    const jsonCodes = Array.from(document.querySelectorAll(
        'code.language-json, [aria-label="json code"] code'
    ));
    if (jsonCodes.length > 0) {
        return jsonCodes[jsonCodes.length - 1].textContent;
    }

    // 2. data-is-streaming container (selector thực tế Claude.ai)
    const streamingEl = document.querySelector('[data-is-streaming]');
    if (streamingEl) {
        return (streamingEl.innerText || streamingEl.textContent || '').trim();
    }

    // 3. Fallback các selector cũ
    const assistantMsgs = Array.from(document.querySelectorAll(
        '[data-message-author-role="assistant"], .font-claude-message'
    ));
    if (assistantMsgs.length > 0) {
        const lastMsg = assistantMsgs[assistantMsgs.length - 1];
        const prose = lastMsg.querySelector('.prose');
        return prose ? prose.innerText : lastMsg.innerText;
    }

    return "";
}
"""

# JS lấy bất kỳ text nào từ Claude — dùng khi chờ ack system prompt
_JS_GET_ANY_RESPONSE = """
() => {
    // data-is-streaming container
    const streamingEl = document.querySelector('[data-is-streaming]');
    if (streamingEl) {
        const text = (streamingEl.innerText || streamingEl.textContent || '').trim();
        if (text.length > 5) return text;
    }

    // Fallback các selector cũ
    const selectors = [
        '[data-message-author-role="assistant"]',
        '.font-claude-message',
        '[data-testid="assistant-message"]',
        '.prose',
    ];
    for (const sel of selectors) {
        const els = Array.from(document.querySelectorAll(sel));
        if (els.length > 0) {
            const last = els[els.length - 1];
            const text = last.innerText || last.textContent || '';
            if (text.trim().length > 5) return text.trim();
        }
    }
    return "";
}
"""

# JS detect lỗi network / error toast của Claude UI
_JS_GET_ERROR = """
() => {
    const selectors = [
        '[data-testid="error-message"]',
        '.error-message',
        '[role="alert"]',
        '[class*="error"]',
        '[class*="Error"]',
    ];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            const t = (el.innerText || el.textContent || '').trim();
            if (t.length > 5) return t;
        }
    }
    return "";
}
"""

# JS kiểm tra Claude đang stream hay đã xong
# Trả về true nếu CÒN đang generate (nút Stop hiện hoặc spinner hiện)
_JS_IS_GENERATING = """
() => {
    // Nút stop/interrupt thường có aria-label "Stop" hoặc data-testid chứa "stop"
    const stopBtn = document.querySelector(
        'button[aria-label="Stop"], button[data-testid*="stop"], button[data-value="stop"]'
    );
    if (stopBtn) return true;

    // Spinner hoặc loading indicator
    const spinner = document.querySelector(
        '[data-testid="streaming-indicator"], .loading-spinner, [aria-label*="loading"]'
    );
    if (spinner) return true;

    // Kiểm tra class animate trên assistant message cuối (cursor nhấp nháy)
    const msgs = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
    if (msgs.length > 0) {
        const last = msgs[msgs.length - 1];
        if (last.querySelector('.animate-pulse, .cursor-blink')) return true;
    }

    return false;
}
"""


def _count_streaming_els(page) -> int:
    """Đếm số element data-is-streaming hiện có — dùng để detect response mới."""
    try:
        return page.evaluate("() => document.querySelectorAll('[data-is-streaming]').length")
    except Exception:
        return 0


def _wait_response(page, timeout: int = 300, log_fn=print,
                   ack_mode: bool = False, prev_count: int = 0) -> str:
    """
    Chờ Claude xong bằng wait_for_function (event-driven, ~100ms poll của Playwright).

    prev_count: số data-is-streaming elements trước khi gửi message —
                dùng để tránh lấy response cũ ngay lập tức.
    """
    label   = "ack system prompt" if ack_mode else "article response"
    min_len = 5 if ack_mode else 50
    get_js  = _JS_GET_ANY_RESPONSE if ack_mode else _JS_GET_LAST_RESPONSE
    log_fn(f"  Chờ Claude {label} (timeout {timeout}s)...")

    _JS_WAIT_DONE = f"""
    () => {{
        // 1. Error toast
        for (const sel of ['[role="alert"]', '[class*="ErrorMessage"]',
                            '[data-testid="error-message"]', '.error-message']) {{
            for (const el of document.querySelectorAll(sel)) {{
                const t = (el.innerText || el.textContent || '').trim();
                if (t.length > 5) return 'ERR:' + t.slice(0, 200);
            }}
        }}
        // 2. Phải có nhiều hơn {prev_count} element — tức là response mới đã xuất hiện
        const allEls = document.querySelectorAll('[data-is-streaming]');
        if (allEls.length <= {prev_count}) return false;
        // 3. Element mới nhất phải đã xong streaming
        const last = allEls[allEls.length - 1];
        if (last.getAttribute('data-is-streaming') === 'true') return false;
        // 4. Đủ text
        const text = (last.innerText || last.textContent || '').trim();
        return text.length >= {min_len};
    }}
    """

    t0 = time.time()
    try:
        result = page.wait_for_function(
            _JS_WAIT_DONE,
            timeout=timeout * 1000,  # Playwright dùng ms
        )
        val = result.json_value() if result else None
        elapsed = int(time.time() - t0)

        if isinstance(val, str) and val.startswith("ERR:"):
            err_msg = val[4:]
            if any(kw in err_msg.lower() for kw in
                   ["couldn't connect", "cannot connect", "network"]):
                raise ConnectionError(f"Claude UI báo lỗi mạng: {err_msg}")
            log_fn(f"  ⚠ Claude UI có thông báo: {err_msg[:100]}")

        # Lấy text thực sự
        text = page.evaluate(get_js) or ""
        text = text.strip()
        log_fn(f"  Claude xong sau ~{elapsed}s — {len(text)} ký tự")
        return text

    except PWTimeout:
        log_fn(f"  ⚠ Timeout {timeout}s — lấy text hiện tại")
        try:
            page.screenshot(path=os.path.abspath("debug_screenshot.png"), full_page=True)
            # Dump data-* attrs để debug selector
            debug_info = page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('*').forEach(el => {
                    const attrs = Array.from(el.attributes).filter(a => a.name.startsWith('data-'));
                    if (attrs.length > 0) {
                        const text = (el.innerText || '').trim();
                        if (text.length > 30)
                            out.push(attrs.map(a => a.name+'='+a.value.slice(0,40)).join(' ') + ' | len='+text.length);
                    }
                });
                return [...new Set(out)].slice(0, 30).join('\\n');
            }""")
            with open(os.path.abspath("debug_dom_attrs.txt"), "w", encoding="utf-8") as f:
                f.write(debug_info or "(empty)")
            log_fn("  Debug DOM saved: debug_screenshot.png, debug_dom_attrs.txt")
        except Exception:
            pass
        return page.evaluate(get_js) or ""
    except ConnectionError:
        raise
    except Exception as e:
        log_fn(f"  ⚠ wait_for_function lỗi: {e} — fallback lấy text hiện tại")
        return page.evaluate(get_js) or ""



def _get_input_box(page):
    """Lấy input box Claude — thử nhiều selector."""
    for selector in [
        '[contenteditable="true"][data-placeholder]',
        'div[contenteditable="true"]',
        'textarea',
    ]:
        el = page.locator(selector).first
        if el.count() > 0:
            return el
    raise RuntimeError("Không tìm thấy input box của Claude")


def _click_send(page, log_fn=print):
    """Đảm bảo bấm được nút Send (kể cả khi text dài bị biến thành file)."""
    time.sleep(0.5)
    
    try:
        # Dùng native click của Playwright với force=True để bỏ qua check animation/stable
        page.click('button[aria-label="Send message"]', timeout=2000, force=True)
        log_fn("  Đã click nút Send (native Playwright).")
    except Exception as e:
        log_fn(f"  Không tìm thấy nút Send qua Playwright: {e}")
        # Fallback 1: Dùng Enter
        log_fn("  Thử gửi bằng phím Enter...")
        page.keyboard.press("Enter")
        # Log debug nếu cần thiết
        try:
            page.screenshot(path=os.path.abspath("debug_screenshot_send.png"), full_page=True)
            with open("debug_dom_send.html", "w", encoding="utf-8") as f:
                f.write(page.evaluate("() => document.body.innerHTML"))
        except Exception:
            pass



def _send_text(page, text: str, log_fn=print):
    """
    Gửi text vào input box Claude qua clipboard (Ctrl+V).
    """
    inp = _get_input_box(page)
    inp.click()
    time.sleep(0.3)

    # Xóa nội dung cũ
    page.keyboard.press("Control+a")
    time.sleep(0.1)
    page.keyboard.press("Backspace")
    time.sleep(0.1)

    # Set clipboard rồi paste
    _set_clipboard(text)
    time.sleep(0.5)
    page.keyboard.press("Control+v")
    time.sleep(1.5)
    page.keyboard.press("Space")
    time.sleep(0.3)

    log_fn(f"  Đã paste {len(text)} chars")


def _set_clipboard(text: str):
    """
    Đưa text vào clipboard Windows.
    Dùng PowerShell ẩn window (-WindowStyle Hidden) để không hiện CMD.
    """
    import subprocess, tempfile, os
    tmp_path = tempfile.mktemp(suffix=".txt")
    with open(tmp_path, "w", encoding="utf-8-sig") as f:
        f.write(text)
    try:
        cmd = f"[System.IO.File]::ReadAllText('{tmp_path}', [System.Text.Encoding]::UTF8) | Set-Clipboard"
        subprocess.run(
            ["powershell", "-WindowStyle", "Hidden", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            timeout=15,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass




def _send_urls_for_fetch(page, urls: list[str], log_fn=print):
    """
    Gửi từng URL riêng lẻ vào Claude để trigger web fetch tự động.
    Claude.ai chỉ fetch URL khi URL đứng một mình trong message,
    không fetch được khi URL nằm trong đoạn text dài.
    """
    if not urls:
        return
    log_fn(f"  Gửi {len(urls)} URL để Claude fetch...")
    # Gửi tất cả URL trong 1 message, mỗi URL 1 dòng — Claude sẽ fetch lần lượt
    url_msg = "\n".join(urls)
    _set_clipboard(url_msg)
    inp = _get_input_box(page)
    inp.click()
    time.sleep(0.2)
    page.keyboard.press("Control+a")
    time.sleep(0.1)
    page.keyboard.press("Backspace")
    time.sleep(0.1)
    page.keyboard.press("Control+v")
    time.sleep(2.0)  # Đợi Claude.ai parse và hiện URL preview card
    page.keyboard.press("Space")
    time.sleep(0.5)
    _click_send(page, log_fn)
    # Đợi Claude fetch xong — mỗi URL ~10-20s, dùng ack_mode
    fetch_timeout = max(60, len(urls) * 20)
    log_fn(f"  Đợi Claude fetch URLs (timeout {fetch_timeout}s)...")
    r = _wait_response(page, timeout=fetch_timeout, log_fn=log_fn, ack_mode=True)
    log_fn(f"  Fetch xong: {r[:120] if r else '(không lấy được text ack)'}")


def _wait_idle(page, log_fn=print, timeout: int = 10):
    """Poll cho đến khi Claude không còn generating. Tối đa timeout giây."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not page.evaluate(_JS_IS_GENERATING):
                return
        except Exception:
            return
        time.sleep(1)


def run_annotation(system_prompt: str, article_prompt: str,
                   urls: list[str] | None = None, log_fn=print) -> str:
    """
    Connect vào Chrome thật qua CDP (port 9222).
    Chrome phải đã mở claude.ai và đã login.

    urls: không dùng nữa — URL đã nằm trong article_prompt, Claude tự fetch.
    """
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
        except Exception as e:
            raise RuntimeError(
                f"Không connect được Chrome (port {DEBUG_PORT}).\n"
                f"Hãy chạy: python login_claude.py\n"
                f"Lỗi: {e}"
            )

        log_fn("Đã connect Chrome thật.")

        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("Chrome không có tab nào mở")

        ctx = contexts[0]
        pages = ctx.pages

        claude_page = None
        for pg in pages:
            if "claude.ai" in pg.url:
                claude_page = pg
                break

        if claude_page is None:
            log_fn("Mở tab Claude mới...")
            claude_page = ctx.new_page()
        else:
            log_fn(f"Dùng tab Claude: {claude_page.url}")

        claude_page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=60000)
        # Chờ input box thực sự render (React hydrate xong) thay vì sleep cứng
        try:
            claude_page.wait_for_selector(
                '[contenteditable="true"], textarea',
                timeout=15000,
            )
            log_fn("  Input box sẵn sàng.")
        except PWTimeout:
            log_fn("  ⚠ Input box chưa thấy sau 15s — tiếp tục thử.")

        if "login" in claude_page.url or "auth" in claude_page.url:
            raise RuntimeError("Claude chưa login — hãy login trong Chrome rồi chạy lại")

        # === STEP 1: System prompt ===
        log_fn("Gửi system prompt (rule.md)...")
        c0 = _count_streaming_els(claude_page)
        _send_text(claude_page, system_prompt, log_fn)
        _click_send(claude_page, log_fn)

        r1 = _wait_response(claude_page, timeout=60, log_fn=log_fn, ack_mode=True, prev_count=c0)
        log_fn(f"Claude confirm: {r1[:100] if r1 else '(không lấy được text — tiếp tục)'}")

        _wait_idle(claude_page, log_fn)

        # === STEP 2: Article prompt ===
        log_fn("Gửi dữ liệu bài viết...")
        c1 = _count_streaming_els(claude_page)
        _send_text(claude_page, article_prompt, log_fn)
        _click_send(claude_page, log_fn)

        log_fn("Chờ Claude xử lý + trả JSON...")
        response = _wait_response(claude_page, timeout=600, log_fn=log_fn, ack_mode=False, prev_count=c1)

        return response


def run_annotation_with_retry(
    system_prompt: str,
    article_prompt: str,
    urls: list[str] | None = None,
    log_fn=print,
    max_retries: int = 3,
) -> str:
    """Wrapper retry — legacy mode (toàn bài 1 lần)."""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            return run_annotation(system_prompt, article_prompt, urls=urls, log_fn=log_fn)
        except RuntimeError:
            raise  # lỗi setup — không retry
        except Exception as e:
            last_err = e
            log_fn(f"Lần {attempt}/{max_retries} thất bại: {e}")
            if attempt < max_retries:
                wait = 15 if isinstance(e, ConnectionError) else 5
                log_fn(f"Retry sau {wait}s...")
                time.sleep(wait)
    raise RuntimeError(f"Thất bại sau {max_retries} lần. Lỗi cuối: {last_err}")


def _connect_claude_page(log_fn=print):
    """
    Connect CDP, tìm/tạo tab Claude, navigate đến /new.
    Trả về (playwright_instance, browser, page) — caller chịu trách nhiệm đóng.
    """
    import playwright.sync_api as pw_api
    p = pw_api.sync_playwright().start()
    try:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
    except Exception as e:
        p.stop()
        raise RuntimeError(
            f"Không connect được Chrome (port {DEBUG_PORT}).\n"
            f"Hãy chạy: python login_claude.py\nLỗi: {e}"
        )

    contexts = browser.contexts
    if not contexts:
        p.stop()
        raise RuntimeError("Chrome không có tab nào mở")

    ctx = contexts[0]
    claude_page = next((pg for pg in ctx.pages if "claude.ai" in pg.url), None)

    if claude_page is None:
        log_fn("Mở tab Claude mới...")
        claude_page = ctx.new_page()
    else:
        log_fn(f"Dùng tab Claude: {claude_page.url}")

    claude_page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        claude_page.wait_for_selector('[contenteditable="true"], textarea', timeout=15000)
        log_fn("  Input box sẵn sàng.")
    except PWTimeout:
        log_fn("  ⚠ Input box chưa thấy sau 15s — tiếp tục thử.")

    if "login" in claude_page.url or "auth" in claude_page.url:
        p.stop()
        raise RuntimeError("Claude chưa login — hãy login trong Chrome rồi chạy lại")

    return p, browser, claude_page


def _send_and_wait(page, text: str, timeout: int, ack_mode: bool,
                   log_fn=print, max_retries: int = 2) -> str:
    """
    Gửi 1 message và chờ response. Retry nếu response rỗng.
    Raise nếu hết retry.
    """
    for attempt in range(1, max_retries + 1):
        c = _count_streaming_els(page)
        _send_text(page, text, log_fn)
        _click_send(page, log_fn)
        resp = _wait_response(page, timeout=timeout, log_fn=log_fn,
                              ack_mode=ack_mode, prev_count=c)
        if resp and resp.strip():
            return resp
        log_fn(f"  ⚠ Response rỗng (attempt {attempt}/{max_retries})")
        _wait_idle(page, log_fn)
    raise RuntimeError("Response rỗng sau tất cả retry")


def run_annotation_per_claim(
    system_prompt: str,
    header_prompt: str,
    claim_prompts: list[str],
    footer_prompt_fn,           # callable() → str, gọi sau khi tất cả claim xong
    log_fn=print,
    on_claim_done=None,
    claim_timeout: int = 120,
    claim_max_retries: int = 2,
) -> tuple[list[str], str]:
    """
    Per-claim annotation trong 1 conversation:
      1. Mở session, gửi system_prompt → ack
      2. Gửi header_prompt → ack
      3. Với mỗi claim_prompts[i]:
           - Gửi → chờ response (timeout claim_timeout)
           - Retry tối đa claim_max_retries nếu rỗng
           - Nếu vẫn fail → ghi chuỗi lỗi, tiếp tục claim kế
           - Gọi on_claim_done(i, raw_response_or_error_str)
      4. Gọi footer_prompt_fn() → build footer với claims đã xong
         Gửi footer → lấy article-level JSON
      5. Trả về (claim_raws, article_raw)

    on_claim_done(idx: int, raw: str) — callback realtime, có thể None.
    raw là JSON string hoặc chuỗi 'TOOL_ERROR:...' nếu fail.
    footer_prompt_fn: callable không tham số, trả str — gọi sau tất cả claim.
    """
    p, browser, page = _connect_claude_page(log_fn)
    try:
        # STEP 1: System prompt
        log_fn("Gửi system prompt...")
        _send_and_wait(page, system_prompt, timeout=60, ack_mode=True, log_fn=log_fn)

        # STEP 2: Header — giới thiệu bài + quy trình
        log_fn("Gửi header bài viết...")
        _wait_idle(page, log_fn)
        _send_and_wait(page, header_prompt, timeout=60, ack_mode=True, log_fn=log_fn)

        # STEP 3: Từng claim
        claim_raws = []
        total = len(claim_prompts)
        for i, cp in enumerate(claim_prompts):
            log_fn(f"\n  [Claim {i+1}/{total}] Gửi...")
            _wait_idle(page, log_fn)
            raw = ""
            try:
                raw = _send_and_wait(
                    page, cp,
                    timeout=claim_timeout,
                    ack_mode=False,
                    log_fn=log_fn,
                    max_retries=claim_max_retries,
                )
                log_fn(f"  [Claim {i+1}/{total}] Nhận {len(raw)} ký tự")
            except Exception as e:
                raw = f"TOOL_ERROR: {e}"
                log_fn(f"  [Claim {i+1}/{total}] ⚠ Lỗi: {e} — tiếp tục claim kế")

            claim_raws.append(raw)
            if on_claim_done:
                on_claim_done(i, raw)

        # STEP 4: Footer — build sau khi tất cả claim xong rồi gửi
        article_raw = ""
        try:
            footer_prompt = footer_prompt_fn() if callable(footer_prompt_fn) else ""
        except Exception as e:
            footer_prompt = ""
            log_fn(f"  ⚠ Build footer lỗi: {e}")

        if footer_prompt and footer_prompt.strip():
            log_fn("\n  Gửi footer (article-level)...")
            _wait_idle(page, log_fn)
            try:
                article_raw = _send_and_wait(
                    page, footer_prompt,
                    timeout=120, ack_mode=False, log_fn=log_fn,
                )
            except Exception as e:
                article_raw = f"TOOL_ERROR: {e}"
                log_fn(f"  ⚠ Footer lỗi: {e}")

        return claim_raws, article_raw

    finally:
        p.stop()
