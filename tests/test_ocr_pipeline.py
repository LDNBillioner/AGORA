"""Tests for the OCR normalization/validation pipeline in Engine.py."""

import pytest
from fastapi import HTTPException

from Engine import (
    normalize_ocr_payload,
    fallback_parse_items,
    validate_ocr_output,
)


class TestNormalizeOcrPayload:
    def test_parses_clean_json(self):
        raw = '{"merchant_name": "Toko A", "items": [], "total_amount": 100}'
        assert normalize_ocr_payload(raw)["merchant_name"] == "Toko A"

    def test_strips_markdown_code_fence(self):
        raw = '```json\n{"merchant_name": "Toko B", "items": []}\n```'
        assert normalize_ocr_payload(raw)["merchant_name"] == "Toko B"

    def test_falls_back_to_raw_text_on_invalid_json(self):
        payload = normalize_ocr_payload("bukan json sama sekali")
        assert payload["raw_text"] == "bukan json sama sekali"
        assert payload["currency"] == "IDR"


class TestFallbackParseItems:
    def test_parses_qty_x_price_lines(self):
        payload = {
            "raw_text": "Kopi Susu 2 x 15000\nRoti Bakar 1 x 10000"
        }
        items = fallback_parse_items(payload)
        assert len(items) == 2
        assert items[0] == {"item": "Kopi Susu", "quantity": 2, "price": 15000.0}

    def test_skips_summary_lines(self):
        payload = {
            "raw_text": "Total: 25000\nBayar 50000\nKopi Susu 2 x 15000"
        }
        items = fallback_parse_items(payload)
        assert [i["item"] for i in items] == ["Kopi Susu"]

    def test_empty_raw_text_returns_empty_list(self):
        assert fallback_parse_items({"raw_text": ""}) == []


class TestValidateOcrOutput:
    def test_rejects_non_dict(self):
        with pytest.raises(HTTPException) as exc:
            validate_ocr_output(["not", "a", "dict"])
        assert exc.value.status_code == 400

    def test_rejects_when_no_items_can_be_parsed(self):
        with pytest.raises(HTTPException) as exc:
            validate_ocr_output({"raw_text": "", "items": []})
        assert exc.value.status_code == 400

    def test_normalizes_and_computes_total(self):
        result = validate_ocr_output(
            {
                "merchant_name": "Toko C",
                "items": [
                    {"item": "Kopi", "qty": "2", "price": "15.000"},
                    {"item": "Gula", "quantity": "1", "amount": "8,5"},
                ],
            }
        )
        assert result["items"][0] == {
            "item": "Kopi", "quantity": 2, "price": 15000.0,
        }
        assert result["items"][1]["price"] == 8.5
        # total computed from items when missing
        assert result["total_amount"] == pytest.approx(15000 * 2 + 8.5)
        assert result["currency"] == "IDR"

    def test_keeps_provided_total(self):
        result = validate_ocr_output(
            {
                "items": [{"item": "A", "quantity": 1, "price": 10}],
                "total_amount": "99.999",
            }
        )
        assert result["total_amount"] == 99999.0
