import json
from datetime import UTC, datetime

import httpx
import pytest

from discord_mcp.config import MarketBoardSettings
from discord_mcp.feeds.market_board import (
    COLOR_DOWN,
    COLOR_UP,
    build_embed,
    fetch_markets,
    format_market_cap,
    format_price,
    publish,
    run_once,
    update_board,
)

WEBHOOK_URL = "https://discord.com/api/webhooks/1/token"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _coin(coin_id: str, symbol: str, price: float, change: float | None) -> dict:
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": coin_id.title(),
        "current_price": price,
        "price_change_percentage_24h": change,
        "market_cap": 1_620_000_000_000,
    }


def test_format_price_keeps_precision_for_small_coins():
    assert format_price(80712) == "$80,712.00"
    assert format_price(0.00001234) == "$0.000012"


def test_format_market_cap_uses_compact_units():
    assert format_market_cap(1_622_672_427_961) == "$1.62T"
    assert format_market_cap(59_641_685_392) == "$59.64B"
    assert format_market_cap(None) == "n/a"


def test_build_embed_fields_and_color():
    markets = [_coin("bitcoin", "btc", 80712, 0.85), _coin("solana", "sol", 101.07, -3.2)]
    fear_greed = {"value": "48", "value_classification": "Neutral"}

    embed = build_embed(markets, fear_greed, interval=300, now=NOW)

    assert [field["name"] for field in embed["fields"]] == [
        "BTC · Bitcoin",
        "SOL · Solana",
        "Fear & Greed Index",
    ]
    assert "🟢 +0.85%" in embed["fields"][0]["value"]
    assert "🔴 -3.20%" in embed["fields"][1]["value"]
    assert embed["color"] == COLOR_DOWN
    assert "CoinGecko" in embed["footer"]["text"]
    assert "every 5 min" in embed["footer"]["text"]


def test_build_embed_without_fear_greed():
    embed = build_embed([_coin("bitcoin", "btc", 80712, 1.0)], None, interval=300, now=NOW)

    assert len(embed["fields"]) == 1
    assert embed["color"] == COLOR_UP


def test_fetch_markets_keeps_configured_order():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[_coin("bitcoin", "btc", 1, 0), _coin("solana", "sol", 1, 0)]
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        markets = fetch_markets(client, ["solana", "bitcoin", "unknown"])

    assert [coin["id"] for coin in markets] == ["solana", "bitcoin"]


def test_publish_edits_existing_message():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "42"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        message_id = publish(client, WEBHOOK_URL, {"title": "board"}, "42")

    assert message_id == "42"
    assert [(r.method, r.url.path) for r in requests] == [
        ("PATCH", "/api/webhooks/1/token/messages/42")
    ]


def test_publish_reposts_when_message_was_deleted():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(404, json={"message": "Unknown Message"})
        return httpx.Response(200, json={"id": "99"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        message_id = publish(client, WEBHOOK_URL, {"title": "board"}, "42")

    assert message_id == "99"
    assert [r.method for r in requests] == ["PATCH", "POST"]
    assert requests[1].url.params["wait"] == "true"
    assert json.loads(requests[1].content)["username"] == "Market Board"


def test_update_board_reports_failure_without_raising(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.coingecko.com":
            return httpx.Response(200, json=[_coin("bitcoin", "btc", 80712, 1.0)])
        if request.url.host == "api.alternative.me":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(500)

    config = MarketBoardSettings(
        webhook_url=WEBHOOK_URL, coins="bitcoin", state_file=str(tmp_path / "state.json")
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert update_board(client, config) is False

    assert not (tmp_path / "state.json").exists()


def test_publish_without_repost_raises_on_missing_message():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"message": "Unknown Message"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            publish(client, WEBHOOK_URL, {"title": "board"}, "42", repost_if_missing=False)

    assert [r.method for r in requests] == ["PATCH"]


def test_run_once_with_message_id_edits_it_and_skips_state_file(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.coingecko.com":
            return httpx.Response(200, json=[_coin("bitcoin", "btc", 80712, 1.0)])
        if request.url.host == "api.alternative.me":
            return httpx.Response(
                200, json={"data": [{"value": "48", "value_classification": "Neutral"}]}
            )
        return httpx.Response(200, json={"id": "777"})

    config = MarketBoardSettings(
        webhook_url=WEBHOOK_URL,
        coins="bitcoin",
        message_id="777",
        state_file=str(tmp_path / "state.json"),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert run_once(client, config) == "777"

    webhook_requests = [r for r in requests if r.url.host == "discord.com"]
    assert [(r.method, r.url.path) for r in webhook_requests] == [
        ("PATCH", "/api/webhooks/1/token/messages/777")
    ]
    assert not (tmp_path / "state.json").exists()


def test_update_board_handles_invalid_webhook_url(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.coingecko.com":
            return httpx.Response(200, json=[_coin("bitcoin", "btc", 80712, 1.0)])
        return httpx.Response(200, json={"data": []})

    config = MarketBoardSettings(
        webhook_url="http://[::1/x",
        coins="bitcoin",
        message_id="1",
        state_file=str(tmp_path / "state.json"),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert update_board(client, config) is False
