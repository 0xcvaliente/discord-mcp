"""Live crypto market board: one Discord message, edited in place through a webhook.

Runs independently of the MCP server, so it keeps updating without an MCP client connected.
"""

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from discord_mcp.config import MarketBoardSettings, settings
from discord_mcp.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
FEAR_GREED_URL = "https://api.alternative.me/fng/"

COLOR_UP = 0x2ECC71
COLOR_DOWN = 0xE74C3C


def fetch_markets(client: httpx.Client, coin_ids: list[str], api_key: str = "") -> list[dict]:
    headers = {"x-cg-demo-api-key": api_key} if api_key else {}
    response = client.get(
        COINGECKO_MARKETS_URL,
        params={"vs_currency": "usd", "ids": ",".join(coin_ids)},
        headers=headers,
    )
    response.raise_for_status()
    by_id = {coin["id"]: coin for coin in response.json()}
    # Keep the configured order; CoinGecko sorts by market cap
    return [by_id[coin_id] for coin_id in coin_ids if coin_id in by_id]


def fetch_fear_greed(client: httpx.Client) -> dict | None:
    try:
        response = client.get(FEAR_GREED_URL, params={"limit": 1})
        response.raise_for_status()
        return response.json()["data"][0]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        logger.warning("fear_greed_fetch_failed", error=str(e))
        return None


def format_price(price: float) -> str:
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:,.6f}"


def format_market_cap(value: float | None) -> str:
    if not value:
        return "n/a"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if value >= threshold:
            return f"${value / threshold:.2f}{suffix}"
    return f"${value:,.0f}"


def format_change(change: float | None) -> str:
    if change is None:
        return "n/a"
    icon = "🟢" if change >= 0 else "🔴"
    return f"{icon} {change:+.2f}%"


def build_embed(
    markets: list[dict], fear_greed: dict | None, interval: int, now: datetime
) -> dict[str, Any]:
    fields = [
        {
            "name": f"{coin['symbol'].upper()} · {coin['name']}",
            "value": (
                f"**{format_price(coin['current_price'])}**\n"
                f"{format_change(coin.get('price_change_percentage_24h'))} (24h)\n"
                f"MCap {format_market_cap(coin.get('market_cap'))}"
            ),
            "inline": True,
        }
        for coin in markets
    ]

    if fear_greed:
        fields.append(
            {
                "name": "Fear & Greed Index",
                "value": f"**{fear_greed['value']}** · {fear_greed['value_classification']}",
                "inline": False,
            }
        )

    changes = [
        coin["price_change_percentage_24h"]
        for coin in markets
        if coin.get("price_change_percentage_24h") is not None
    ]
    trending_up = not changes or sum(changes) >= 0

    return {
        "title": "📊 Market Board",
        "color": COLOR_UP if trending_up else COLOR_DOWN,
        "fields": fields,
        "footer": {
            "text": (
                f"Data provided by CoinGecko · Fear & Greed by alternative.me · "
                f"updates every {max(interval // 60, 1)} min"
            )
        },
        "timestamp": now.isoformat(),
    }


def load_message_id(state_path: Path) -> str | None:
    try:
        return json.loads(state_path.read_text()).get("message_id")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_message_id(state_path: Path, message_id: str) -> None:
    state_path.write_text(json.dumps({"message_id": message_id}))


def publish(
    client: httpx.Client,
    webhook_url: str,
    embed: dict[str, Any],
    message_id: str | None,
    repost_if_missing: bool = True,
) -> str:
    payload = {"embeds": [embed]}

    if message_id:
        response = client.patch(f"{webhook_url}/messages/{message_id}", json=payload)
        if response.status_code != 404:
            response.raise_for_status()
            return message_id
        if not repost_if_missing:
            logger.error(
                "board_message_missing",
                message_id=message_id,
                hint=(
                    "The board message or webhook was deleted; update MARKET_BOARD_MESSAGE_ID "
                    "and the webhook secret"
                ),
            )
            response.raise_for_status()
        logger.info("board_message_missing_reposting", message_id=message_id)

    response = client.post(
        webhook_url, params={"wait": "true"}, json={"username": "Market Board", **payload}
    )
    response.raise_for_status()
    return response.json()["id"]


def run_once(client: httpx.Client, config: MarketBoardSettings) -> str:
    coin_ids = [coin.strip() for coin in config.coins.split(",") if coin.strip()]
    markets = fetch_markets(client, coin_ids, config.coingecko_api_key)
    fear_greed = fetch_fear_greed(client)
    embed = build_embed(markets, fear_greed, config.interval, datetime.now(UTC))

    if config.message_id:
        message_id = publish(
            client, config.webhook_url, embed, config.message_id, repost_if_missing=False
        )
    else:
        state_path = Path(config.state_file)
        message_id = publish(client, config.webhook_url, embed, load_message_id(state_path))
        save_message_id(state_path, message_id)

    logger.info("board_updated", message_id=message_id, coins=len(markets))
    return message_id


def update_board(client: httpx.Client, config: MarketBoardSettings) -> bool:
    try:
        run_once(client, config)
        return True
    except httpx.HTTPStatusError as e:
        # The default message includes the request URL, which for webhooks contains the token
        logger.error("board_update_failed", status=e.response.status_code, host=e.request.url.host)
    except httpx.InvalidURL as e:
        # Not an HTTPError, and its message can contain the webhook URL
        logger.error("board_update_failed", error_type=type(e).__name__)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.error("board_update_failed", error=str(e))
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Post a live crypto market board to Discord")
    parser.add_argument("--once", action="store_true", help="Update the board once and exit")
    args = parser.parse_args()

    setup_logging()
    # httpx logs every request URL at INFO, which would leak the webhook token
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = settings.market_board
    if not config.webhook_url:
        raise SystemExit("MARKET_BOARD_WEBHOOK_URL is not set")

    with httpx.Client(timeout=15) as client:
        if args.once:
            if not update_board(client, config):
                raise SystemExit(1)
            return

        logger.info("market_board_started", interval=config.interval)
        while True:
            update_board(client, config)
            time.sleep(config.interval)


if __name__ == "__main__":
    main()
