import json
import re

# Map normalize fact_check_status — underscore → space, typo fix
_STATUS_NORMALIZE = {
    "XAC_NHAN": "XAC NHAN",
    "MAU_THUAN": "MAU THUAN",
    "KHONG_TIM_THAY": "KHONG TIM THAY",
    "BO_QUA": "BO QUA",
    "XAC NHAN": "XAC NHAN",
    "LECH": "LECH",
    "MAU THUAN": "MAU THUAN",
    "OUTDATED": "OUTDATED",
    "KHONG TIM THAY": "KHONG TIM THAY",
    "BO QUA": "BO QUA",
    "ERROR": "ERROR",
    # Compound status với underscore
    "KHONG_TIM_THAY + ESCALATE": "KHONG TIM THAY + ESCALATE",
    "KHONG_TIM_THAY+ESCALATE": "KHONG TIM THAY + ESCALATE",
    "KHONG TIM THAY+ESCALATE": "KHONG TIM THAY + ESCALATE",
    "KHONG TIM THAY + ESCALATE": "KHONG TIM THAY + ESCALATE",
    # Claude typo variants
    "KHONG_XAC_NHAN": "KHONG TIM THAY",
    "KHONG XAC NHAN": "KHONG TIM THAY",
    "CHUA XAC NHAN": "KHONG TIM THAY",
    "CHUA_XAC_NHAN": "KHONG TIM THAY",
}


def extract_json(raw: str) -> dict:
    """
    Extract JSON từ Claude response — chịu được text thừa trước/sau.
    Strategy theo thứ tự ưu tiên:
      1. Parse trực tiếp
      2. Lấy từ ```json ... ``` fence
      3. Tìm { bắt đầu object đến } cuối cùng (balanced brace scan)
      4. Greedy regex fallback
    """
    raw = raw.strip()

    # Strategy 1: parse trực tiếp
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: code fence ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: balanced brace scan — tìm { đầu tiên rồi đếm brace đến khi balanced
    start = raw.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(raw[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[start:i+1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break  # nếu không parse được thì thử strategy 4

    # Strategy 4: greedy regex — lấy từ { đầu đến } cuối
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Không parse được JSON từ response Claude.\n"
        f"Raw ({len(raw)} chars, hiện 500 đầu):\n{raw[:500]}"
    )


def normalize_data(data: dict) -> dict:
    """
    Post-process data từ Claude:
    - Normalize fact_check_status (underscore → space)
    - Clean source URLs (remove hallucinated paths nếu cần)
    """
    for claim in data.get("claims", []):
        # Normalize status
        raw_status = str(claim.get("fact_check_status", "")).strip().upper()
        claim["fact_check_status"] = _STATUS_NORMALIZE.get(raw_status, raw_status)

        # Ensure numeric fields are float
        for field in ["source_fidelity", "source_coverage", "hallucination_rate", "source_quality"]:
            val = claim.get(field, 0)
            try:
                claim[field] = float(val)
            except (TypeError, ValueError):
                claim[field] = 0.0

    # Article level float fields
    art = data.get("article", {})
    for field in ["rel", "comp"]:
        val = art.get(field, 0)
        try:
            art[field] = float(val)
        except (TypeError, ValueError):
            art[field] = 0.0

    return data


def validate_schema(data: dict) -> bool:
    """Kiểm tra schema tối thiểu theo rule.md."""
    return "article" in data and "claims" in data


_CLAIM_REQUIRED_FIELDS = {
    "claim", "risk_level", "fact_check_status",
    "fact_check_source_url", "source_fidelity",
    "source_coverage", "hallucination_rate", "source_quality", "notes",
}

_ARTICLE_REQUIRED_FIELDS = {
    "domain_key", "domain", "sub_domain", "sub_domain_id",
    "rel", "comp",
}


def extract_single_claim_json(raw: str) -> dict:
    """
    Parse JSON 1 claim từ Claude response (per-claim mode).
    Dùng lại 4 strategy của extract_json nhưng expect object claim trực tiếp.
    Raise ValueError nếu không parse được hoặc thiếu field bắt buộc.
    """
    data = extract_json(raw)

    # Claude có thể wrap trong {"claims": [...]} hoặc {"claim": {...}}
    if "claims" in data and isinstance(data["claims"], list) and data["claims"]:
        data = data["claims"][0]
    elif "claim_data" in data:
        data = data["claim_data"]

    # Validate field tối thiểu
    missing = _CLAIM_REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"Claim JSON thiếu fields: {missing}")

    return data


def normalize_claim(claim: dict) -> dict:
    """Normalize 1 claim — dùng trong per-claim mode."""
    raw_status = str(claim.get("fact_check_status", "")).strip().upper()
    claim["fact_check_status"] = _STATUS_NORMALIZE.get(raw_status, raw_status)

    for field in ["source_fidelity", "source_coverage", "hallucination_rate", "source_quality"]:
        val = claim.get(field, 0)
        try:
            claim[field] = float(val)
        except (TypeError, ValueError):
            claim[field] = 0.0

    return claim


def make_error_claim(claim_idx: int, para: dict, reason: str) -> dict:
    """
    Tạo claim lỗi placeholder khi Claude fail sau retry.
    Giúp pipeline tiếp tục mà không mất claim.
    """
    return {
        "claim": para.get("text", ""),
        "risk_level": "GENERAL",
        "fact_check_status": "ERROR",
        "fact_check_source_url": "",
        "source_fidelity": 0.0,
        "source_coverage": 0.0,
        "hallucination_rate": 0.0,
        "source_quality": 0.0,
        "notes": f"RISK=GENERAL: N/A\nSF=N/A: TOOL_ERROR\nSC=0.05: TOOL_ERROR\nHR=0.00: TOOL_ERROR\nSQ=0.05: TOOL_ERROR\nTXT=LỖI: {reason[:200]}",
    }


def extract_article_json(raw: str) -> dict:
    """
    Parse article-level JSON từ footer response (per-claim mode).
    Expect object với các field rel, comp, sub_domain, v.v.
    """
    data = extract_json(raw)

    # Unwrap nếu Claude wrap trong {"article": {...}}
    if "article" in data and isinstance(data["article"], dict):
        data = data["article"]

    missing = _ARTICLE_REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"Article JSON thiếu fields: {missing}")

    # Normalize float fields
    for field in ["rel", "comp"]:
        val = data.get(field, 0)
        try:
            data[field] = float(val)
        except (TypeError, ValueError):
            data[field] = 0.0

    return data
