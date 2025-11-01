"""WebSocket client for Binance streams."""

import asyncio
import json
import logging
from typing import AsyncIterator, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException


logger = logging.getLogger(__name__)


class BinanceWSClient:
    """Asynchronous WebSocket client for Binance streams."""

    def __init__(
        self,
        symbol: str,
        stream_type: str = "trade",
        base_url: str = "wss://stream.binance.com:9443/stream",
        reconnect_delay: float = 1.0,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
    ):
        """Initialize Binance WebSocket client.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            stream_type: Stream type (e.g., "trade", "kline_1m")
            base_url: Binance WebSocket base URL
            reconnect_delay: Delay before reconnection attempt in seconds
            ping_interval: WebSocket ping interval in seconds
            ping_timeout: WebSocket ping timeout in seconds
        """
        self.symbol = symbol.lower()
        self.stream_type = stream_type
        self.base_url = base_url
        self.reconnect_delay = reconnect_delay
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        # Build WebSocket URL
        stream_name = f"{self.symbol}@{stream_type}"
        self.url = f"{base_url}?streams={stream_name}"

        logger.info(f"Initialized BinanceWSClient for {stream_name}")

    async def connect_and_stream(
        self,
        message_handler: Callable[[dict], None],
    ) -> None:
        """Connect to WebSocket and stream messages with auto-reconnect.

        Args:
            message_handler: Callback function to handle incoming messages
        """
        while True:
            try:
                logger.info(f"Connecting to {self.url}")

                async with websockets.connect(
                    self.url,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                ) as websocket:
                    logger.info("WebSocket connected successfully")

                    async for message in websocket:
                        try:
                            data = json.loads(message)

                            # Binance stream format: {"stream": "...", "data": {...}}
                            if "data" in data:
                                message_handler(data["data"])
                            else:
                                logger.warning(f"Unexpected message format: {data}")

                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode message: {e}")
                        except Exception as e:
                            logger.error(f"Error in message handler: {e}")

            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")

            except WebSocketException as e:
                logger.error(f"WebSocket error: {e}")

            except Exception as e:
                logger.error(f"Unexpected error: {e}")

            # Reconnect with delay
            logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
            await asyncio.sleep(self.reconnect_delay)

    async def stream_messages(self) -> AsyncIterator[dict]:
        """Stream messages as an async iterator with auto-reconnect.

        Yields:
            Message dictionaries from the WebSocket stream
        """
        while True:
            try:
                logger.info(f"Connecting to {self.url}")

                async with websockets.connect(
                    self.url,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                ) as websocket:
                    logger.info("WebSocket connected successfully")

                    async for message in websocket:
                        try:
                            data = json.loads(message)

                            # Binance stream format: {"stream": "...", "data": {...}}
                            if "data" in data:
                                yield data["data"]
                            else:
                                logger.warning(f"Unexpected message format: {data}")

                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode message: {e}")

            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")

            except WebSocketException as e:
                logger.error(f"WebSocket error: {e}")

            except Exception as e:
                logger.error(f"Unexpected error: {e}")

            # Reconnect with delay
            logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
            await asyncio.sleep(self.reconnect_delay)
