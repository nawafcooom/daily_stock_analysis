# -*- coding: utf-8 -*-
"""Regression tests for report consistency: snapshot change %, localized units,
missing-metric rendering, watch-only position plan, and Telegram formatting."""

from types import SimpleNamespace

from data_provider.us_index_mapping import get_us_index_english_name
from src.analyzer import GeminiAnalyzer, _apply_hold_watch_dashboard
from src.notification import NotificationService
from src.notification_sender.telegram_sender import TelegramSender
from src.report_language import get_localized_stock_name


def _snapshot(today, yesterday, language="en", realtime=None):
    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
    context = {"today": today, "yesterday": yesterday, "date": "2026-11-16"}
    if realtime is not None:
        context["realtime"] = realtime
    return analyzer._build_market_snapshot(context, language)


def test_snapshot_change_pct_matches_close_and_prev_close():
    # Fallback quote reported +2.47% while the bar actually fell 8.75%.
    snapshot = _snapshot(
        {"close": 534.06, "open": 566.79, "high": 571.14, "low": 520.01, "pct_chg": 2.47, "volume": 49163200},
        {"close": 585.30},
    )
    assert snapshot["change_amount"] == "-51.24"
    assert snapshot["pct_chg"] == "-8.75%"
    assert snapshot["amplitude"] == "8.74%"


def test_snapshot_change_pct_falls_back_to_bar_value_without_prev_close():
    snapshot = _snapshot({"close": 100.0, "pct_chg": 1.5}, {})
    assert snapshot["pct_chg"] == "1.50%"


def test_snapshot_uses_report_language_units_and_hides_none():
    today = {"close": 356.27, "volume": 39985300, "amount": 1.2e10}
    english = _snapshot(today, {"close": 354.88}, "en", realtime={"price": 356.27, "volume_ratio": None})
    assert english["volume"] == "39.99M shares"
    assert "万" not in english["volume"] and "亿" not in english["amount"]
    assert english["volume_ratio"] == "N/A"
    assert english["pct_chg"] == "0.39%"
    chinese = _snapshot(today, {"close": 354.88}, "zh")
    assert chinese["volume"] == "3998.53 万股"


def test_financial_amounts_follow_report_language():
    assert NotificationService._format_amount_cn(114_477_000_000, "USD", "en") == "114.48B USD"
    assert NotificationService._format_amount_cn(-2_500_000, "USD", "en") == "-2.50M USD"
    assert NotificationService._format_amount_cn(114_477_000_000, "USD", "zh") == "1144.77 亿美元"
    assert NotificationService._format_amount_cn(114_477_000_000, "USD") == "1144.77 亿美元"
    assert NotificationService._format_per_share(1.07, "USD", "en") == "1.0700 USD"
    assert NotificationService._format_per_share(1.07, "USD") == "1.0700 美元"


def test_display_metric_never_prints_none():
    assert NotificationService._display_metric(None) == "N/A"
    assert NotificationService._display_metric("None", suffix="%") == "N/A"
    assert NotificationService._display_metric(1.25) == "1.25"
    assert NotificationService._display_metric(3.2, suffix="%") == "3.2%"
    assert NotificationService._display_metric("3.2%", suffix="%") == "3.2%"


def test_us_index_name_is_english_in_non_chinese_reports():
    assert get_us_index_english_name("SPX") == "S&P 500 Index"
    assert get_us_index_english_name("AAPL") is None
    assert get_localized_stock_name("标普500指数", "SPX", "en") == "S&P 500 Index"
    assert get_localized_stock_name("标普500指数", "SPX", "zh") == "标普500指数"
    assert get_localized_stock_name("Apple Inc.", "AAPL", "en") == "Apple Inc."


def test_watch_downgrade_marks_position_plan_as_conditional():
    result = SimpleNamespace(
        operation_advice="Buy",
        risk_warning="",
        buy_reason="",
        dashboard={
            "battle_plan": {
                "position_strategy": {
                    "suggested_position": "Suggested position: 4/10",
                    "entry_plan": "Build a 2/10 position at market open",
                }
            }
        },
    )
    kwargs = dict(
        advice="Hold and watch",
        reason="capital flow unavailable",
        current_price=356.27,
        support=354.21,
        resistance=365.0,
        flow_bias="unavailable",
        no_position="Do not chase.",
        has_position="Keep size controlled.",
    )
    _apply_hold_watch_dashboard(result, "en", **kwargs)
    _apply_hold_watch_dashboard(result, "en", **kwargs)  # idempotent
    suggested = result.dashboard["battle_plan"]["position_strategy"]["suggested_position"]
    assert suggested.startswith("Watch-only: no new position now")
    assert suggested.endswith("Suggested position: 4/10")
    assert suggested.count("Watch-only") == 1


def _telegram_sender():
    return TelegramSender.__new__(TelegramSender)


def test_telegram_conversion_does_not_backslash_parentheses():
    converted = _telegram_sender()._convert_to_telegram_markdown(
        "**Apple Inc. (AAPL)**: [Latest News] none; see [docs](https://example.com/a)"
    )
    assert converted == "*Apple Inc. (AAPL)*: \\[Latest News] none; see [docs](https://example.com/a)"


def test_telegram_plain_fallback_strips_markup():
    stripped = TelegramSender._strip_telegram_markdown("*📊 Earnings* \\[Latest News] 3 * 4 (ok)")
    assert stripped == "📊 Earnings [Latest News] 3 * 4 (ok)"


def test_telegram_long_section_splits_on_line_boundaries():
    sender = _telegram_sender()
    sent = []
    sender._send_telegram_message = lambda api_url, chat_id, text, *a, **k: sent.append(text) or True
    content = "\n".join(f"- *Signal {i}*: value {i}" for i in range(60))
    assert sender._send_telegram_chunked("url", "chat", content, 200)
    assert len(sent) > 1
    assert "".join(sent) == content
    assert all(chunk.endswith("\n") for chunk in sent[:-1])
    assert all(chunk.count("*") % 2 == 0 for chunk in sent)
