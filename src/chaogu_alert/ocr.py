from __future__ import annotations

import re
from typing import Any


def extract_trades(image_path: str) -> list[dict[str, Any]]:
    """Run PaddleOCR on image and extract trade records."""
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        raise ImportError(
            "PaddleOCR not installed. Run: pip install paddleocr paddlepaddle"
        )

    ocr = PaddleOCR(lang="ch", show_log=False)
    result = ocr.ocr(image_path)

    if not result or not result[0]:
        return []

    text_boxes = [
        {"text": line[1][0], "confidence": line[1][1],
         "bbox": line[0]}
        for line in result[0]
    ]

    rows = _cluster_rows(text_boxes)
    trades = []
    for row_texts in rows:
        trade = _extract_fields(row_texts)
        if _validate(trade):
            trades.append(trade)
    return trades


def _cluster_rows(text_boxes: list[dict]) -> list[list[str]]:
    """Cluster text boxes into rows by y-coordinate proximity."""
    if not text_boxes:
        return []
    sorted_boxes = sorted(text_boxes, key=lambda b: (b["bbox"][0][1], b["bbox"][0][0]))
    rows: list[list[str]] = []
    current_row: list[str] = []
    current_y = sorted_boxes[0]["bbox"][0][1]

    for box in sorted_boxes:
        y = box["bbox"][0][1]
        if abs(y - current_y) < 10:  # same row threshold in pixels
            current_row.append(box["text"])
        else:
            if current_row:
                rows.append(current_row)
            current_row = [box["text"]]
            current_y = y
    if current_row:
        rows.append(current_row)
    return rows


def _extract_fields(row_texts: list[str]) -> dict[str, Any]:
    """Extract trade fields from a row of texts using regex."""
    combined = " ".join(row_texts)

    # Symbol code: 6 digits
    code_match = re.search(r"\b(\d{6})\b", combined)
    symbol = code_match.group(1) if code_match else ""

    # Action direction
    if any(w in combined for w in ["买入", "买"]):
        action = "buy"
    elif any(w in combined for w in ["卖出", "卖"]):
        action = "sell"
    else:
        action = ""

    # Price: decimal with 3 digits after point (Chinese brokerage standard)
    price_match = re.search(r"(\d+\.\d{3})", combined)
    price = float(price_match.group(1)) if price_match else 0.0

    # Date: YYYY-MM-DD or YYYY/MM/DD
    date_match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", combined)
    trade_date = date_match.group(1).replace("/", "-") if date_match else ""

    # Time: HH:MM:SS
    time_match = re.search(r"(\d{2}:\d{2}:\d{2})", combined)
    trade_time = time_match.group(1) if time_match else None

    # Shares: integer, 100-1000000, divisible by 100
    shares_matches = re.findall(r"\b(\d+)\b", combined)
    shares = 0
    for m in shares_matches:
        n = int(m)
        if 100 <= n <= 1000000 and n % 100 == 0:
            shares = n
            break

    # Amount: decimal with 2 digits
    amount_match = re.search(r"(\d+\.\d{2})", combined)
    amount = float(amount_match.group(1)) if amount_match else round(shares * price, 2)

    return {
        "symbol": symbol,
        "name": "",
        "action": action,
        "trade_date": trade_date,
        "trade_time": trade_time,
        "price": price,
        "shares": shares,
        "amount": amount,
        "commission": 0.0,
    }


def _validate(trade: dict) -> bool:
    """Return True if required fields are present."""
    return bool(
        trade["symbol"]
        and trade["action"]
        and trade["trade_date"]
        and trade["price"] > 0
        and trade["shares"] > 0
    )
