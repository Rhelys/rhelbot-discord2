import discord
from discord import app_commands
from discord.ext import commands, tasks
from os import remove, listdir, rename, path
import subprocess
import asyncio
from asyncio import sleep
import websockets
import json
import zipfile
from typing import Optional, Dict
from ruyaml import YAML
import shutil
from datetime import datetime
import uuid
import io
import zlib
import pickle
from collections import namedtuple
import re
import time

# Import all helper functions
from helpers.data_helpers import *
from helpers.lookup_helpers import *
from helpers.server_helpers import *
from helpers.formatting_helpers import *
from helpers.progress_helpers import *

donkeyServer = discord.Object(id=591625815528177690)

@app_commands.guilds(donkeyServer)
class ApCog(commands.GroupCog, group_name="ap"):
    # Class constants
    OUTPUT_DIR = "./Archipelago/output/"
    AP_DIR = "./Archipelago/"
    SYSTEM_EXTENSIONS = [".archipelago", ".txt", ".apsave"]
    STATUS_FILE = "./game_status.txt"
    DEFAULT_SERVER_URL = "ws://ap.rhelys.com:38281"
    
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()
        
        # Tracking variables
        self.active_connections: Dict[str, Dict] = {}
        self.game_data: Dict[str, Dict] = {}
        self.connection_data: Dict[str, Dict] = {}
        self.player_progress: Dict[int, set] = {}
        self.server_process = None
        self.player = ""
        self.game = ""
        
        # Create instance attributes for class constants for compatibility
        self.output_directory = self.OUTPUT_DIR
        self.ap_directory = self.AP_DIR
        self.system_extensions = self.SYSTEM_EXTENSIONS
        self.status_file = self.STATUS_FILE

    def resolve_player_name(self, discord_user_id: int, player_input: str):
        """
        Resolve 'me' or a Discord user mention to the registered player name(s), or return the input as-is.
        
        Args:
            discord_user_id: The Discord ID of the user making the request
            player_input: The player name input, which could be 'me' or a Discord mention
        
        Returns:
            - A player name string if a single match is found
            - A list of player names if multiple matches are found
            - The original input if it's not 'me' or a Discord mention
            - None if no match is found
        """
        # Handle "me" case
        if player_input.lower() == "me":
            status_file = "game_status.json"
            if not path.exists(status_file):
                return None
                
            try:
                with open(status_file, 'r') as f:
                    game_status = json.load(f)
                
                # Get the list of player names for this Discord user
                player_names = game_status.get("discord_users", {}).get(str(discord_user_id), [])
                
                if not player_names:
                    return None
                elif len(player_names) == 1:
                    # If there's only one player, return it directly
                    return player_names[0]
                else:
                    # If there are multiple players, return the list of names
                    return player_names
            except (json.JSONDecodeError, IOError):
                return None
        
        # Handle Discord mention case (e.g., <@123456789>)
        mention_match = re.match(r'<@!?(\d+)>', player_input)
        if mention_match:
            mentioned_user_id = mention_match.group(1)
            
            status_file = "game_status.json"
            if not path.exists(status_file):
                return None
                
            try:
                with open(status_file, 'r') as f:
                    game_status = json.load(f)
                
                # Get the list of player names for the mentioned Discord user
                player_names = game_status.get("discord_users", {}).get(mentioned_user_id, [])
                
                if not player_names:
                    return None
                elif len(player_names) == 1:
                    # If there's only one player, return it directly
                    return player_names[0]
                else:
                    # If there are multiple players, return the list of names
                    return player_names
            except (json.JSONDecodeError, IOError):
                return None
        
        # If it's a plain username with @ prefix, try to find a matching Discord user
        if player_input.startswith('@'):
            username = player_input[1:]  # Remove the @ symbol
            
            status_file = "game_status.json"
            if not path.exists(status_file):
                return player_input  # Return original if we can't check
                
            try:
                with open(status_file, 'r') as f:
                    game_status = json.load(f)
                
                # We don't have direct access to Discord usernames in the status file,
                # so we'll have to assume the original input for now
                return player_input
            except (json.JSONDecodeError, IOError):
                return player_input
        
        # Return original input if it's not a special case
        return player_input

    async def upload_success(self, filepath: str, interaction: discord.Interaction):
        with open(filepath, "rb") as submitted_file:
            await interaction.channel.send(
                content="Player joined successfully\nPlayer: {}\n"
                "Game: {}".format(self.player, self.game),
                file=discord.File(
                    submitted_file, filename=f"{self.player}_{self.game}.yaml"
                ),
            )

    def list_players(self):
        """
        Get a dictionary of current players and their games.
        
        Returns:
            dict: Dictionary mapping player names to their game names
        """
        from helpers.data_helpers import load_game_status
        
        current_players = {}
        game_status = load_game_status("game_status.json")
        
        # Extract players from the game status
        players_data = game_status.get("players", {})
        for player_name, player_info in players_data.items():
            game = player_info.get("game", "Unknown")
            current_players[player_name] = game
            
        return current_players

    async def websocket_listener(self, server_url: str, channel_id: int, password: str = None):
        """Background task to listen to Archipelago websocket and forward messages to Discord channel"""
        channel = self.bot.get_channel(channel_id)
        if not channel:
            print(f"Could not find channel with ID {channel_id}")
            return

        websocket = None
        reconnect_attempts = 0
        max_reconnect_attempts = 5
        base_delay = 2  # Base delay in seconds
        max_delay = 60  # Maximum delay in seconds
        
        while reconnect_attempts <= max_reconnect_attempts:
            try:
                if reconnect_attempts > 0:
                    # Calculate exponential backoff delay
                    delay = min(base_delay * (2 ** (reconnect_attempts - 1)), max_delay)
                    await channel.send(f"⚠️ Connection lost to {server_url}, reconnecting in {delay} seconds... (attempt {reconnect_attempts}/{max_reconnect_attempts})")
                    print(f"Waiting {delay} seconds before reconnect attempt {reconnect_attempts}")
                    await asyncio.sleep(delay)
                
                print(f"Attempting to connect to {server_url} (attempt {reconnect_attempts + 1})")
                
                # Connect to the Archipelago websocket server with proper compression support
                websocket = await asyncio.wait_for(
                    websockets.connect(
                        server_url, 
                        ping_interval=20,  # Ping every 20 seconds
                        ping_timeout=10,   # Wait 10 seconds for pong
                        close_timeout=10,  # Wait 10 seconds for close
                        max_size=None,     # No message size limit
                        compression="deflate"  # Enable compression as expected by Archipelago
                    ),
                    timeout=15.0
                )
                
                print(f"Successfully connected to {server_url}")
                
                # Update the connection tracking with the websocket
                if server_url in self.active_connections:
                    self.active_connections[server_url]["websocket"] = websocket
                
                # Send connection message - based on working bridgeipelago example
                import uuid
                connect_msg = {
                    "cmd": "Connect",
                    "game": "",
                    "password": password,
                    "name": "Rhelbot",
                    "version": {"major": 0, "minor": 6, "build": 0, "class": "Version"},
                    "tags": ["Tracker"],
                    "items_handling": 0b000,  # No items handling for tracker
                    "uuid": uuid.getnode()
                }
                await websocket.send(json.dumps([connect_msg]))
                print("Sent connection message")
                
                # Reset reconnect attempts on successful connection and message exchange
                connection_confirmed = False
                connection_stable = False
                stable_message_count = 0
                
                # Listen for messages indefinitely
                try:
                    while True:
                        try:
                            # Wait for message with longer timeout for initial connection
                            timeout = 30.0 if not connection_confirmed else 120.0
                            message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                            
                            try:
                                data = json.loads(message)
                                print(f"Received message: {data}")
                                
                                # Process different message types
                                for msg in data:
                                    msg_cmd = msg.get("cmd", "")
                                    
                                    # Handle connection confirmation
                                    if msg_cmd == "Connected" and not connection_confirmed:
                                        connection_confirmed = True
                                        await channel.send(f"🔗 Successfully connected to Archipelago server: {server_url}")
                                        
                                        # Store connection data for player lookups
                                        server_key = f"connection_{len(self.connection_data)}"
                                        self.connection_data[server_key] = msg
                                        print(f"Stored connection data: {msg.get('slot_info', {})}")
                                        
                                        # Request DataPackage but only for games that are actually being played
                                        # This reduces the size compared to requesting all games
                                        slot_info = msg.get("slot_info", {})
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
                                        
                                    # Handle connection rejection
                                    elif msg_cmd == "ConnectionRefused":
                                        reason = msg.get("errors", ["Unknown error"])
                                        await channel.send(f"❌ Connection refused: {', '.join(reason)}")
                                        return
                                        
                                    # Process all messages
                                    try:
                                        await self.process_ap_message(msg, channel)
                                        
                                        # Count stable messages to reset reconnect counter
                                        if connection_confirmed:
                                            stable_message_count += 1
                                            if stable_message_count >= 5 and not connection_stable:
                                                connection_stable = True
                                                reconnect_attempts = 0  # Reset on stable connection
                                                print(f"Connection to {server_url} is stable, reset reconnect counter")
                                                
                                    except Exception as msg_error:
                                        print(f"Error processing individual message: {msg_error}")
                                        # Continue processing other messages
                                        continue
                                        
                            except json.JSONDecodeError as json_error:
                                print(f"Failed to decode message: {message} - Error: {json_error}")
                                continue
                                
                        except asyncio.TimeoutError:
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
                                    continue
                                except Exception as ping_error:
                                    print(f"Ping failed: {ping_error}")
                                    raise websockets.exceptions.ConnectionClosed(None, None)
                                
                        except websockets.exceptions.ConnectionClosed as conn_closed:
                            print(f"Websocket connection closed: {conn_closed}")
                            raise conn_closed
                            
                        except Exception as loop_error:
                            print(f"Unexpected error in message loop: {loop_error}")
                            # For unexpected errors, try to continue but increment reconnect counter
                            if not connection_stable:
                                raise loop_error
                            continue
                            
                except (websockets.exceptions.ConnectionClosed, Exception) as conn_error:
                    print(f"Connection error: {conn_error}")
                    
                    # If we haven't established a stable connection, increment reconnect attempts
                    if not connection_stable:
                        reconnect_attempts += 1
                    else:
                        # If connection was stable, reset counter and try again
                        reconnect_attempts = 1
                        connection_stable = False
                    
                    if reconnect_attempts <= max_reconnect_attempts:
                        continue  # Try to reconnect
                    else:
                        await channel.send(f"❌ Connection to {server_url} failed after {max_reconnect_attempts} attempts")
                        break
                        
            except asyncio.TimeoutError:
                print(f"Connection timeout to {server_url}")
                reconnect_attempts += 1
                if reconnect_attempts <= max_reconnect_attempts:
                    continue
                else:
                    await channel.send(f"❌ Connection timeout to {server_url} after {max_reconnect_attempts} attempts")
                    break
                    
            except websockets.exceptions.InvalidURI:
                await channel.send(f"❌ Invalid server URL: {server_url}")
                break
                
            except Exception as connect_error:
                print(f"Error connecting to {server_url}: {connect_error}")
                reconnect_attempts += 1
                if reconnect_attempts <= max_reconnect_attempts:
                    continue
                else:
                    await channel.send(f"❌ Error connecting to {server_url}: {str(connect_error)}")
                    break
                    
            finally:
                # Clean up websocket connection for this attempt
                if websocket:
                    try:
                        await websocket.close()
                    except Exception as close_error:
                        print(f"Error closing websocket: {close_error}")
                    websocket = None
        
        # Final cleanup
        print(f"Websocket listener for {server_url} is exiting")
        if server_url in self.active_connections:
            del self.active_connections[server_url]

    def lookup_item_name(self, game: str, item_id: int) -> str:
        """
        Look up item name from ID using game data.
        
        Tries local datapackage first, then falls back to in-memory game_data.
        """
        return lookup_item_name(game, item_id, self.game_data)
    
    def lookup_location_name(self, game: str, location_id: int) -> str:
        """
        Look up location name from ID using game data.
        
        Tries local datapackage first, then falls back to in-memory game_data.
        """
        return lookup_location_name(game, location_id, self.game_data)
    
    def lookup_player_name(self, player_id: int) -> str:
        """
        Look up player name from ID using connection data.
        
        Tries local datapackage first, then falls back to in-memory connection_data.
        """
        return lookup_player_name(player_id, self.connection_data)
    
    def lookup_player_game(self, player_id: int) -> str:
        """
        Look up player's game from ID using connection data.
        
        Tries local datapackage first, then falls back to in-memory connection_data.
        """
        return lookup_player_game(player_id, self.connection_data)

    async def process_ap_message(self, msg: dict, channel):
        """Process and format Archipelago messages for Discord"""
        cmd = msg.get("cmd", "")
        
        # Debug: Print all received messages to console for troubleshooting
        print(f"AP Message received: {cmd} - {msg}")
        
        if cmd == "Connected":
            # Store connection data for player lookups - use a simpler approach
            # Since we might have multiple servers, store all connection data
            server_key = f"connection_{len(self.connection_data)}"  # Simple key generation
            self.connection_data[server_key] = msg
            print(f"Stored connection data: {msg.get('slot_info', {})}")
            
            players = msg.get("slot_info", {})
            if players:
                player_list = ", ".join([f"{info['name']} ({info['game']})" for info in players.values()])
                await channel.send(f"🎮 **Game Connected**\nPlayers: {player_list}")
            else:
                await channel.send(f"🎮 **Connected to Archipelago server**")
            
        elif cmd == "ConnectionRefused":
            errors = msg.get("errors", ["Unknown error"])
            await channel.send(f"❌ **Connection Refused**: {', '.join(errors)}")
            
        elif cmd == "ReceivedItems":
            items = msg.get("items", [])
            for item in items:
                item_name = item.get("item", "Unknown Item")
                player_name = item.get("player", "Unknown Player")
                await channel.send(f"📦 **{player_name}** received: {item_name}")
                
        elif cmd == "LocationInfo":
            locations = msg.get("locations", [])
            for location in locations:
                location_name = location.get("location", "Unknown Location")
                player_name = location.get("player", "Unknown Player")
                await channel.send(f"📍 **{player_name}** checked: {location_name}")
                
        elif cmd == "PrintJSON":
            # Handle chat messages and game events
            msg_type = msg.get("type", "")
            data = msg.get("data", [])
            
            # Skip chat messages from players
            if msg_type == "Chat":
                print(f"Skipping chat message: {data}")
                return
                
            elif msg_type == "ItemSend":
                # Parse the complex item send message structure
                try:
                    # Extract components from the data array
                    sender_id = None
                    recipient_id = None
                    item_id = None
                    item_flags = None
                    location_id = None
                    
                    for item in data:
                        if item.get("type") == "player_id":
                            if sender_id is None:
                                sender_id = item.get("text")
                            else:
                                recipient_id = item.get("text")
                        elif item.get("type") == "item_id":
                            item_id = item.get("text")
                            item_flags = item.get("flags", 0)
                        elif item.get("type") == "location_id":
                            location_id = item.get("text")
                    
                    # Track location check for progress tracking
                    if sender_id and location_id:
                        sender_id_int = int(sender_id)
                        location_id_int = int(location_id)
                        
                        # Initialize player progress if not exists
                        if sender_id_int not in self.player_progress:
                            self.player_progress[sender_id_int] = set()
                        
                        # Add this location to the player's checked locations
                        self.player_progress[sender_id_int].add(location_id_int)
                        print(f"Tracked location check: Player {sender_id_int} checked location {location_id_int}")
                    
                    # Only send messages for progression items (key items)
                    # Check both item_flags == 1 and item_flags & 1 (bitwise check for progression flag)
                    is_progression = (item_flags == 1) or (item_flags is not None and (item_flags & 1) != 0)
                    
                    if is_progression and sender_id and recipient_id and item_id and location_id:
                        # Debug logging
                        print(f"Processing key ItemSend: sender_id={sender_id}, recipient_id={recipient_id}, item_id={item_id}, item_flags={item_flags}, location_id={location_id}")
                        
                        # Look up actual names using the stored data
                        sender_name = self.lookup_player_name(int(sender_id))
                        recipient_name = self.lookup_player_name(int(recipient_id))
                        
                        # Skip if either player is the Rhelbot tracker
                        if sender_name.lower() == "rhelbot" or recipient_name.lower() == "rhelbot":
                            print(f"Skipping ItemSend involving Rhelbot tracker")
                            return
                        
                        # Check if the recipient player has completed 100% of their locations
                        recipient_id_int = int(recipient_id)
                        
                        # Only perform the completion check if we have save data loaded
                        # Try to load save data if needed
                        save_data = load_apsave_data(self.output_directory, self.AP_DIR)
                        if save_data and self.is_player_completed(recipient_id_int, save_data):
                            print(f"Skipping ItemSend to player {recipient_name} who has completed 100% of locations")
                            return
                            
                        # Get the recipient's game to look up item and location names
                        recipient_game = self.lookup_player_game(int(recipient_id))
                        sender_game = self.lookup_player_game(int(sender_id))
                        
                        # Use recipient's game for item lookup, sender's game for location lookup
                        item_name = self.lookup_item_name(recipient_game, int(item_id))
                        location_name = self.lookup_location_name(sender_game, int(location_id))
                        
                        # Key item emoji
                        item_emoji = "🔑"
                        
                        message = f"{item_emoji} **{sender_name}** sent **{item_name}** to **{recipient_name}**\n📍 From: {location_name}"
                        await channel.send(message)
                    else:
                        # Skip non-key items
                        if item_flags is not None:
                            print(f"Skipping non-key ItemSend (flags={item_flags}) from player {sender_id} to player {recipient_id}")
                        
                except Exception as e:
                    print(f"Error parsing ItemSend message: {e}")
                    
            elif msg_type in ["ItemReceive"]:
                # Skip item receive messages from players
                print(f"Skipping ItemReceive message: {data}")
                return
                    
            elif msg_type in ["Goal", "Release", "Collect", "Countdown"]:
                # Keep important game events but not player-specific ones
                text = "".join([item.get("text", "") for item in data])
                if text:
                    await channel.send(f"🎯 {text}")
                    
            elif msg_type in ["Tutorial", "ServerChat"]:
                # Keep server messages and tutorials but filter out join/leave messages
                text = "".join([item.get("text", "") for item in data])
                if text:
                    # Skip join/leave info messages with comprehensive filtering
                    text_lower = text.lower()
                    join_leave_keywords = [
                        "has joined", "has left", "joined the game", "left the game",
                        "tracking", "client(", "tags:", "connected", "disconnected",
                        "now tracking", "no longer tracking", "syncing", "sync complete",
                        "slot data", "connecting", "connection established", "room join",
                        "player slot", "team #"
                    ]
                    
                    if any(keyword in text_lower for keyword in join_leave_keywords):
                        print(f"Skipping join/leave message: {text}")
                        return
                    
                    await channel.send(f"ℹ️ {text}")
                    
            else:
                # For other message types, check if they're player-related or join/leave before sending
                text = "".join([item.get("text", "") for item in data])
                if text:
                    # Skip messages that appear to be player-related or join/leave messages
                    text_lower = text.lower()
                    filter_keywords = [
                        "player", "sent", "received", "found", "checked", 
                        "has joined", "has left", "joined the game", "left the game",
                        "tracking", "client(", "connected", "disconnected",
                        "now tracking", "no longer tracking", "syncing", "sync complete",
                        "slot data", "connecting", "connection established", "room join",
                        "player slot", "team #", "tags:", "collecting", "collected"
                    ]
                    
                    if any(keyword in text_lower for keyword in filter_keywords):
                        print(f"Skipping filtered message: {text}")
                        return
                    
                    await channel.send(f"ℹ️ {text}")
                
        elif cmd == "RoomUpdate":
            # Handle room/game state updates
            if "players" in msg:
                players = msg["players"]
                online_players = [p["alias"] for p in players if p.get("status", 0) > 0]
                if online_players:
                    await channel.send(f"👥 **Online players**: {', '.join(online_players)}")
                    
        elif cmd == "RoomInfo":
            # Handle room information
            room_info = []
            if "seed_name" in msg:
                room_info.append(f"**Seed**: {msg['seed_name']}")
            if "players" in msg:
                player_count = len(msg["players"])
                room_info.append(f"**Players**: {player_count}")
            if room_info:
                await channel.send(f"🏠 **Room Info**\n" + "\n".join(room_info))
                
        elif cmd == "DataPackage":
            # Handle data package (game information) and store it for lookups
            print(f"Received DataPackage: {msg}")
            games = msg.get("data", {}).get("games", {})
            if games:
                # Store the game data for lookups
                self.game_data = games
                print(f"Stored game data for {len(games)} games: {list(games.keys())}")
                
                # Debug: Show what data we have for each game
                for game_name, game_info in games.items():
                    item_count = len(game_info.get("item_name_to_id", {}))
                    location_count = len(game_info.get("location_name_to_id", {}))
                    print(f"Game '{game_name}': {item_count} items, {location_count} locations")
                
                game_list = list(games.keys())
                await channel.send(f"🎲 **Available games**: {', '.join(game_list[:10])}" + 
                                ("..." if len(game_list) > 10 else ""))
            else:
                print("DataPackage received but no games data found")
        
        # Handle any other message types by showing the command type
        elif cmd and cmd not in ["Bounced"]:  # Bounced messages are just echoes, ignore them
            await channel.send(f"📨 **{cmd}**: {str(msg)[:200]}{'...' if len(str(msg)) > 200 else ''}")

    @app_commands.command(
        name="track",
        description="Start tracking an Archipelago server and forward messages to a Discord channel",
    )
    @app_commands.describe(
        server_url="Archipelago server websocket URL (e.g., ws://ap.rhelys.com:38281)",
        channel_id="Discord channel ID to send messages to",
        password="Optional server password. Enter 'null' if no password is needed (default: read from server_password.txt)"
    )
    async def ap_track(
        self, 
        interaction: discord.Interaction, 
        server_url: Optional[str], 
        channel_id: Optional[str],
        password: Optional[str] = None
    ):
        await interaction.response.defer()
        
        if not server_url:
            server_url = "ws://ap.rhelys.com:38281"  # Default server URL
            try:
                from helpers.server_helpers import get_server_password
                password = get_server_password()  # Read password from file
            except Exception as e:
                await interaction.followup.send(f"❌ Server password error: {str(e)}")
                return

        if not channel_id:
            channel_id = str(interaction.channel.id)  # Default to current channel
        
        if password == "null":
            password = None  # Convert "null" string to None

        # Validate channel ID
        try:
            channel_id_int = int(channel_id)
            target_channel = self.bot.get_channel(channel_id_int)
            if not target_channel:
                await interaction.followup.send(f"❌ Could not find channel with ID: {channel_id}")
                return
        except ValueError:
            await interaction.followup.send(f"❌ Invalid channel ID: {channel_id}")
            return
        
        # Check if already tracking this server
        if server_url in self.active_connections:
            await interaction.followup.send(f"❌ Already tracking server: {server_url}")
            return
        
        # Validate websocket URL format
        if not server_url.startswith(("ws://", "wss://")):
            await interaction.followup.send(f"❌ Invalid websocket URL. Must start with ws:// or wss://")
            return
        
        # Check if we already have a datapackage available
        from helpers.data_helpers import is_datapackage_available, fetch_and_save_datapackage
        have_datapackage = is_datapackage_available()
        
        # If not, fetch and save it for faster lookups during tracking
        if not have_datapackage:
            await interaction.followup.send("📦 Fetching datapackage for local caching...")
            
            try:
                datapackage_success = fetch_and_save_datapackage(server_url, password)
                if datapackage_success:
                    logger.info(f"Successfully cached datapackage for tracking {server_url}")
                else:
                    logger.warning(f"Failed to cache datapackage for tracking {server_url}")
            except Exception as dp_error:
                logger.error(f"Error caching datapackage for tracking: {dp_error}")
        
        # Start the websocket listener task
        task = asyncio.create_task(self.websocket_listener(server_url, channel_id_int, password))
        
        # Track the connection
        self.active_connections[server_url] = {
            "task": task,
            "channel_id": channel_id_int,
            "websocket": None
        }
        
        await interaction.followup.send(
            f"✅ Started tracking Archipelago server: {server_url}\n"
            f"Messages will be sent to: {target_channel.mention}"
        )

    @app_commands.command(
        name="untrack",
        description="Stop tracking an Archipelago server",
    )
    @app_commands.describe(server_url="Archipelago server URL to stop tracking")
    async def ap_untrack(self, interaction: discord.Interaction, server_url: Optional[str]):
        await interaction.response.defer()

        if not server_url:
            server_url = "ws://ap.rhelys.com:38281"  # Default server URL
        
        if server_url not in self.active_connections:
            await interaction.followup.send(f"❌ Not currently tracking server: {server_url}")
            return
        
        # Cancel the task and close websocket
        connection = self.active_connections[server_url]
        
        # Cancel the background task first
        connection["task"].cancel()
        
        # Close the websocket if it exists
        if connection["websocket"]:
            try:
                await connection["websocket"].close()
            except Exception as e:
                print(f"Error closing websocket: {e}")
        
        # Remove from tracking
        del self.active_connections[server_url]
        
        await interaction.followup.send(f"✅ Stopped tracking server: {server_url}")

    @app_commands.command(
        name="tracked",
        description="List all currently tracked Archipelago servers",
    )
    async def ap_tracked(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        if not self.active_connections:
            await interaction.followup.send("📭 No servers are currently being tracked.")
            return
        
        tracked_list = []
        for server_url, connection in self.active_connections.items():
            channel = self.bot.get_channel(connection["channel_id"])
            channel_name = channel.mention if channel else f"Unknown Channel ({connection['channel_id']})"
            status = "🟢 Connected" if connection["websocket"] else "🟡 Connecting"
            tracked_list.append(f"• {server_url} → {channel_name} {status}")
        
        embed = discord.Embed(
            title="📡 Tracked Archipelago Servers",
            description="\n".join(tracked_list),
            color=0x00ff00
        )
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="addgame",
        description="Adds a new .apworld file to the server for future games",
    )
    @app_commands.describe(
        apworld="Valid .apworld for a new game that the server doesn't support"
    )
    async def ap_add_game(
        self, interaction: discord.Interaction, apworld: discord.Attachment
    ):
        await interaction.response.defer()

        if not apworld.filename.endswith(".apworld"):
            await interaction.followup.send(
                "Not a .apworld file. Make sure you've selected the right file for upload"
            )
            return

        await apworld.save(f"{self.ap_directory}/custom_worlds/{apworld.filename}")
        await interaction.followup.send(
            "File added. If your game requires a patch file to be generated then work with the server owner to ensure "
            "your game will generate successfully"
        )

    """
    /ap join -  Takes in a yaml file from a discord member, checks to make sure it's a valid file and that we're not
                overwriting an existing valid file for a player, and responds back to the channel saying that the file
                has been uploaded
                
    Parameters: discord.File object as required from the command
                
    Function workflow:
                1: Validate that the file is a .yaml file, otherwise the generator won't accept it and will error out
                    1 - Exit: If the file is not a .yaml file, return a message to the user
                2: Save the submitted .yaml file 
                3: Read the players text file for currently used slot names
                4: Read the .yaml file and compare the slot name against the player list
                    4 - Exit: If the name matches, send an error back and recommend /ap leave to open the slot name
                              and delete the file initially saved
                5: Add the slot name to the player list and return a success to the user 
    """
    # Todo - Add validation for the player name prior to adding to the game_status file to ensure it will lint
    @app_commands.command(
        name="join",
        description="Adds your yaml config file to the pending Archipelago game",
    )
    @app_commands.describe(
        playerfile="Your .yaml file. Must be for a unique player slot or an edit on your existing file"
    )
    async def ap_join(
        self, interaction: discord.Interaction, playerfile: discord.Attachment
    ) -> None:
        await interaction.response.defer()

        if not playerfile.filename.endswith(".yaml"):
            await interaction.followup.send(
                f"File supplied is not a yaml file. Check that you uploaded the "
                f"correct file and try again"
            )
            return

        await interaction.followup.send("Processing file")

        filepath = f"./Archipelago/players/{playerfile.filename}"
        await playerfile.save(filepath)

        try:
            # Parse the YAML file to get player name and game
            with open(filepath, "r", encoding="utf-8") as playeryaml:
                yaml_object = YAML(typ="safe", pure=True)
                raw_data = yaml_object.load_all(playeryaml)
                data_list = list(raw_data)

                player_name = None
                game_name = None
                
                # Extract player name and game from the YAML
                for element in data_list:
                    player_name = element.get("name")
                    game_name = element.get("game")
                    break

                if not player_name:
                    await interaction.channel.send("YAML file must contain a player 'name' field.")
                    remove(filepath)
                    return

                # Disallow "me" as a player name
                if player_name.lower() == "me":
                    await interaction.channel.send("Player name cannot be 'me'. Please choose a different name.")
                    remove(filepath)
                    return

            # Load or create game status
            status_file = "game_status.json"
            if os.path.exists(status_file):
                try:
                    with open(status_file, 'r') as f:
                        game_status = json.load(f)
                except (json.JSONDecodeError, IOError):
                    game_status = {"players": {}, "discord_users": {}}
            else:
                game_status = {"players": {}, "discord_users": {}}

            # Ensure required keys exist
            if "players" not in game_status:
                game_status["players"] = {}
            if "discord_users" not in game_status:
                game_status["discord_users"] = {}

            # Check if player already exists
            if player_name in game_status["players"]:
                existing_discord_user = None
                # Find which Discord user owns this player
                for discord_id, mapped_player in game_status["discord_users"].items():
                    if mapped_player == player_name:
                        existing_discord_user = discord_id
                        break
                
                # If the same Discord user is updating their file, allow it
                if existing_discord_user == str(interaction.user.id):
                    game_status["players"][player_name] = {
                        "filepath": filepath,
                        "game": game_name,
                        "joined_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat()
                    }
                    
                    # Save updated game status
                    with open(status_file, 'w') as f:
                        json.dump(game_status, f, indent=2)
                    
                    # Set the player and game class variables before calling upload_success
                    self.player = player_name
                    self.game = game_name
                    
                    await self.upload_success(filepath, interaction)
                else:
                    await interaction.channel.send(
                        f"{player_name} already exists and belongs to another user. "
                        "Choose a different player name."
                    )
                    remove(filepath)
                    return
            else:
                # New player - add to game status
                game_status["players"][player_name] = {
                    "filepath": filepath,
                    "game": game_name,
                    "joined_at": datetime.now().isoformat()
                }
                
                # Record Discord user to player mapping
                user_id_str = str(interaction.user.id)
                if user_id_str not in game_status["discord_users"]:
                    game_status["discord_users"][user_id_str] = []
                
                # Add the new player to the user's list of players if not already there
                if player_name not in game_status["discord_users"][user_id_str]:
                    game_status["discord_users"][user_id_str].append(player_name)
                
                # Save updated game status
                with open(status_file, 'w') as f:
                    json.dump(game_status, f, indent=2)
                
                # Set the player and game class variables before calling upload_success
                self.player = player_name
                self.game = game_name
                
                await self.upload_success(filepath, interaction)

        except Exception as e:
            await interaction.channel.send(f"Error processing YAML file: {str(e)}")
            if os.path.exists(filepath):
                remove(filepath)

    """
    /ap start - Generates the game files from uploaded player files and then starts the server. Optionally
                takes in a pre-generated file to start up.
                
    Parameters: [Optional] apfile: Generated .zip file from Archipelago to start with the server
    
    """

    # Todo - pull the server password from the host.yaml file instead of hardcoding it here

    @app_commands.command(
        name="start",
        description="Starts the game. Either generates or takes an optional "
        "pre-generated game file.",
    )
    @app_commands.describe(apfile="Pre-generated zip file for an Archipelago game")
    async def ap_start(
        self, interaction: discord.Interaction, apfile: Optional[discord.Attachment]
    ) -> None:
        await interaction.response.send_message(
            "Attempting to start Archipelago server, hold please...\nError messages will be sent to this channel"
        )

        # Clean up existing files - this is a port from the update command later in the file
        def outputfiles():
            return listdir(self.output_directory)

        for file in outputfiles():
            remove(f"{self.output_directory}/{file}")
            
        # Delete any existing datapackage to ensure clean start
        from helpers.data_helpers import delete_local_datapackage
        delete_local_datapackage()
        logger.info("Deleted local datapackage before server start")

        # Todo - add error handling for submitted file to make sure it includes a .archipelago file
        if apfile:
            if apfile.filename.endswith(".zip"):
                await apfile.save(f"{self.output_directory}/donkey.zip")
            else:
                await interaction.edit_original_response(
                    content="File submitted was not a .zip file. Submit a valid "
                    "generated Archipelago game file"
                )
                return
        else:
            # Setting up the bot tracking capability
            try:
                if path.exists("./rhelbot.yaml"):
                    shutil.copy("./rhelbot.yaml", "./Archipelago/players/rhelbot.yaml")
                    print("Successfully copied rhelbot.yaml to players directory")
                else:
                    print("Warning: rhelbot.yaml not found, skipping copy")
            except Exception as e:
                print(f"Error copying rhelbot.yaml: {e}")
                await interaction.edit_original_response(
                    content=f"Warning: Could not copy rhelbot.yaml: {str(e)}"
                )
                return
            
            # Start the generation process and capture output for error detection
            import time
            import asyncio
            start_time = time.time()

            # Run generation with both interactive window and output capture
            # First, start the interactive window for user visibility
            interactive_process = subprocess.Popen([
                "cmd", "/c", "start", "cmd", "/k",
                "python", "./Archipelago/Generate.py"
            ], shell=True)

            # Also run a background process to capture output for error detection
            # Use a temporary directory to avoid duplicate outputs
            import tempfile
            temp_dir = tempfile.mkdtemp()

            monitoring_process = subprocess.Popen([
                "python", "./Archipelago/Generate.py", "--outputpath", temp_dir
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=".")

            print(f"Started generation processes - Interactive PID: {interactive_process.pid}, Monitoring PID: {monitoring_process.pid}")

            generation_timeout = 1200  # 20 minutes timeout
            check_interval = 5  # Check every 5 seconds for faster error detection
            elapsed_time = 0

            await interaction.edit_original_response(
                content="🔄 Generation running in interactive window... Monitoring for completion and errors..."
            )

            while elapsed_time < generation_timeout:
                await sleep(check_interval)
                elapsed_time += check_interval

                # Check if monitoring process has finished or has output
                poll_result = monitoring_process.poll()
                if poll_result is not None:
                    # Process has finished, check the result
                    stdout, stderr = monitoring_process.communicate()

                    if poll_result == 0:
                        # Success - break and continue with file checking
                        print("Generation process completed successfully")
                        break
                    else:
                        # Error occurred - send error details to Discord
                        error_message = "❌ Generation failed with errors:\n"
                        if stderr:
                            # Limit error message length for Discord
                            error_text = stderr.strip()
                            if len(error_text) > 1500:
                                error_text = error_text[:1500] + "...\n(truncated)"
                            error_message += f"```\n{error_text}\n```"
                        elif stdout:
                            # Sometimes errors appear in stdout
                            output_text = stdout.strip()
                            if "error" in output_text.lower() or "exception" in output_text.lower():
                                if len(output_text) > 1500:
                                    output_text = output_text[:1500] + "...\n(truncated)"
                                error_message += f"```\n{output_text}\n```"
                            else:
                                error_message += "Process exited with error code but no error details captured."
                        else:
                            error_message += "Process exited with error code but no error details captured."

                        await interaction.edit_original_response(content=error_message)
                        return

                # Check if generation has produced output files (alternative success detection)
                try:
                    output_files = listdir(self.output_directory)
                    zip_files = [f for f in output_files if f.endswith('.zip')]

                    if zip_files:
                        # Generation completed successfully - terminate monitoring process
                        try:
                            monitoring_process.terminate()
                        except:
                            pass
                        break

                    # Update progress every 60 seconds
                    if elapsed_time % 60 == 0:
                        minutes = elapsed_time // 60
                        await interaction.edit_original_response(
                            content=f"🔄 Generation running in interactive window... ({minutes}m elapsed)"
                        )

                except Exception as e:
                    print(f"Error checking output files: {e}")
                    continue

            # Clean up monitoring process if still running
            try:
                if monitoring_process.poll() is None:
                    monitoring_process.terminate()
                    monitoring_process.wait(timeout=5)
            except:
                pass

            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass

            # Final check for output files
            try:
                output_files = listdir(self.output_directory)
                zip_files = [f for f in output_files if f.endswith('.zip')]
            except Exception as e:
                print(f"Error in final file check: {e}")
                zip_files = []

            if not zip_files:
                if elapsed_time >= generation_timeout:
                    await interaction.edit_original_response(
                        content="❌ Generation timed out after 20 minutes. Check the generation window for details or look for error messages above."
                    )
                else:
                    await interaction.edit_original_response(
                        content="❌ Generation failed - no output file was created. Check the generation window for details or look for error messages above."
                    )
                return
            
            # Show final generation completion time
            final_time = time.time()
            total_elapsed = final_time - start_time
            total_minutes = int(total_elapsed // 60)
            total_seconds = int(total_elapsed % 60)
            
            if total_minutes > 0:
                final_time_str = f"{total_minutes}m {total_seconds}s"
            else:
                final_time_str = f"{total_seconds}s"
            
            await interaction.edit_original_response(
                content=f"✅ Generation completed in {final_time_str}! Starting server..."
            )

        outputfile = listdir(self.output_directory)

        # Todo - Refactor this to not be an absolute reference to the first object, just in case
        rename(
            f"{self.output_directory}{outputfile[0]}",
            f"{self.output_directory}/donkey.zip",
        )
        
        # Start the server and track the process
        self.server_process = subprocess.Popen([r"serverstart.bat"])
        print(f"Started server process with PID: {self.server_process.pid}")
        await sleep(8)

        # Keep the server started message simple to avoid character limit issues
        try:
            from helpers.server_helpers import get_server_password
            server_password = get_server_password()
            server_message = f"Archipelago server started.\nServer: ap.rhelys.com\nPort: 38281\nPassword: {server_password}"
            await interaction.edit_original_response(content=server_message)
            
            # After server is started, fetch and save the datapackage
            try:
                from helpers.data_helpers import fetch_and_save_datapackage
                import asyncio
                
                # Give the server a moment to fully initialize
                await asyncio.sleep(5)
                
                # Use await with the async function
                success = await fetch_and_save_datapackage("ws://ap.rhelys.com:38281", server_password)
                if success:
                    logger.info("Successfully saved datapackage after server start")
                else:
                    logger.warning("Failed to save datapackage after server start")
            except Exception as dp_error:
                logger.error(f"Error saving datapackage after server start: {dp_error}")
                
        except Exception as e:
            await interaction.edit_original_response(
                content=f"✅ Archipelago server started.\n❌ Server password error: {str(e)}\n"
                        "Server: ap.rhelys.com\nPort: 38281"
            )

        with zipfile.ZipFile(
            f"{self.output_directory}/donkey.zip", mode="r"
        ) as gamefile:
            for file in gamefile.namelist():
                if not file.endswith(tuple(self.system_extensions)):
                    gamefile.extract(file, f"{self.output_directory}")
        gamefile.close()

        finaloutputlist = listdir(self.output_directory)

        for dirfile in finaloutputlist:
            if dirfile == "donkey.zip":
                print("Skipping output zip")
            elif not dirfile.endswith(tuple(self.system_extensions)) and path.isfile(
                f"{self.output_directory}/{dirfile}"
            ):
                with open(f"{self.output_directory}/{dirfile}", "rb") as f:
                    await interaction.followup.send(
                        file=discord.File(f, filename=dirfile)
                    )

    """
    /ap cleanup  - 
    
    
     
    """

    # Todo - add in a "safe" cleanup option to remove everything but what's running
    # Todo - Disabling this for now and putting it into the startup function for ease of use.
    # I'll come back to this once I have a need to run multiple servers at once
    """
    @app_commands.command(
        name="cleanup",
        description="Cleans up the output and player files from the last game",
    )
    async def ap_cleanup(self, interaction: discord.Interaction):
        def outputfiles():
            return listdir(self.output_directory)

        for file in outputfiles():
            remove(f"{self.output_directory}/{file}")

        await interaction.response.send_message("File cleanup complete", ephemeral=True)
        # Todo - Store player files somewhere with the date of the game and remove them for next generation
    """

    def _get_output_files(self):
        """Helper method to get list of files in output directory"""
        return listdir(self.output_directory)

    def _cleanup_output_files(self):
        """Helper method to clean up all files in output directory"""
        for file in self._get_output_files():
            remove(f"{self.output_directory}/{file}")

    async def _kill_server_processes(self):
        """Helper method to kill running Archipelago server processes"""
        killed_processes = []
        
        try:
            import psutil
            
            # Find and kill the MultiServer.py process
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    # Check if this is a Python process running MultiServer.py
                    if (proc.info['name'] and 'python' in proc.info['name'].lower() and 
                        proc.info['cmdline'] and any('MultiServer.py' in arg for arg in proc.info['cmdline'])):
                        
                        print(f"Found MultiServer process: PID {proc.info['pid']}")
                        proc.kill()
                        killed_processes.append(proc.info['pid'])
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            # Also try to terminate the tracked server process if it exists
            if self.server_process:
                try:
                    # Kill the batch file process and its children
                    parent = psutil.Process(self.server_process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                    killed_processes.append(self.server_process.pid)
                    self.server_process = None
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
        except ImportError:
            # Fallback method using taskkill on Windows
            try:
                subprocess.run([
                    "taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq *MultiServer*"
                ], capture_output=True, text=True)
            except Exception as e:
                print(f"Error stopping server: {e}")
        
        return killed_processes

    @app_commands.command(
        name="spoiler", description="Pulls the spoiler log from the current game"
    )
    async def ap_spoiler(self, interaction: discord.Interaction):
        await interaction.response.defer()

        for file in self._get_output_files():
            if file.endswith(".zip"):
                with zipfile.ZipFile(
                    f"{self.output_directory}/donkey.zip", mode="r"
                ) as gamefile:
                    for zipped_file in gamefile.namelist():
                        if zipped_file.endswith("Spoiler.txt"):
                            gamefile.extract(zipped_file, self.output_directory)
                            await sleep(3)
                            await interaction.followup.send(
                                "Spoiler file for current game"
                            )

        for endfile in self._get_output_files():
            if endfile.endswith("Spoiler.txt"):
                with open(f"{self.output_directory}/{endfile}", "rb") as sendfile:
                    await interaction.channel.send(
                        file=discord.File(sendfile, filename="Spoiler.txt")
                    )

    @app_commands.command(
        name="status",
        description="Gets the status and players of the current or pending game",
    )
    async def ap_status(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Check if server is running
        server_running = False
        server_pid = None
        
        try:
            import psutil
            
            # Check for MultiServer.py processes
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if (proc.info['name'] and 'python' in proc.info['name'].lower() and 
                        proc.info['cmdline'] and any('MultiServer.py' in arg for arg in proc.info['cmdline'])):
                        server_running = True
                        server_pid = proc.info['pid']
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
                    
        except ImportError:
            # Fallback: check if tracked process is still running
            if self.server_process:
                try:
                    # Check if process is still running
                    if self.server_process.poll() is None:
                        server_running = True
                        server_pid = self.server_process.pid
                except:
                    pass

        # Get current players
        try:
            current_players = self.list_players()
        except (AttributeError, FileNotFoundError):
            current_players = {}

        # Build status message
        status_parts = []
        
        # Server status
        if server_running:
            status_parts.append(f"🟢 **Server Status**: Running (PID: {server_pid})")
            status_parts.append("📡 **Connection**: ap.rhelys.com:38281")
        else:
            status_parts.append("🔴 **Server Status**: Not running")
        
        # Player status
        if current_players:
            playerlist = list(current_players.keys())
            status_parts.append(f"👥 **Current Players**: {', '.join(playerlist)}")
        else:
            status_parts.append("👥 **Current Players**: None")
        
        # Game file status
        if path.exists(f"{self.output_directory}/donkey.zip"):
            status_parts.append("📁 **Game File**: Ready (donkey.zip)")
        else:
            status_parts.append("📁 **Game File**: Not found")

        await interaction.followup.send("\n".join(status_parts))

    @app_commands.command(
        name="leave",
        description="Deletes player's file from the staged files. Use 'me' to remove yourself.",
    )
    @app_commands.describe(player="Removes yaml file for selected player from the game or use 'me' to remove yourself")
    async def ap_leave(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer()

        # Resolve "me" to actual player name
        resolved_name = self.resolve_player_name(interaction.user.id, player)
        if resolved_name is None and player.lower() == "me":
            await interaction.followup.send("You haven't joined the game yet.")
            return
        elif resolved_name is None:
            resolved_name = player

        # Load game status
        status_file = "game_status.json"
        if not os.path.exists(status_file):
            await interaction.followup.send("No game status found.")
            return

        try:
            with open(status_file, 'r') as f:
                game_status = json.load(f)
        except (json.JSONDecodeError, IOError):
            await interaction.followup.send("Error reading game status file.")
            return

        players = game_status.get("players", {})
        discord_users = game_status.get("discord_users", {})

        if player.lower() == "all":
            # Remove all player files
            for player_name, player_info in players.items():
                filepath = player_info.get("filepath")
                if filepath and os.path.exists(filepath):
                    remove(filepath)

            # Clear the game status
            game_status = {"players": {}, "discord_users": {}}
            
            with open(status_file, 'w') as f:
                json.dump(game_status, f, indent=2)

            await interaction.followup.send("All player files have been deleted")
            return

        # Remove specific player
        if resolved_name in players:
            player_info = players[resolved_name]
            filepath = player_info.get("filepath")
            
            # Remove the player file if it exists
            if filepath and os.path.exists(filepath):
                remove(filepath)
            
            # Remove from players
            del players[resolved_name]
            
            # Remove from discord_users mapping
            for user_id, player_list in discord_users.items():
                if isinstance(player_list, list) and resolved_name in player_list:
                    player_list.remove(resolved_name)
                    # If the user has no more players, remove their entry entirely
                    if not player_list:
                        del discord_users[user_id]
                    break
                elif not isinstance(player_list, list) and player_list == resolved_name:
                    # Handle legacy single-player format (backwards compatibility)
                    del discord_users[user_id]
                    break
            
            # Save updated game status
            with open(status_file, 'w') as f:
                json.dump(game_status, f, indent=2)
            
            display_name = "You have" if player.lower() == "me" else f"Player '{resolved_name}' has"
            await interaction.followup.send(f"{display_name} left the game.")
        else:
            display_name = "you are" if player.lower() == "me" else f"player '{resolved_name}' is"
            await interaction.followup.send(f"Player not found - {display_name} not in the game.")

    @app_commands.command(
        name="stop",
        description="Stops the currently running Archipelago server",
    )
    async def ap_stop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            import psutil
            import os
            
            # Find and kill the MultiServer.py process
            killed_processes = []
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    # Check if this is a Python process running MultiServer.py
                    if (proc.info['name'] and 'python' in proc.info['name'].lower() and 
                        proc.info['cmdline'] and any('MultiServer.py' in arg for arg in proc.info['cmdline'])):
                        
                        print(f"Found MultiServer process: PID {proc.info['pid']}, CMD: {' '.join(proc.info['cmdline'])}")
                        proc.kill()
                        killed_processes.append(proc.info['pid'])
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            # Also try to terminate the tracked server process if it exists
            if self.server_process:
                try:
                    # Kill the batch file process and its children
                    parent = psutil.Process(self.server_process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                    killed_processes.append(self.server_process.pid)
                    self.server_process = None
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            if killed_processes:
                # Delete the local datapackage when server is stopped
                try:
                    from helpers.data_helpers import delete_local_datapackage
                    delete_success = delete_local_datapackage()
                    datapackage_message = "\nDatapackage cleaned up successfully." if delete_success else ""
                    logger.info(f"Deleted datapackage on server stop: {delete_success}")
                except Exception as dp_error:
                    datapackage_message = f"\nWarning: Failed to clean up datapackage: {str(dp_error)}"
                    logger.error(f"Error deleting datapackage on server stop: {dp_error}")
                
                await interaction.followup.send(
                    f"✅ Successfully stopped Archipelago server.\n"
                    f"Killed processes: {', '.join(map(str, killed_processes))}{datapackage_message}"
                )
            else:
                await interaction.followup.send("❌ No running Archipelago server found.")
                
        except ImportError:
            # Fallback method using taskkill on Windows
            try:
                # Kill any Python processes running MultiServer.py
                result = subprocess.run([
                    "taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq *MultiServer*"
                ], capture_output=True, text=True)
                
                if result.returncode == 0:
                    await interaction.followup.send("✅ Successfully stopped Archipelago server using taskkill.")
                else:
                    await interaction.followup.send("❌ No running Archipelago server found or failed to stop.")
                    
            except Exception as e:
                await interaction.followup.send(f"❌ Error stopping server: {str(e)}")

    @app_commands.command(
        name="restart",
        description="Restarts the Archipelago server using the existing game file (no generation)",
    )
    async def ap_restart(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Check if game file exists
        if not path.exists(f"{self.output_directory}/donkey.zip"):
            await interaction.followup.send(
                "❌ No game file found (donkey.zip). Use `/ap start` to generate and start a new game first."
            )
            return
        
        await interaction.followup.send("🔄 Restarting Archipelago server...")
        
        # Stop any running server first
        try:
            import psutil
            
            # Find and kill the MultiServer.py process
            killed_processes = []
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    # Check if this is a Python process running MultiServer.py
                    if (proc.info['name'] and 'python' in proc.info['name'].lower() and 
                        proc.info['cmdline'] and any('MultiServer.py' in arg for arg in proc.info['cmdline'])):
                        
                        print(f"Stopping MultiServer process: PID {proc.info['pid']}")
                        proc.kill()
                        killed_processes.append(proc.info['pid'])
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            # Also try to terminate the tracked server process if it exists
            if self.server_process:
                try:
                    # Kill the batch file process and its children
                    parent = psutil.Process(self.server_process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                    killed_processes.append(self.server_process.pid)
                    self.server_process = None
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            if killed_processes:
                print(f"Stopped processes: {killed_processes}")
                await sleep(2)  # Give processes time to fully terminate
                
        except ImportError:
            # Fallback method using taskkill on Windows
            try:
                subprocess.run([
                    "taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq *MultiServer*"
                ], capture_output=True, text=True)
                await sleep(2)
            except Exception as e:
                print(f"Error stopping server: {e}")
        
        # Start the server with existing game file
        try:
            self.server_process = subprocess.Popen([r"serverstart.bat"])
            print(f"Restarted server process with PID: {self.server_process.pid}")
            await sleep(8)  # Give server time to start
            
            try:
                from helpers.server_helpers import get_server_password
                server_password = get_server_password()
                restart_message = "✅ Archipelago server restarted successfully!\n" \
                                 f"Server: ap.rhelys.com\nPort: 38281\nPassword: {server_password}"
                
                # After server is restarted, fetch and save a fresh datapackage
                try:
                    from helpers.data_helpers import fetch_and_save_datapackage
                    import asyncio
                    
                    # Give the server a moment to fully initialize
                    await asyncio.sleep(5)
                    
                    # Use await with the async function
                    success = await fetch_and_save_datapackage("ws://ap.rhelys.com:38281", server_password)
                    if success:
                        logger.info("Successfully saved datapackage after server restart")
                    else:
                        logger.warning("Failed to save datapackage after server restart")
                        restart_message += "\nWarning: Failed to refresh datapackage."
                except Exception as dp_error:
                    logger.error(f"Error saving datapackage after server restart: {dp_error}")
                    restart_message += f"\nWarning: Error refreshing datapackage: {str(dp_error)}"
                
                await interaction.edit_original_response(content=restart_message)
            except Exception as e:
                await interaction.edit_original_response(
                    content="✅ Archipelago server restarted successfully!\n"
                            f"❌ Server password error: {str(e)}\n"
                            "Server: ap.rhelys.com\nPort: 38281"
                )
            
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ Failed to restart server: {str(e)}"
            )

    @app_commands.command(
        name="progress",
        description="Shows location check progress for all players in the current game. Use 'me' for your own progress.",
    )
    @app_commands.describe(player="Optional: Show progress for a specific player or use 'me' for your own progress")
    async def ap_progress(self, interaction: discord.Interaction, player: Optional[str] = None):
        await interaction.response.defer()
        
        # Check if server is running first
        server_running = self.is_server_running()

        if not server_running:
            await interaction.followup.send("❌ Archipelago server is not running. Use `/ap start` to start the server first.")
            return

        # Resolve player reference if provided
        original_player = None
        target_players = None
        if player:
            original_player = player
            resolved_player = self.resolve_player_name(interaction.user.id, player)
            if resolved_player is None and player.lower() == "me":
                await interaction.followup.send("❌ You haven't joined the game yet. Use `/ap join` first.")
                return
            elif isinstance(resolved_player, list):
                # If multiple players are found, show all of them
                target_players = resolved_player
                await interaction.followup.send(f"ℹ️ Showing progress for all your players: {', '.join(resolved_player)}")
            else:
                target_players = [resolved_player]

        # Check if we have active connection data that indicates a current game is running
        has_active_connection = bool(self.connection_data and self.game_data and self.player_progress)

        # Load save data, but we'll validate it against the active connection if available
        save_data = load_apsave_data(self.output_directory, self.ap_directory)
        if not save_data:
            await interaction.followup.send("❌ Could not load save data. Make sure the Archipelago server has a save file.")
            return

        # If we have an active connection but the save file might be from a different game,
        # warn the user and prioritize live tracking data over save file location data
        if has_active_connection:
            print("Using live tracking data from active WebSocket connection, supplemented by save file structure")
        else:
            print("Using save file data - no active connection detected")
            # When there's no active tracking, we can't be sure the save file is from the current game
            # Try to validate by checking if the save file is recent relative to server start
            from pathlib import Path
            import time

            output_path = Path(self.output_directory)
            apsave_files = list(output_path.glob("*.apsave"))

            if apsave_files:
                most_recent_save = max(apsave_files, key=lambda f: f.stat().st_mtime)
                save_age_minutes = (time.time() - most_recent_save.stat().st_mtime) / 60

                # If the save file is more than 30 minutes old, warn the user
                if save_age_minutes > 30:
                    await interaction.edit_original_response(
                        content=f"⚠️ **Warning**: Using save file data that is {save_age_minutes:.0f} minutes old. "
                        f"This may not reflect the current game session.\n\n"
                    )
        
        # Set flag for specific player filtering
        show_specific_players = (target_players is not None)
        
        # Get player and game data from save file or connection data
        all_players = {}
        game_data = {}
        
        # First try to get data from active websocket connection
        if self.connection_data and self.game_data:
            for server_key, conn_data in self.connection_data.items():
                slot_info = conn_data.get("slot_info", {})
                for slot_id, player_info in slot_info.items():
                    player_id = int(slot_id)
                    all_players[player_id] = {
                        "name": player_info.get("name", f"Player {player_id}"),
                        "game": player_info.get("game", "Unknown")
                    }
            game_data = self.game_data
        else:
            # If no websocket connection, connect to server to get current game data and validate against save file
            await interaction.edit_original_response(content="📡 Connecting to server to get current game data...")

            server_data = await self.fetch_server_data()
            if server_data:
                all_players = server_data["players"]
                game_data = server_data["game_data"]

                # Validate that current server players match save file players
                save_players = set()
                for (team, slot), locations in save_data.get("location_checks", {}).items():
                    if team == 0:  # Assuming team 0
                        save_players.add(slot)

                current_players = set(all_players.keys())

                # If players don't match, warn about potential mismatch
                if save_players and current_players and not save_players.intersection(current_players):
                    await interaction.edit_original_response(
                        content="⚠️ **Warning**: Save file players don't match current server players. "
                        f"This save file appears to be from a different game session.\n\n"
                    )

                # Store temporarily for the location counting function to use
                self._temp_player_data = all_players
                self._temp_game_data = game_data
            else:
                # Fallback: try to extract basic data from save file
                all_players, game_data = self.extract_player_data_from_save(save_data)
        
        if not all_players:
            await interaction.followup.send("❌ No players found in the current game.")
            return
        
        progress_lines = []

        # Add data source indicator to help users understand reliability
        if has_active_connection:
            progress_lines.append("📊 **Player Progress Report** (Live Tracking)\n")
        else:
            progress_lines.append("📊 **Player Progress Report** (Save File Data)\n")
        
        # Get location checks from save data
        location_checks = save_data.get("location_checks", {})

        # Check for potential save file mismatch if we have active connection data
        if has_active_connection and all_players:
            # Check if the players in the connection match those in the save file
            save_players = set()
            for (team, slot), locations in location_checks.items():
                if team == 0:  # Assuming team 0
                    save_players.add(slot)

            connection_players = set(all_players.keys())

            # If there's a significant mismatch, warn that data might be from different games
            if save_players and not save_players.intersection(connection_players):
                await interaction.edit_original_response(
                    content="⚠️ **Warning**: Save file appears to be from a different game session than currently running. Showing live tracking data where available.\n\n"
                )

        # Merge with real-time tracking data for most up-to-date information
        # This ensures we show the latest location checks even if the save file hasn't been updated yet
        for player_id, real_time_locations in self.player_progress.items():
            # Get the current save data for this player
            save_locations = location_checks.get((0, player_id), set())

            # Merge real-time data with save data (union of both sets)
            merged_locations = save_locations.union(real_time_locations)
            location_checks[(0, player_id)] = merged_locations
        
        # Get client activity timers from save data
        client_activity_timers = save_data.get("client_activity_timers", ())
        
        # Convert client_activity_timers to a dictionary for easier lookup
        activity_timer_dict = {}
        if isinstance(client_activity_timers, (list, tuple)):
            for entry in client_activity_timers:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    player_key, timestamp = entry[0], entry[1]
                    if isinstance(player_key, (list, tuple)) and len(player_key) >= 2:
                        team, slot = player_key[0], player_key[1]
                        activity_timer_dict[(team, slot)] = timestamp
        
        # Filter out "Rhelbot" and create a list for sorting
        player_progress_data = []
        
        for player_id, player_info in all_players.items():
            player_name = player_info["name"]
            player_game = player_info["game"]
            
            # Skip the Rhelbot tracker
            if player_name.lower() == "rhelbot":
                continue
                
            # Filter for specific players if requested
            if show_specific_players and player_name not in target_players:
                continue
            
            # Get checked locations for this player from save data
            # location_checks format: {(team, slot): set of location_ids}
            checked_locations = location_checks.get((0, player_id), set())  # Assuming team 0
            checked_count = len(checked_locations)
            
            # Get total locations for this player from the actual multiworld data
            total_locations = get_player_total_locations(player_id, save_data)
            
            # Calculate percentage
            if total_locations > 0:
                percentage = (checked_count / total_locations) * 100
                is_complete = percentage >= 100.0
                
                # Add checkmark for 100% completion after the game name
                completion_indicator = " ✅" if is_complete else ""
                
                # Get last activity timestamp for this player
                last_activity_timestamp = activity_timer_dict.get((0, player_id))  # Assuming team 0
                timestamp_line = ""
                if last_activity_timestamp:
                    # Convert to Unix timestamp and format for Discord
                    unix_timestamp = int(last_activity_timestamp)
                    timestamp_line = f"\n└ Last check time: <t:{unix_timestamp}:R>"
                
                progress_bar = self.create_progress_bar(percentage)
                player_line = (
                    f"**{player_name}** ({player_game}){completion_indicator}\n"
                    f"└ {checked_count}/{total_locations} locations ({percentage:.1f}%)\n"
                    f"└ {progress_bar}{timestamp_line}\n"
                )
            else:
                # Get last activity timestamp for this player
                last_activity_timestamp = activity_timer_dict.get((0, player_id))  # Assuming team 0
                timestamp_line = ""
                if last_activity_timestamp:
                    # Convert to Unix timestamp and format for Discord
                    unix_timestamp = int(last_activity_timestamp)
                    timestamp_line = f"\n└ Last check time: <t:{unix_timestamp}:R>"
                
                player_line = (
                    f"**{player_name}** ({player_game})\n"
                    f"└ {checked_count}/? locations (No location data available){timestamp_line}\n"
                )
            
            # Store for sorting
            player_progress_data.append((player_name.lower(), player_line))
        
        # If specific players were requested but none found, show an error message
        if show_specific_players and not any(p for p in player_progress_data):
            # List available players for reference
            available_players = [info["name"] for info in all_players.values() 
                               if info["name"].lower() != "rhelbot"]
            
            # Customize error message based on the original input
            if original_player and original_player.lower() == "me":
                await interaction.followup.send(
                    f"❌ You don't have any players in this game.\n"
                    f"Available players: {', '.join(available_players)}"
                )
            elif original_player and (original_player.startswith('@') or original_player.startswith('<@')):
                await interaction.followup.send(
                    f"❌ The mentioned Discord user doesn't have any players in this game.\n"
                    f"Available players: {', '.join(available_players)}"
                )
            else:
                player_name = target_players[0] if target_players else original_player
                await interaction.followup.send(
                    f"❌ Player '{player_name}' not found.\n"
                    f"Available players: {', '.join(available_players)}"
                )
            return
            
        # Sort alphabetically by player name
        player_progress_data.sort(key=lambda x: x[0])
        
        # Add sorted progress lines
        for _, player_line in player_progress_data:
            progress_lines.append(player_line)
        
        # Calculate total game progress
        total_checked = 0
        total_locations = 0
        
        for player_id, player_info in all_players.items():
            player_name = player_info["name"]
            
            # Skip the Rhelbot tracker
            if player_name.lower() == "rhelbot":
                continue
            
            # Get checked locations for this player
            checked_locations = location_checks.get((0, player_id), set())
            checked_count = len(checked_locations)
            
            # Get total locations for this player
            player_total_locations = get_player_total_locations(player_id, save_data)
            
            total_checked += checked_count
            total_locations += player_total_locations
        
        # Add total progress section
        if total_locations > 0:
            total_percentage = (total_checked / total_locations) * 100
            total_progress_bar = self.create_progress_bar(total_percentage)
            
            progress_lines.append("─" * 40)  # Separator line
            progress_lines.append("\n📈 **Total Game Progress**")
            progress_lines.append(f"\n└ {total_checked}/{total_locations} locations ({total_percentage:.1f}%)")
            progress_lines.append(f"└ {total_progress_bar}")
        
        # Send the progress report
        progress_message = "\n".join(progress_lines)
        
        # Split message if it's too long for Discord
        if len(progress_message) > 2000:
            # Send in chunks
            chunks = []
            current_chunk = "📊 **Player Progress Report**\n\n"
            
            for line in progress_lines[1:]:  # Skip the header since we added it to current_chunk
                if len(current_chunk + line) > 1900:  # Leave some buffer
                    chunks.append(current_chunk)
                    current_chunk = line
                else:
                    current_chunk += line
            
            if current_chunk:
                chunks.append(current_chunk)
            
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await interaction.followup.send(chunk)
                else:
                    await interaction.channel.send(chunk)
        else:
            await interaction.followup.send(progress_message)
    
    
    def parse_apsave_alternative(self, apsave_file):
        """
        Alternative method to parse .apsave file without full Archipelago dependencies
        
        This method delegates to the helper function in data_helpers.py
        
        Args:
            apsave_file: Path to the .apsave file to parse
            
        Returns:
            Parsed save data dictionary
        """
        return parse_apsave_alternative(apsave_file)
    
    def is_server_running(self) -> bool:
        """
        Check if the Archipelago server is currently running
        (Delegating to helpers.server_helpers.is_server_running)
        """
        from helpers.server_helpers import is_server_running
        return is_server_running(self.server_process)
    
    def extract_player_data_from_save(self, save_data):
        """
        Extract player and game data from save file when websocket connection is not available
        
        This method delegates to the helper function in data_helpers.py
        
        Args:
            save_data: The parsed save data dictionary
            
        Returns:
            Tuple containing player data and game data dictionaries
        """
        return extract_player_data_from_save(save_data)
    
    def get_server_password(self, password_file: str = "server_password.txt") -> str:
        """
        Read the server password from server_password.txt file.
        
        This method delegates to the helper function in server_helpers.py
        
        Args:
            password_file: Path to the password file
            
        Returns:
            The server password as a string
        """
        return get_server_password(password_file)

    def _create_connection_message(self, password: str = None) -> dict:
        """
        Create a standard Archipelago connection message
        (Delegating to helpers.server_helpers.create_connection_message)
        """
        from helpers.server_helpers import create_connection_message
        return create_connection_message(password)

    async def _connect_to_server(self, server_url: str, timeout: float = 15.0):
        """
        Create a websocket connection to the Archipelago server
        (Delegating to helpers.server_helpers.connect_to_server)
        """
        from helpers.server_helpers import connect_to_server
        return await connect_to_server(server_url, timeout)

    async def fetch_server_data(self, server_url: str = "ws://ap.rhelys.com:38281", password: str = None, save_datapackage: bool = False):
        """
        Connect to server temporarily to fetch player and game data.
        
        This method delegates to the helper function in server_helpers.py
        
        Args:
            server_url: Archipelago server URL
            password: Server password (optional)
            save_datapackage: Whether to save the datapackage locally (default: False)
            
        Returns:
            Dictionary containing players and game_data, or None on failure
        """
        from helpers.server_helpers import fetch_server_data
        return await fetch_server_data(server_url, password, save_datapackage)
    
    
    
    
    def create_progress_bar(self, percentage: float, length: int = 20) -> str:
        """
        Create a visual progress bar
        (Delegating to helpers.formatting_helpers.create_progress_bar)
        """
        from helpers.formatting_helpers import create_progress_bar
        return create_progress_bar(percentage, length)
    
    def get_player_hint_points(self, player_id: int, save_data: dict) -> int:
        """
        Get the current hint points for a specific player
        (Delegating to helpers.progress_helpers.get_player_hint_points)
        """
        from helpers.progress_helpers import get_player_hint_points
        return get_player_hint_points(player_id, save_data, get_player_total_locations)
    
    def get_hint_cost(self, player_id: int, save_data: dict) -> int:
        """
        Get the cost of the next hint for a specific player
        (Delegating to helpers.progress_helpers.get_hint_cost)
        """
        from helpers.progress_helpers import get_hint_cost
        return get_hint_cost(player_id, save_data, get_player_total_locations)
        
    def is_player_completed(self, player_id: int, save_data: dict) -> bool:
        """Check if a player has completed 100% of their locations"""
        # Get checked locations for this player from save data
        # location_checks format: {(team, slot): set of location_ids}
        save_locations = save_data.get("location_checks", {}).get((0, player_id), set())  # Assuming team 0

        # Merge with real-time tracking data for most up-to-date information
        real_time_locations = self.player_progress.get(player_id, set())
        checked_locations = save_locations.union(real_time_locations)
        checked_count = len(checked_locations)
        
        # Get total locations for this player
        total_locations = get_player_total_locations(player_id, save_data)
        
        # Calculate completion percentage
        if total_locations > 0:
            percentage = (checked_count / total_locations) * 100
            return percentage >= 100.0
        
        return False  # If we can't determine locations, assume not complete

    @app_commands.command(
        name="hints",
        description="Shows all current hints for key items, grouped by finding player",
    )
    @app_commands.describe(
        player="Optional: Show hints only for a specific player and their hint points/cost"
    )
    async def ap_hints(self, interaction: discord.Interaction, player: Optional[str] = None):
        await interaction.response.defer()
        
        # Check if server is running first
        server_running = is_server_running()
        
        if not server_running:
            await interaction.followup.send("❌ Archipelago server is not running. Use `/ap start` to start the server first.")
            return
        
        # Resolve player reference if provided
        original_player = None
        target_players = None
        if player:
            original_player = player
            resolved_player = self.resolve_player_name(interaction.user.id, player)
            if resolved_player is None and player.lower() == "me":
                await interaction.followup.send("❌ You haven't joined the game yet. Use `/ap join` first.")
                return
            elif isinstance(resolved_player, list):
                # If multiple players are found, show hints for all of them
                target_players = resolved_player
                await interaction.followup.send(f"ℹ️ Showing hints for all your players: {', '.join(resolved_player)}")
            else:
                target_players = [resolved_player]
        
        # Load save data to get hints
        save_data = load_apsave_data()
        if not save_data:
            await interaction.followup.send("❌ Could not load save data. Make sure the Archipelago server has a save file.")
            return
        
        # Get hints from save data
        hints_data = save_data.get("hints", {})
        if not hints_data:
            await interaction.followup.send("📝 No hints found in the current game.")
            return
        
        # Extract all hints from the dictionary of sets and deduplicate
        all_hints_set = set()
        for hint_set in hints_data.values():
            if isinstance(hint_set, set):
                all_hints_set.update(hint_set)
            elif isinstance(hint_set, (list, tuple)):
                all_hints_set.update(hint_set)
            elif hint_set:  # Single hint object
                all_hints_set.add(hint_set)
        
        all_hints = list(all_hints_set)
        
        if not all_hints:
            await interaction.followup.send("📝 No hints found in the current game.")
            return
        
        # Get player and game data from save file or connection data
        all_players = {}
        game_data = {}
        
        # First try to get data from active websocket connection
        if self.connection_data and self.game_data:
            for server_key, conn_data in self.connection_data.items():
                slot_info = conn_data.get("slot_info", {})
                for slot_id, player_info in slot_info.items():
                    player_id = int(slot_id)
                    all_players[player_id] = {
                        "name": player_info.get("name", f"Player {player_id}"),
                        "game": player_info.get("game", "Unknown")
                    }
            game_data = self.game_data
        else:
            # If no websocket connection, connect to server to get DataPackage
            await interaction.edit_original_response(content="📡 Connecting to server to get game data...")
            
            server_data = await self.fetch_server_data()
            if server_data:
                all_players = server_data["players"]
                game_data = server_data["game_data"]
                
                # Store the fetched data temporarily for lookups
                self.game_data = game_data
                self.connection_data["temp_fetch"] = {"slot_info": {str(k): v for k, v in all_players.items()}}
            else:
                # Fallback: try to extract basic data from save file
                all_players, game_data = extract_player_data_from_save(save_data)
        
        # Filter hints for key items (item_flags = 1) and acceptable statuses
        # Status filtering: Include "Found" and "Priority", exclude "No Priority" and "Avoid"
        # Note: For now, including all key item hints since status parsing isn't working correctly
        key_item_hints = []
        for hint in all_hints:
            # Check if this is a Hint object with item_flags = 1 (progression items)
            if hasattr(hint, 'item_flags') and hint.item_flags == 1:
                # For now, include all key item hints since status filtering isn't working properly
                # TODO: Fix status parsing to properly filter by priority/found status
                key_item_hints.append(hint)
                    
            elif isinstance(hint, (list, tuple)) and len(hint) >= 7:
                # Handle tuple/list representation
                item_flags = hint[6] if len(hint) > 6 else 0
                if item_flags == 1:
                    # Convert to a simple object for easier handling
                    class SimpleHint:
                        def __init__(self, data):
                            self.receiving_player = data[0]
                            self.finding_player = data[1]
                            self.location = data[2]
                            self.item = data[3]
                            self.found = data[4]
                            self.entrance = data[5] if len(data) > 5 else ""
                            self.item_flags = data[6] if len(data) > 6 else 0
                            self.status = data[7] if len(data) > 7 else 0
                    
                    simple_hint = SimpleHint(hint)
                    # Include all key item hints for now
                    key_item_hints.append(simple_hint)
                        
            elif isinstance(hint, dict) and hint.get('item_flags', 0) == 1:
                # Handle dictionary representation
                class SimpleHint:
                    def __init__(self, data):
                        self.receiving_player = data.get('receiving_player', 0)
                        self.finding_player = data.get('finding_player', 0)
                        self.location = data.get('location', 0)
                        self.item = data.get('item', 0)
                        self.found = data.get('found', False)
                        self.entrance = data.get('entrance', "")
                        self.item_flags = data.get('item_flags', 0)
                        self.status = data.get('status', 0)
                
                simple_hint = SimpleHint(hint)
                # Include all key item hints for now
                key_item_hints.append(simple_hint)
        
        if not key_item_hints:
            await interaction.followup.send("📝 No hints found for key items in the current game.")
            return
        
        # If specific players are requested, filter hints and show hint points/cost for each
        if target_players:
            # Find the player IDs by name (case-insensitive)
            target_player_data = {}
            
            for target_player_name in target_players:
                for player_id, player_info in all_players.items():
                    if player_info["name"].lower() == target_player_name.lower():
                        target_player_data[player_id] = {
                            "name": player_info["name"],
                            "game": player_info["game"]
                        }
                        break
            
            if not target_player_data:
                # List available players for reference
                available_players = [info["name"] for info in all_players.values() if info["name"].lower() != "rhelbot"]
                
                # Check if the original player input was "me" or a Discord mention for better error message
                if original_player and original_player.lower() == "me":
                    await interaction.followup.send(
                        f"❌ You don't have any players in this game.\n"
                        f"Available players: {', '.join(available_players)}"
                    )
                elif original_player and (original_player.startswith('@') or original_player.startswith('<@')):
                    await interaction.followup.send(
                        f"❌ The mentioned Discord user doesn't have any players in this game.\n"
                        f"Available players: {', '.join(available_players)}"
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Player(s) '{', '.join(target_players)}' not found.\n"
                        f"Available players: {', '.join(available_players)}"
                    )
                return
            
            # Build the message for specific players
            hint_lines = []
            
            # Sort target players alphabetically by name
            sorted_target_players = sorted(target_player_data.items(), key=lambda x: x[1]["name"].lower())
            
            if len(target_player_data) == 1:
                # Single player - use original format
                target_player_id, target_player_info = list(target_player_data.items())[0]
                target_player_name = target_player_info["name"]
                
                # Filter hints for this specific player (as the finding player)
                player_hints = [hint for hint in key_item_hints if hint.finding_player == target_player_id]
                
                # Filter hints requested by this player (as the receiving player)
                requested_hints = [hint for hint in key_item_hints if hint.receiving_player == target_player_id]
                
                # Get hint points and cost information (always show these)
                hint_points = self.get_player_hint_points(target_player_id, save_data)
                hint_cost = self.get_hint_cost(target_player_id, save_data)
                
                hint_lines.append(f"🔑 **Key Item Hints for {target_player_name}**")
                hint_lines.append(f"💰 **Hint Points**: {hint_points}")
                hint_lines.append(f"💸 **Next Hint Cost**: {hint_cost}")
                hint_lines.append("")
                
                # Section 1: Hints this player has found for others
                hint_lines.append("## 🔍 **Hint Locations for Others**")
                if not player_hints:
                    hint_lines.append("📝 No hints found by this player.")
                else:
                    # Sort hints by receiving player name
                    sorted_hints = []
                    for hint in player_hints:
                        receiving_player_name = all_players.get(hint.receiving_player, {}).get("name", f"Player {hint.receiving_player}")
                        sorted_hints.append((receiving_player_name.lower(), hint, receiving_player_name))
                    
                    sorted_hints.sort(key=lambda x: x[0])
                    
                    for _, hint, receiving_player_name in sorted_hints:
                        # Look up item and location names
                        receiving_game = all_players.get(hint.receiving_player, {}).get("game", "Unknown")
                        finder_game = all_players.get(hint.finding_player, {}).get("game", "Unknown")
                        
                        # Get item name (from receiving player's game)
                        item_name = self.lookup_item_name(receiving_game, hint.item) if game_data else f"Item {hint.item}"
                        
                        # Get location name (from finding player's game)
                        location_name = self.lookup_location_name(finder_game, hint.location) if game_data else f"Location {hint.location}"
                        
                        # Status indicator
                        status_indicator = " ✅" if hint.found else ""
                        
                        hint_lines.append(f"└ **{item_name}** → {receiving_player_name}")
                        hint_lines.append(f"  📍 *{location_name}* {status_indicator}")
                
                hint_lines.append("")  # Empty line between sections
                
                # Section 2: Hints this player has requested from others
                hint_lines.append("## 🎯 **Hints Requested from Others**")
                if not requested_hints:
                    hint_lines.append("📝 No hints requested by this player.")
                else:
                    # Sort hints by finding player name
                    sorted_requested_hints = []
                    for hint in requested_hints:
                        finding_player_id = hint.finding_player
                        finding_player_name = all_players.get(finding_player_id, {}).get("name", f"Player {finding_player_id}")
                        
                        # Skip hints from players who have completed 100% of their locations
                        if save_data and self.is_player_completed(finding_player_id, save_data):
                            print(f"Skipping hint from player {finding_player_name} who has completed 100% of locations")
                            continue
                            
                        sorted_requested_hints.append((finding_player_name.lower(), hint, finding_player_name))
                    
                    if not sorted_requested_hints:
                        hint_lines.append("📝 No hints from players who have not completed their locations.")
                    else:
                        sorted_requested_hints.sort(key=lambda x: x[0])
                        
                        for _, hint, finding_player_name in sorted_requested_hints:
                            # Look up item and location names
                            receiving_game = all_players.get(hint.receiving_player, {}).get("game", "Unknown")
                            finder_game = all_players.get(hint.finding_player, {}).get("game", "Unknown")
                            
                            # Get item name (from receiving player's game)
                            item_name = self.lookup_item_name(receiving_game, hint.item) if game_data else f"Item {hint.item}"
                            
                            # Get location name (from finding player's game)
                            location_name = self.lookup_location_name(finder_game, hint.location) if game_data else f"Location {hint.location}"
                            
                            # Status indicator
                            status_indicator = " ✅" if hint.found else ""
                            
                            hint_lines.append(f"└ **{item_name}** ← {finding_player_name}")
                            hint_lines.append(f"  📍 *{location_name}* {status_indicator}")
            
            else:
                # Multiple players - show them grouped by player
                hint_lines.append("🔑 **Key Item Hints**\n")
                
                for target_player_id, target_player_info in sorted_target_players:
                    target_player_name = target_player_info["name"]
                    target_player_game = target_player_info["game"]
                    
                    # Filter hints for this specific player (as the finding player)
                    player_hints = [hint for hint in key_item_hints if hint.finding_player == target_player_id]
                    
                    # Filter hints requested by this player (as the receiving player)  
                    requested_hints = [hint for hint in key_item_hints if hint.receiving_player == target_player_id]
                    
                    # Get hint points and cost information
                    hint_points = self.get_player_hint_points(target_player_id, save_data)
                    hint_cost = self.get_hint_cost(target_player_id, save_data)
                    
                    hint_lines.append(f"## {target_player_name} ({target_player_game})")
                    hint_lines.append(f"💰 **Hint Points**: {hint_points} | 💸 **Next Hint Cost**: {hint_cost}")
                    hint_lines.append("")
                    
                    # Section 1: Hints this player has found for others
                    hint_lines.append("### 🔍 **Hint Locations for Others**")
                    if not player_hints:
                        hint_lines.append("📝 No hints found by this player.")
                    else:
                        # Sort hints by receiving player name
                        sorted_hints = []
                        for hint in player_hints:
                            receiving_player_name = all_players.get(hint.receiving_player, {}).get("name", f"Player {hint.receiving_player}")
                            sorted_hints.append((receiving_player_name.lower(), hint, receiving_player_name))
                        
                        sorted_hints.sort(key=lambda x: x[0])
                        
                        for _, hint, receiving_player_name in sorted_hints:
                            # Look up item and location names
                            receiving_game = all_players.get(hint.receiving_player, {}).get("game", "Unknown")
                            finder_game = all_players.get(hint.finding_player, {}).get("game", "Unknown")
                            
                            # Get item name (from receiving player's game)
                            item_name = self.lookup_item_name(receiving_game, hint.item) if game_data else f"Item {hint.item}"
                            
                            # Get location name (from finding player's game)
                            location_name = self.lookup_location_name(finder_game, hint.location) if game_data else f"Location {hint.location}"
                            
                            # Status indicator
                            status_indicator = " ✅" if hint.found else ""
                            
                            hint_lines.append(f"└ **{item_name}** → {receiving_player_name}")
                            hint_lines.append(f"  📍 *{location_name}* {status_indicator}")
                    
                    hint_lines.append("")  # Empty line between subsections
                    
                    # Section 2: Hints this player has requested from others
                    hint_lines.append("### 🎯 **Hints Requested from Others**")
                    if not requested_hints:
                        hint_lines.append("📝 No hints requested by this player.")
                    else:
                        # Sort hints by finding player name
                        sorted_requested_hints = []
                        for hint in requested_hints:
                            finding_player_id = hint.finding_player
                            finding_player_name = all_players.get(finding_player_id, {}).get("name", f"Player {finding_player_id}")
                            
                            # Skip hints from players who have completed 100% of their locations
                            if save_data and self.is_player_completed(finding_player_id, save_data):
                                print(f"Skipping hint from player {finding_player_name} who has completed 100% of locations")
                                continue
                                
                            sorted_requested_hints.append((finding_player_name.lower(), hint, finding_player_name))
                        
                        if not sorted_requested_hints:
                            hint_lines.append("📝 No hints from players who have not completed their locations.")
                        else:
                            sorted_requested_hints.sort(key=lambda x: x[0])
                            
                            for _, hint, finding_player_name in sorted_requested_hints:
                                # Look up item and location names
                                receiving_game = all_players.get(hint.receiving_player, {}).get("game", "Unknown")
                                finder_game = all_players.get(hint.finding_player, {}).get("game", "Unknown")
                                
                                # Get item name (from receiving player's game)
                                item_name = self.lookup_item_name(receiving_game, hint.item) if game_data else f"Item {hint.item}"
                                
                                # Get location name (from finding player's game)
                                location_name = self.lookup_location_name(finder_game, hint.location) if game_data else f"Location {hint.location}"
                                
                                # Status indicator
                                status_indicator = " ✅" if hint.found else ""
                                
                                hint_lines.append(f"└ **{item_name}** ← {finding_player_name}")
                                hint_lines.append(f"  📍 *{location_name}* {status_indicator}")
                    
                    hint_lines.append("")  # Empty line between players
        
        else:
            # Show all players' hints (original behavior)
            # Group hints by finding player
            hints_by_finder = {}
            for hint in key_item_hints:
                finding_player = hint.finding_player
                if finding_player not in hints_by_finder:
                    hints_by_finder[finding_player] = []
                hints_by_finder[finding_player].append(hint)
            
            # Build the hints message
            hint_lines = []
            hint_lines.append("🔑 **Key Item Hints**\n")
            
            # Sort finding players alphabetically
            sorted_finders = []
            for finding_player in hints_by_finder.keys():
                # Skip Rhelbot
                player_name = all_players.get(finding_player, {}).get("name", f"Player {finding_player}")
                if player_name.lower() != "rhelbot":
                    sorted_finders.append((player_name.lower(), finding_player, player_name))
            
            sorted_finders.sort(key=lambda x: x[0])
            
            for _, finding_player, finder_name in sorted_finders:
                finder_game = all_players.get(finding_player, {}).get("game", "Unknown")
                hint_lines.append(f"## {finder_name} ({finder_game})")
                
                # Sort hints for this player by receiving player name
                player_hints = hints_by_finder[finding_player]
                sorted_hints = []
                for hint in player_hints:
                    receiving_player_id = hint.receiving_player
                    receiving_player_name = all_players.get(receiving_player_id, {}).get("name", f"Player {receiving_player_id}")
                    
                    # Skip hints for players who have completed 100% of their locations
                    if save_data and self.is_player_completed(receiving_player_id, save_data):
                        print(f"Skipping hint for player {receiving_player_name} who has completed 100% of locations")
                        continue
                        
                    sorted_hints.append((receiving_player_name.lower(), hint, receiving_player_name))
                
                if not sorted_hints:
                    hint_lines.append("📝 No hints for players who have not completed their locations.")
                else:
                    sorted_hints.sort(key=lambda x: x[0])
                    
                    for _, hint, receiving_player_name in sorted_hints:
                        # Look up item and location names
                        receiving_game = all_players.get(hint.receiving_player, {}).get("game", "Unknown")
                        finder_game = all_players.get(hint.finding_player, {}).get("game", "Unknown")
                        
                        # Get item name (from receiving player's game)
                        item_name = self.lookup_item_name(receiving_game, hint.item) if game_data else f"Item {hint.item}"
                        
                        # Get location name (from finding player's game)
                        location_name = self.lookup_location_name(finder_game, hint.location) if game_data else f"Location {hint.location}"
                        
                        # Status indicator
                        status_indicator = " ✅" if hint.found else ""
                        
                        hint_lines.append(f"└ **{item_name}** → {receiving_player_name}")
                        hint_lines.append(f"  📍 *{location_name}* {status_indicator}")
                
                hint_lines.append("")  # Empty line between players
        
        # Send the hints message
        hints_message = "\n".join(hint_lines)
        
        # Split message if it's too long for Discord
        if len(hints_message) > 2000:
            # Send in chunks
            chunks = []
            current_chunk = "## 🔑 **Key Item Hints**\n\n"
            
            for line in hint_lines[1:]:  # Skip the header since we added it to current_chunk
                if len(current_chunk + line + "\n") > 1900:  # Leave some buffer
                    chunks.append(current_chunk)
                    current_chunk = line + "\n"
                else:
                    current_chunk += line + "\n"
            
            if current_chunk.strip():
                chunks.append(current_chunk)
            
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await interaction.followup.send(chunk)
                else:
                    await interaction.channel.send(chunk)
        else:
            await interaction.followup.send(hints_message)

    @app_commands.command(
        name="gethint",
        description="Get a hint for a specific item by connecting as a player",
    )
    @app_commands.describe(
        player_name="The player name to connect as",
        item_name="The item to get a hint for"
    )
    async def ap_gethint(self, interaction: discord.Interaction, player_name: str, item_name: str):
        await interaction.response.defer()
        
        # Resolve "me" to actual player name
        resolved_name = self.resolve_player_name(interaction.user.id, player_name)
        if resolved_name is None and player_name.lower() == "me":
            await interaction.followup.send("❌ You haven't joined the game yet. Use `/ap join` first.")
            return
        elif resolved_name is None:
            resolved_name = player_name
        
        try:
            # Get server password
            password = self.get_server_password()
        except Exception as e:
            await interaction.followup.send(f"❌ Server password error: {str(e)}")
            return
        
        # Check if server is running
        if not self.is_server_running():
            await interaction.followup.send("❌ Archipelago server is not running. Use `/ap start` to start the server first.")
            return
        
        server_url = "ws://ap.rhelys.com:38281"
        
        try:
            # Connect to the Archipelago websocket server
            websocket = await self._connect_to_server(server_url)
            
            # Variables to track the hint process
            tracker_connection_established = False
            player_connection_established = False
            hint_response_received = False
            player_game = None
            player_slot = None
            
            try:
                # First connect as tracker to get player's game information
                tracker_connect_msg = {
                    "cmd": "Connect",
                    "game": "",
                    "password": password,
                    "name": "Rhelbot",
                    "version": {"major": 0, "minor": 6, "build": 0, "class": "Version"},
                    "tags": ["Tracker"],
                    "items_handling": 0b000,  # No items handling for tracker
                    "uuid": __import__('uuid').getnode()
                }
                await websocket.send(json.dumps([tracker_connect_msg]))
                
                # Process messages with timeout
                timeout_counter = 0
                max_timeout = 30  # 30 seconds total timeout
                
                while timeout_counter < max_timeout and not hint_response_received:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        data = json.loads(message)
                        
                        for msg in data:
                            cmd = msg.get("cmd", "")
                            
                            if cmd == "Connected" and not tracker_connection_established:
                                tracker_connection_established = True
                                
                                # Extract player information
                                slot_info = msg.get("slot_info", {})
                                
                                # Find the player's slot and game
                                for slot_id, slot_data in slot_info.items():
                                    if slot_data.get("name", "").lower() == resolved_name.lower():
                                        player_slot = int(slot_id)
                                        player_game = slot_data.get("game", "")
                                        break
                                
                                if player_slot is None:
                                    await interaction.followup.send(f"❌ Player '{resolved_name}' not found in the current game.")
                                    return
                                
                                # Close tracker connection and reconnect as player
                                await websocket.close()
                                
                                # Reconnect as the specific player
                                websocket = await self._connect_to_server(server_url)
                                
                                player_connect_msg = {
                                    "cmd": "Connect",
                                    "game": player_game,  # Use the player's actual game
                                    "password": password,
                                    "name": resolved_name,
                                    "version": {"major": 0, "minor": 6, "build": 0, "class": "Version"},
                                    "tags": [],
                                    "items_handling": 0b111,  # Full items handling for player connection
                                    "uuid": __import__('uuid').getnode()
                                }
                                await websocket.send(json.dumps([player_connect_msg]))
                                
                            elif cmd == "Connected" and tracker_connection_established and not player_connection_established:
                                player_connection_established = True
                                
                                # Send initial status to confirm connection
                                await interaction.followup.send(f"🔍 Connected as **{resolved_name}** ({player_game}). Requesting hint for **{item_name}**...")
                                
                                # Send the hint command
                                await websocket.send(json.dumps([{"cmd": "Say", "text": f"!hint {item_name}"}]))
                                
                            elif cmd == "ConnectionRefused":
                                errors = msg.get("errors", ["Unknown error"])
                                await interaction.followup.send(f"❌ Connection refused: {', '.join(errors)}")
                                return
                                
                            elif cmd == "PrintJSON" and player_connection_established:
                                # Look for hint response in the print messages
                                msg_type = msg.get("type", "")
                                data_parts = msg.get("data", [])
                                
                                # Build the text from data parts
                                text_parts = []
                                for part in data_parts:
                                    if isinstance(part, dict) and "text" in part:
                                        text_parts.append(part["text"])
                                    elif isinstance(part, str):
                                        text_parts.append(part)
                                
                                full_text = "".join(text_parts)
                                
                                # Skip our own hint command message
                                if full_text.strip() == f"!hint {item_name}":
                                    print(f"Skipping our own hint command: {full_text}")
                                    continue
                                
                                # Skip messages that are just the player's own command
                                if full_text.strip().startswith(f"{resolved_name}:") and "!hint" in full_text:
                                    print(f"Skipping player's own command echo: {full_text}")
                                    continue
                                
                                # Check if this is a hint response (look for various hint indicators)
                                hint_indicators = [
                                    "found at",
                                    "is at",
                                    "you already know",
                                    "not enough points", 
                                    "no such item",
                                    "cannot afford",
                                    "item does not exist",
                                    "already hinted"
                                ]
                                
                                # Also check if the message contains the item name and typical hint response patterns
                                contains_item = item_name.lower() in full_text.lower()
                                has_hint_pattern = any(indicator in full_text.lower() for indicator in hint_indicators)
                                
                                # Look for location/hint information patterns
                                location_patterns = [
                                    " in ",
                                    " at ",
                                    " from ",
                                    " (world ",
                                    " - "
                                ]
                                has_location_info = any(pattern in full_text.lower() for pattern in location_patterns)
                                
                                # Check if this looks like a server response (not just our command)
                                is_server_response = (
                                    (contains_item and (has_hint_pattern or has_location_info)) or
                                    has_hint_pattern
                                ) and full_text.strip() and not full_text.startswith("!")
                                
                                if is_server_response:
                                    print(f"Detected hint response: {full_text}")
                                    hint_result = full_text
                                    hint_response_received = True
                                    
                                    # Try to resolve names from IDs in the response
                                    processed_hint_result = await self.process_hint_response(full_text, player_game)
                                    
                                    # Determine response type for appropriate color
                                    if "not enough points" in full_text.lower() or "cannot afford" in full_text.lower():
                                        color = 0xff0000  # Red for insufficient points
                                        title = f"❌ Insufficient Points for {item_name}"
                                    elif "no such item" in full_text.lower() or "item does not exist" in full_text.lower():
                                        color = 0xffa500  # Orange for item not found
                                        title = f"⚠️ Item Not Found: {item_name}"
                                    elif "you already know" in full_text.lower() or "already hinted" in full_text.lower():
                                        color = 0x0099ff  # Blue for already known
                                        title = f"ℹ️ Already Known: {item_name}"
                                    else:
                                        color = 0x00ff00  # Green for successful hint
                                        title = f"🔍 Hint for {item_name}"
                                    
                                    # Create response embed
                                    embed = discord.Embed(
                                        title=title,
                                        description=f"**Player:** {resolved_name} ({player_game})",
                                        color=color
                                    )
                                    
                                    # Add only the processed hint response from server
                                    embed.add_field(name="Server Response", value=processed_hint_result, inline=False)
                                    
                                    await interaction.followup.send(embed=embed)
                                    break
                                else:
                                    print(f"Skipping non-hint message: {full_text}")
                        
                    except asyncio.TimeoutError:
                        timeout_counter += 1
                        continue
                
                # Check if we got a response
                if not hint_response_received and player_connection_established:
                    await interaction.followup.send(
                        f"❌ No hint response received for **{item_name}**. "
                        f"The item may not exist, may already be found, or the server may be unresponsive."
                    )
                elif not tracker_connection_established:
                    await interaction.followup.send("❌ Failed to connect to the Archipelago server to get player information.")
                elif not player_connection_established:
                    await interaction.followup.send(f"❌ Failed to connect to the Archipelago server as **{resolved_name}**. Make sure the player name is correct and exists in the current game.")
                    
            finally:
                await websocket.close()
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error getting hint: {str(e)}")

    async def process_hint_response(self, hint_text: str, player_game: str) -> str:
        """Process hint response to resolve item and location names from IDs"""
        # Get game data if needed
        if not self.game_data:
            server_data = await self.fetch_server_data()
            if server_data and server_data.get("game_data"):
                self.game_data = server_data["game_data"]
        
        return await process_hint_response(
            hint_text, 
            player_game, 
            self.game_data, 
            self.lookup_item_name, 
            self.lookup_location_name, 
            self.fetch_server_data
        )

    @app_commands.command(
        name="shame",
        description="Identify players who haven't checked locations in over 72 hours"
    )
    async def ap_shame(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Check if server is running first
        server_running = self.is_server_running()
        
        if not server_running:
            await interaction.followup.send("❌ Archipelago server is not running. Use `/ap start` to start the server first.")
            return
            
        # Load save data
        save_data = load_apsave_data(self.output_directory, self.ap_directory)
        if not save_data:
            await interaction.followup.send("❌ Could not load save data. Make sure the Archipelago server has a save file.")
            return
        
        # Load game status to map players to Discord users
        game_status = load_game_status()
        discord_users = game_status.get("discord_users", {})
        
        # Get player and game data
        all_players = {}
        
        # Try to get data from active websocket connection first
        if self.connection_data:
            for server_key, conn_data in self.connection_data.items():
                slot_info = conn_data.get("slot_info", {})
                for slot_id, player_info in slot_info.items():
                    player_id = int(slot_id)
                    all_players[player_id] = {
                        "name": player_info.get("name", f"Player {player_id}"),
                        "game": player_info.get("game", "Unknown")
                    }
        else:
            # Fallback: connect to server to get data
            server_data = await self.fetch_server_data()
            if server_data:
                all_players = server_data["players"]
            else:
                # Final fallback: extract from save file
                all_players, _ = self.extract_player_data_from_save(save_data)
        
        if not all_players:
            await interaction.followup.send("❌ No players found in the current game.")
            return
        
        # Get activity timers and location checks
        client_activity_timers = save_data.get("client_activity_timers", ())
        location_checks = save_data.get("location_checks", {})

        # Merge with real-time tracking data for most up-to-date information
        for player_id, real_time_locations in self.player_progress.items():
            save_locations = location_checks.get((0, player_id), set())
            merged_locations = save_locations.union(real_time_locations)
            location_checks[(0, player_id)] = merged_locations
        
        # Convert activity timers to dictionary
        activity_timer_dict = {}
        if isinstance(client_activity_timers, (list, tuple)):
            for entry in client_activity_timers:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    player_key, timestamp = entry[0], entry[1]
                    if isinstance(player_key, (list, tuple)) and len(player_key) >= 2:
                        team, slot = player_key[0], player_key[1]
                        activity_timer_dict[(team, slot)] = timestamp
        
        # Calculate 72 hours ago timestamp
        seventy_two_hours_ago = time.time() - (72 * 60 * 60)
        
        # Find offending players
        offending_players = []
        
        for player_id, player_info in all_players.items():
            player_name = player_info["name"]
            player_game = player_info["game"]
            
            # Skip the Rhelbot tracker
            if player_name.lower() == "rhelbot":
                continue
            
            # Check if player has finished their game
            checked_locations = location_checks.get((0, player_id), set())
            total_locations = get_player_total_locations(player_id, save_data)
            
            if total_locations > 0:
                percentage = (len(checked_locations) / total_locations) * 100
                is_complete = percentage >= 100.0
                
                # Skip completed players
                if is_complete:
                    continue
            
            # Check last activity time
            last_activity_timestamp = activity_timer_dict.get((0, player_id))
            
            # If no activity timestamp, consider them inactive
            if not last_activity_timestamp or last_activity_timestamp < seventy_two_hours_ago:
                # Find Discord user for this player
                discord_user_id = None
                for user_id, user_players in discord_users.items():
                    if player_name in user_players:
                        discord_user_id = user_id
                        break
                
                offending_players.append({
                    "player_name": player_name,
                    "player_game": player_game,
                    "discord_user_id": discord_user_id,
                    "last_activity": last_activity_timestamp
                })
        
        # Build shame message
        if not offending_players:
            await interaction.followup.send("🎉 All players are active! No one to shame today.")
            return
        
        # Group players by Discord user for better formatting
        players_by_user = {}
        unknown_players = []
        
        for player in offending_players:
            if player["discord_user_id"]:
                user_id = player["discord_user_id"]
                if user_id not in players_by_user:
                    players_by_user[user_id] = []
                players_by_user[user_id].append(player)
            else:
                unknown_players.append(player)
        
        shame_lines = ["🔔 **LAZY DONKEY ALERT** 🔔\n"]
        shame_lines.append("The following players haven't checked locations in over 72 hours:\n")
        
        # Process Discord users with known mappings
        for user_id, user_players in players_by_user.items():
            mention = f"<@{user_id}>"
            
            if len(user_players) == 1:
                # Single player for this user
                player = user_players[0]
                if player["last_activity"]:
                    unix_timestamp = int(player["last_activity"])
                    time_str = f" - (last check: <t:{unix_timestamp}:R>)"
                else:
                    time_str = " - (no recorded activity)"
                
                shame_lines.append(f"• {mention} - {player['player_name']} ({player['player_game']}){time_str}")
            else:
                # Multiple players for this user
                shame_lines.append(f"• {mention} - Multiple players:")
                for player in user_players:
                    if player["last_activity"]:
                        unix_timestamp = int(player["last_activity"])
                        time_str = f" - (last check: <t:{unix_timestamp}:R>)"
                    else:
                        time_str = " - (no recorded activity)"
                    
                    shame_lines.append(f"  └ {player['player_name']} ({player['player_game']}){time_str}")
        
        # Process players with unknown Discord users
        for player in unknown_players:
            if player["last_activity"]:
                unix_timestamp = int(player["last_activity"])
                time_str = f" - (last check: <t:{unix_timestamp}:R>)"
            else:
                time_str = " - (no recorded activity)"
            
            shame_lines.append(f"• **{player['player_name']}** ({player['player_game']}) - Discord user unknown{time_str}")
        
        shame_lines.append(f"\n⏰ Get back to checking those locations! The multiworld waits for no one!")
        
        shame_message = "\n".join(shame_lines)
        await interaction.followup.send(shame_message)

    @app_commands.command(
        name="help", description="Basic Archipelago setup information and game lists"
    )
    async def ap_help(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "# __Archipelago Setup Resources__\n"
            "* Main site: https://archipelago.gg/\n"
            "* Beta site: http://archipelago.gg:24242/\n"
            "* Setup guides: https://archipelago.gg/tutorial/\n"
            "* Community games list: https://docs.google.com/spreadsheets/d/1iuzDTOAvdoNe8Ne8i461qGNucg5OuEoF-Ikqs8aUQZw\n"
            "* Archipelago Discord: https://discord.gg/8Z65BR2"
        )


async def setup(bot) -> None:
    print(f"Entering AP cog setup\n")
    await bot.add_cog(ApCog(bot=bot))
    print("AP cog setup complete\n")
