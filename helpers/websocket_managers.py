"""
WebSocket management for Archipelago server connections.
Handles connection lifecycle, retry logic, and message processing.
"""

import asyncio
import json
import uuid
import websockets
from typing import Optional, Dict, Callable


class WebSocketConnectionManager:
    """Manages WebSocket connections with retry logic and error handling."""

    def __init__(self, max_reconnect_attempts: int = 5, base_delay: int = 2, max_delay: int = 60):
        self.max_reconnect_attempts = max_reconnect_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay

    def calculate_backoff_delay(self, attempt: int) -> int:
        """Calculate exponential backoff delay."""
        return min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)

    async def create_connection(self, server_url: str, timeout: float = 15.0):
        """Create a WebSocket connection with proper configuration."""
        return await asyncio.wait_for(
            websockets.connect(
                server_url,
                ping_interval=20,  # Ping every 20 seconds
                ping_timeout=10,   # Wait 10 seconds for pong
                close_timeout=10,  # Wait 10 seconds for close
                max_size=None,     # No message size limit
                compression="deflate"  # Enable compression as expected by Archipelago
            ),
            timeout=timeout
        )

    def create_connect_message(self, password: Optional[str] = None) -> dict:
        """Create the initial connection message for Archipelago."""
        return {
            "cmd": "Connect",
            "game": "",
            "password": password,
            "name": "Rhelbot",
            "version": {"major": 0, "minor": 6, "build": 0, "class": "Version"},
            "tags": ["Tracker"],
            "items_handling": 0b000,  # No items handling for tracker
            "uuid": uuid.getnode()
        }

    async def send_initial_handshake(self, websocket, password: Optional[str] = None):
        """Send initial connection message and handle handshake."""
        connect_msg = self.create_connect_message(password)
        await websocket.send(json.dumps([connect_msg]))
        print("Sent connection message")

    async def request_data_package(self, websocket, slot_info: Dict):
        """Request DataPackage for games in use."""
        games_in_use = list(set(player_info.get("game", "") for player_info in slot_info.values()))
        games_in_use = [game for game in games_in_use if game]  # Remove empty strings

        if games_in_use:
            get_data_msg = {"cmd": "GetDataPackage", "games": games_in_use}
            print(f"Requesting DataPackage for games: {games_in_use}")
        else:
            # Fallback to requesting all games if we can't determine which ones are in use
            get_data_msg = {"cmd": "GetDataPackage"}
            print("Requesting full DataPackage (couldn't determine games in use)")

        await websocket.send(json.dumps([get_data_msg]))


class WebSocketMessageProcessor:
    """Handles WebSocket message processing and connection state."""

    def __init__(self):
        self.connection_confirmed = False
        self.connection_stable = False
        self.stable_message_count = 0

    async def process_connection_message(self, msg: dict, channel, connection_data: Dict, websocket):
        """Process Connected message and handle initial setup."""
        if msg.get("cmd") == "Connected" and not self.connection_confirmed:
            self.connection_confirmed = True
            server_url = websocket.remote_address
            await channel.send(f"🔗 Successfully connected to Archipelago server: {server_url}")

            # Store connection data for player lookups
            server_key = f"connection_{len(connection_data)}"
            connection_data[server_key] = msg
            print(f"Stored connection data: {msg.get('slot_info', {})}")

            # Request DataPackage
            slot_info = msg.get("slot_info", {})
            manager = WebSocketConnectionManager()
            await manager.request_data_package(websocket, slot_info)

            return True
        return False

    async def process_connection_refused(self, msg: dict, channel):
        """Process ConnectionRefused message."""
        if msg.get("cmd") == "ConnectionRefused":
            reason = msg.get("errors", ["Unknown error"])
            await channel.send(f"❌ Connection refused: {', '.join(reason)}")
            return True
        return False

    def update_stability_counter(self) -> int:
        """Update and return the current reconnect attempts based on connection stability."""
        if self.connection_confirmed:
            self.stable_message_count += 1
            if self.stable_message_count >= 5 and not self.connection_stable:
                self.connection_stable = True
                print("Connection is stable, reset reconnect counter")
                return 0  # Reset reconnect attempts
        return None  # No change to reconnect attempts


class WebSocketErrorHandler:
    """Handles WebSocket errors and retry logic."""

    @staticmethod
    async def handle_timeout_error(connection_confirmed: bool, websocket) -> bool:
        """Handle timeout errors. Returns True if connection should continue."""
        if not connection_confirmed:
            print("Connection timeout during initial handshake")
            raise websockets.exceptions.ConnectionClosed(None, None)
        else:
            print("No message received in 120 seconds, checking connection...")
            # Send a ping to check if connection is still alive
            try:
                pong = await websocket.ping()
                await asyncio.wait_for(pong, timeout=10.0)
                print("Connection is still alive")
                return True
            except Exception as ping_error:
                print(f"Ping failed: {ping_error}")
                raise websockets.exceptions.ConnectionClosed(None, None)

    @staticmethod
    def should_retry_connection(error_type: type, reconnect_attempts: int, max_attempts: int,
                              connection_stable: bool) -> tuple[bool, int]:
        """
        Determine if connection should be retried and return new attempt count.
        Returns (should_retry, new_attempt_count).
        """
        if error_type == websockets.exceptions.InvalidURI:
            return False, reconnect_attempts

        # If we haven't established a stable connection, increment reconnect attempts
        if not connection_stable:
            new_attempts = reconnect_attempts + 1
        else:
            # If connection was stable, reset counter and try again
            new_attempts = 1

        should_retry = new_attempts <= max_attempts
        return should_retry, new_attempts

    @staticmethod
    async def cleanup_websocket(websocket):
        """Safely close websocket connection."""
        if websocket:
            try:
                await websocket.close()
            except Exception as close_error:
                print(f"Error closing websocket: {close_error}")


async def websocket_listener_main_loop(server_url: str, channel, password: Optional[str],
                                     active_connections: Dict, connection_data: Dict,
                                     process_ap_message_func: Callable):
    """
    Main WebSocket listener loop with connection management and retry logic.

    Args:
        server_url: WebSocket server URL to connect to
        channel: Discord channel to send messages to
        password: Optional server password
        active_connections: Dictionary to track active connections
        connection_data: Dictionary to store connection data
        process_ap_message_func: Function to process individual AP messages
    """
    manager = WebSocketConnectionManager()
    error_handler = WebSocketErrorHandler()

    websocket = None
    reconnect_attempts = 0

    while reconnect_attempts <= manager.max_reconnect_attempts:
        message_processor = WebSocketMessageProcessor()

        try:
            if reconnect_attempts > 0:
                # Calculate exponential backoff delay
                delay = manager.calculate_backoff_delay(reconnect_attempts)
                await channel.send(f"⚠️ Connection lost to {server_url}, reconnecting in {delay} seconds... (attempt {reconnect_attempts}/{manager.max_reconnect_attempts})")
                print(f"Waiting {delay} seconds before reconnect attempt {reconnect_attempts}")
                await asyncio.sleep(delay)

            print(f"Attempting to connect to {server_url} (attempt {reconnect_attempts + 1})")

            # Create connection
            websocket = await manager.create_connection(server_url)
            print(f"Successfully connected to {server_url}")

            # Update the connection tracking with the websocket
            if server_url in active_connections:
                active_connections[server_url]["websocket"] = websocket

            # Send initial handshake
            await manager.send_initial_handshake(websocket, password)

            # Main message processing loop
            try:
                while True:
                    try:
                        # Wait for message with longer timeout for initial connection
                        timeout = 30.0 if not message_processor.connection_confirmed else 120.0
                        message = await asyncio.wait_for(websocket.recv(), timeout=timeout)

                        try:
                            data = json.loads(message)
                            print(f"Received message: {data}")

                            # Process different message types
                            for msg in data:
                                # Handle connection confirmation
                                if await message_processor.process_connection_message(
                                    msg, channel, connection_data, websocket
                                ):
                                    continue

                                # Handle connection rejection
                                if await message_processor.process_connection_refused(msg, channel):
                                    return

                                # Process all messages
                                try:
                                    is_complete = await process_ap_message_func(msg, channel)

                                    # Check if game completion was detected
                                    if is_complete:
                                        print(f"Game completion detected, stopping tracking for {server_url}")
                                        await channel.send(f"✅ Game completed! Stopping tracking for {server_url}")

                                        # Close websocket gracefully
                                        if websocket:
                                            await websocket.close()

                                        # Remove from active connections
                                        if server_url in active_connections:
                                            del active_connections[server_url]

                                        return  # Exit the listener

                                    # Update stability counter and potentially reset reconnect attempts
                                    reset_attempts = message_processor.update_stability_counter()
                                    if reset_attempts is not None:
                                        reconnect_attempts = reset_attempts

                                except Exception as msg_error:
                                    print(f"Error processing individual message: {msg_error}")
                                    continue

                        except json.JSONDecodeError as json_error:
                            print(f"Failed to decode message: {message} - Error: {json_error}")
                            continue

                    except asyncio.TimeoutError:
                        if await error_handler.handle_timeout_error(
                            message_processor.connection_confirmed, websocket
                        ):
                            continue

                    except websockets.exceptions.ConnectionClosed as conn_closed:
                        print(f"Websocket connection closed: {conn_closed}")
                        raise conn_closed

                    except Exception as loop_error:
                        print(f"Unexpected error in message loop: {loop_error}")
                        # For unexpected errors, try to continue but increment reconnect counter
                        if not message_processor.connection_stable:
                            raise loop_error
                        continue

            except (websockets.exceptions.ConnectionClosed, Exception) as conn_error:
                print(f"Connection error: {conn_error}")

                # Determine if we should retry
                should_retry, new_attempts = error_handler.should_retry_connection(
                    type(conn_error), reconnect_attempts, manager.max_reconnect_attempts,
                    message_processor.connection_stable
                )

                reconnect_attempts = new_attempts

                if should_retry:
                    continue  # Try to reconnect
                else:
                    await channel.send(f"❌ Connection to {server_url} failed after {manager.max_reconnect_attempts} attempts")
                    break

        except asyncio.TimeoutError:
            print(f"Connection timeout to {server_url}")
            reconnect_attempts += 1
            if reconnect_attempts <= manager.max_reconnect_attempts:
                continue
            else:
                await channel.send(f"❌ Connection timeout to {server_url} after {manager.max_reconnect_attempts} attempts")
                break

        except websockets.exceptions.InvalidURI:
            await channel.send(f"❌ Invalid server URL: {server_url}")
            break

        except Exception as connect_error:
            print(f"Error connecting to {server_url}: {connect_error}")
            reconnect_attempts += 1
            if reconnect_attempts <= manager.max_reconnect_attempts:
                continue
            else:
                await channel.send(f"❌ Error connecting to {server_url}: {str(connect_error)}")
                break

        finally:
            # Clean up websocket connection for this attempt
            await error_handler.cleanup_websocket(websocket)
            websocket = None

    # Final cleanup
    print(f"Websocket listener for {server_url} is exiting")
    if server_url in active_connections:
        del active_connections[server_url]