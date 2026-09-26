import pytest

from agents.orchestrator import (
    InvalidPayload,
    validate_report_payload,
    validate_signal_summary_payload,
)


def test_validate_report_payload_accepts_valid_data():
    payload = validate_report_payload(
        {
            "ticker": "aapl",
            "horizon": "short_term",
            "verdict": "Bullish",
            "confidence": "medium",
            "reasoning": "Strong earnings beat.",
        }
    )
    assert payload == {
        "ticker": "AAPL",
        "horizon": "short_term",
        "verdict": "Bullish",
        "confidence": "medium",
        "reasoning": "Strong earnings beat.",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"horizon": "next_quarter"},
        {"verdict": "Very Bullish"},
        {"confidence": "super high"},
        {"reasoning": ""},
        {"ticker": ""},
    ],
)
def test_validate_report_payload_rejects_bad_enums(overrides):
    payload = {
        "ticker": "AAPL",
        "horizon": "short_term",
        "verdict": "Bullish",
        "confidence": "medium",
        "reasoning": "Strong earnings beat.",
    }
    payload.update(overrides)
    with pytest.raises(InvalidPayload):
        validate_report_payload(payload)


def test_validate_signal_summary_payload_accepts_valid_data():
    payload = validate_signal_summary_payload(
        {"ticker": "aapl", "agent": "fundamentals", "summary": "Solid margins."}
    )
    assert payload == {"ticker": "AAPL", "agent": "fundamentals", "summary": "Solid margins."}


def test_validate_signal_summary_payload_rejects_unknown_agent():
    with pytest.raises(InvalidPayload):
        validate_signal_summary_payload(
            {"ticker": "AAPL", "agent": "macro", "summary": "Solid margins."}
        )
