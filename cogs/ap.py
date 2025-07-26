import discord
from discord import app_commands
from discord.ext import commands, tasks
from os import remove, listdir, rename, path
import subprocess
from asyncio import sleep
import asyncio
import websockets
import json
import zipfile
from typing import Optional, Dict
from ruyaml import YAML
import shutil

donkeyServer = discord.Object(id=591625815528177690)

# Learning from https://github.com/Quasky/bridgeipelago/blob/main/bridgeipelago.py

@app_commands.guilds(donkeyServer)
class ApCog(commands.GroupCog, group_name="ap"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()  # this is now required in this context.
        
        # Websocket tracking variables
        self.active_connections: Dict[str, Dict] = {}  # server_url -> {task, channel_id, websocket}
        
        # Data storage for lookups
        self.game_data: Dict[str, Dict] = {}  # game_name -> {item_name_to_id, location_name_to_id}
        self.connection_data: Dict[str, Dict] = {}  # server_url -> {slot_info, etc}
        
        # Progress tracking for each player
        self.player_progress: Dict[int, set] = {}  # player_id -> set of checked location_ids
        
        # Server process tracking
        self.server_process = None

    # Config section - To be updated if anything moves around, or it's going to be run by someone else
    output_directory = "./Archipelago/output/"
    ap_directory = "./Archipelago/"
    system_extensions = [".archipelago", ".txt", ".apsave"]
    status_file = "./game_status.txt"
    player = ""
    game = ""

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
        current_players = {}

        # Pulling the list of current players in the game for reference/comparison
        with open(self.status_file) as status:
            for line in status:
                name, file = line.rstrip("\n").split(":")
                current_players[name.capitalize()] = file

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
        """Look up item name from ID using game data"""
        print(f"Looking up item: game='{game}', item_id={item_id}")
        
        if not self.game_data:
            print("No game data available")
            return f"Item {item_id}"
            
        if game not in self.game_data:
            print(f"Game '{game}' not found in game data. Available games: {list(self.game_data.keys())}")
            return f"Item {item_id}"
        
        game_data = self.game_data[game]
        if "item_name_to_id" not in game_data:
            print(f"No item_name_to_id in game data for '{game}'. Available keys: {list(game_data.keys())}")
            return f"Item {item_id}"
        
        item_mapping = game_data["item_name_to_id"]
        print(f"Searching through {len(item_mapping)} items for ID {item_id}")
        
        # Search through the item_name_to_id mapping
        for item_name, id_value in item_mapping.items():
            if str(id_value) == str(item_id):
                print(f"Found match: {item_name}")
                return item_name
        
        print(f"No match found for item ID {item_id}")
        return f"Item {item_id}"
    
    def lookup_location_name(self, game: str, location_id: int) -> str:
        """Look up location name from ID using game data"""
        print(f"Looking up location: game='{game}', location_id={location_id}")
        
        if not self.game_data:
            print("No game data available")
            return f"Location {location_id}"
            
        if game not in self.game_data:
            print(f"Game '{game}' not found in game data. Available games: {list(self.game_data.keys())}")
            return f"Location {location_id}"
        
        game_data = self.game_data[game]
        if "location_name_to_id" not in game_data:
            print(f"No location_name_to_id in game data for '{game}'. Available keys: {list(game_data.keys())}")
            return f"Location {location_id}"
        
        location_mapping = game_data["location_name_to_id"]
        print(f"Searching through {len(location_mapping)} locations for ID {location_id}")
        
        # Search through the location_name_to_id mapping
        for location_name, id_value in location_mapping.items():
            if str(id_value) == str(location_id):
                print(f"Found match: {location_name}")
                return location_name
        
        print(f"No match found for location ID {location_id}")
        return f"Location {location_id}"
    
    def lookup_player_name(self, player_id: int) -> str:
        """Look up player name from ID using connection data"""
        # Search through all connection data for the player
        for server_url, conn_data in self.connection_data.items():
            slot_info = conn_data.get("slot_info", {})
            for slot_id, player_info in slot_info.items():
                if str(slot_id) == str(player_id):
                    return player_info.get("name", f"Player {player_id}")
        
        return f"Player {player_id}"
    
    def lookup_player_game(self, player_id: int) -> str:
        """Look up player's game from ID using connection data"""
        # Search through all connection data for the player's game
        for server_url, conn_data in self.connection_data.items():
            slot_info = conn_data.get("slot_info", {})
            for slot_id, player_info in slot_info.items():
                if str(slot_id) == str(player_id):
                    return player_info.get("game", "Unknown")
        
        return "Unknown"

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
            
            if msg_type == "Chat":
                # Simple text message for chat
                text = "".join([item.get("text", "") for item in data])
                await channel.send(f"💬 {text}")
                
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
                    
                    # Only send messages for progression (1) and useful (2) items
                    if item_flags in [1, 2]:
                        # Debug logging
                        print(f"Processing ItemSend: sender_id={sender_id}, recipient_id={recipient_id}, item_id={item_id}, item_flags={item_flags}, location_id={location_id}")
                        print(f"Available connection data keys: {list(self.connection_data.keys())}")
                        print(f"Available game data keys: {list(self.game_data.keys())}")
                        
                        # Look up actual names using the stored data
                        sender_name = self.lookup_player_name(int(sender_id))
                        recipient_name = self.lookup_player_name(int(recipient_id))
                        
                        # Get the recipient's game to look up item and location names
                        recipient_game = self.lookup_player_game(int(recipient_id))
                        sender_game = self.lookup_player_game(int(sender_id))
                        
                        print(f"Looked up: sender={sender_name}, recipient={recipient_name}, recipient_game={recipient_game}, sender_game={sender_game}")
                        
                        # Use recipient's game for item lookup, sender's game for location lookup
                        item_name = self.lookup_item_name(recipient_game, int(item_id))
                        location_name = self.lookup_location_name(sender_game, int(location_id))
                        
                        print(f"Final lookup results: item_name={item_name}, location_name={location_name}")
                        
                        # Determine item type emoji based on flags
                        item_emoji = "🔑" if item_flags == 1 else "🔧"  # progression vs useful
                        
                        message = f"{item_emoji} **{sender_name}** sent **{item_name}** to **{recipient_name}**\n📍 From: {location_name}"
                        await channel.send(message)
                        
                except Exception as e:
                    print(f"Error parsing ItemSend message: {e}")
                    # Fallback to simple text
                    text = "".join([item.get("text", "") for item in data])
                    await channel.send(f"🎯 {text}")
                    
            elif msg_type in ["ItemReceive", "Hint", "Goal", "Release", "Collect", "Countdown"]:
                # For other message types, combine the text parts
                text = "".join([item.get("text", "") for item in data])
                if text:
                    await channel.send(f"🎯 {text}")
                    
            else:
                # Generic message handling
                text = "".join([item.get("text", "") for item in data])
                if text:
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
        password="Optional server password. Enter 'null' if no password is needed (default: '1440')"
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
            password = "1440"  # Default password

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

        await apworld.save(f"{self.ap_directory}/worlds/{apworld.filename}")
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

        try:
            current_players = self.list_players()
        except AttributeError:
            print("No players yet")

        if playerfile.filename.endswith(".yaml"):
            await interaction.followup.send("Processing file")

            filepath = f"./Archipelago/players/{playerfile.filename}"
            await playerfile.save(filepath)

            # Pulling out the player name and their game from the submitted yaml file
            # Todo - Add the game each person is playing to the status file
            with open(filepath, "r", encoding="utf-8") as playeryaml:
                yaml_object = YAML(typ="safe", pure=True)
                raw_data = yaml_object.load_all(playeryaml)
                data_list = list(raw_data)

                # Setting the metadata for the rest of the comparisons from the submitted file
                for element in data_list:
                    self.player = element.get("name")
                    self.game = element.get("game")

            if self.player in current_players:
                if filepath == current_players[self.player]:
                    await self.upload_success(filepath, interaction)
                else:
                    # Todo - setup the secondary edit https://stackoverflow.com/questions/75009840/how-i-can-edit
                    #  -followup-message-discord-py
                    await interaction.channel.send(
                        f"{self.player} already exists in another file. "
                        "Remove the second file before submitting again"
                    )
                    remove(filepath)

            else:
                await self.upload_success(filepath, interaction)
                with open("game_status.txt", "a+") as status_file:
                    capital_player = self.player.capitalize()
                    status_file.write(f"{capital_player}:{filepath}\n")

        else:
            await interaction.followup.send(
                f"File supplied is not a yaml file. Check that you uploaded the "
                f"correct file and try again"
            )

    """
    /ap start - Generates the game files from uploaded player files and then starts the server. Optionally
                takes in a pre-generated file to start up.
                
    Parameters: [Optional] apfile: Generated .zip file from Archipelago to start with the server
    
    """

    # Todo - Find out how to connect the bot to the server + channel for status messages
    # https://github.com/LegendaryLinux/ArchipelaBot
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
            "Attempting to start Archipelago server. This will take 2 minutes"
        )

        # Clean up existing files - this is a port from the update command later in the file
        def outputfiles():
            return listdir(self.output_directory)

        for file in outputfiles():
            remove(f"{self.output_directory}/{file}")

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
            
            # Start the generation process and wait for it to complete
            process = await asyncio.create_subprocess_exec(
                "python", "./Archipelago/Generate.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                await interaction.edit_original_response(
                    content=f"Generation failed with error: {stderr.decode()}"
                )
                return

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

        await interaction.edit_original_response(
            content="Archipelago server started.\nServer: ap.rhelys.com\nPort: 38281\nPassword: 1440"
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
                    await interaction.channel.send(
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

    @app_commands.command(
        name="spoiler", description="Pulls the spoiler log from the current game"
    )
    async def ap_spoiler(self, interaction: discord.Interaction):
        await interaction.response.defer()

        def outputfiles():
            return listdir(self.output_directory)

        for file in outputfiles():
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

        for endfile in outputfiles():
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
        description="Deletes player's file from the staged files. "
        " Returns list of current players without a selection.",
    )
    @app_commands.describe(player="Removes yaml file for selected player from the game")
    async def ap_leave(self, interaction: discord.Interaction, player: str):
        file_lines = []
        player_dict = self.list_players()

        if player.lower() == "all":
            for player_line in player_dict:
                remove(player_dict[player_line])

            remove(self.status_file)

            with open(self.status_file, "x") as file:
                await interaction.response.send_message(
                    f"All player files have been deleted"
                )

        else:
            try:
                remove(player_dict[player.capitalize()])
            except KeyError:
                await interaction.response.send_message(
                    f"{player.capitalize()} is not in the game. Check current players with /ap status"
                )
                return

            with open(self.status_file, "r") as player_list:
                file_lines = player_list.readlines()

            with open(self.status_file, "w") as player_list:
                for line in file_lines:
                    if not line.startswith(player.capitalize()):
                        player_list.write(line)

            await interaction.response.send_message(
                f"{player.capitalize()}'s file has been deleted"
            )

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
                await interaction.followup.send(
                    f"✅ Successfully stopped Archipelago server.\n"
                    f"Killed processes: {', '.join(map(str, killed_processes))}"
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
            
            await interaction.edit_original_response(
                content="✅ Archipelago server restarted successfully!\n"
                        "Server: ap.rhelys.com\nPort: 38281\nPassword: 1440"
            )
            
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ Failed to restart server: {str(e)}"
            )

    @app_commands.command(
        name="progress",
        description="Shows location check progress for all players in the current game",
    )
    async def ap_progress(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Check if we have connection data (players)
        if not self.connection_data:
            await interaction.followup.send("❌ No active game connection found. Use `/ap track` to connect to a server first.")
            return
        
        # Check if we have game data (location information)
        if not self.game_data:
            await interaction.followup.send("❌ No game data available. Make sure the bot is connected to an active Archipelago server.")
            return
        
        # Try to load progress from .apsave file
        save_data = self.load_apsave_data()
        if not save_data:
            await interaction.followup.send("❌ Could not load save data. Make sure the Archipelago server is running and has a save file.")
            return
        
        progress_lines = []
        progress_lines.append("📊 **Player Progress Report**\n")
        
        # Get all players from connection data
        all_players = {}
        for server_key, conn_data in self.connection_data.items():
            slot_info = conn_data.get("slot_info", {})
            for slot_id, player_info in slot_info.items():
                player_id = int(slot_id)
                all_players[player_id] = {
                    "name": player_info.get("name", f"Player {player_id}"),
                    "game": player_info.get("game", "Unknown")
                }
        
        if not all_players:
            await interaction.followup.send("❌ No players found in the current game.")
            return
        
        # Get location checks from save data
        location_checks = save_data.get("location_checks", {})
        
        # Calculate progress for each player
        for player_id, player_info in all_players.items():
            player_name = player_info["name"]
            player_game = player_info["game"]
            
            # Get checked locations for this player from save data
            # location_checks format: {(team, slot): set of location_ids}
            checked_locations = location_checks.get((0, player_id), set())  # Assuming team 0
            checked_count = len(checked_locations)
            
            # Get total locations for this player's game
            total_locations = 0
            if player_game in self.game_data:
                game_data = self.game_data[player_game]
                location_mapping = game_data.get("location_name_to_id", {})
                total_locations = len(location_mapping)
            
            # Calculate percentage
            if total_locations > 0:
                percentage = (checked_count / total_locations) * 100
                progress_bar = self.create_progress_bar(percentage)
                progress_lines.append(
                    f"**{player_name}** ({player_game})\n"
                    f"└ {checked_count}/{total_locations} locations ({percentage:.1f}%)\n"
                    f"└ {progress_bar}\n"
                )
            else:
                progress_lines.append(
                    f"**{player_name}** ({player_game})\n"
                    f"└ {checked_count}/? locations (No location data available)\n"
                )
        
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
    
    def load_apsave_data(self):
        """Load and parse the .apsave file to get current game state"""
        import pickle
        import zlib
        import sys
        from pathlib import Path
        
        # Look for .apsave files in the output directory
        output_path = Path(self.output_directory)
        apsave_files = list(output_path.glob("*.apsave"))
        
        if not apsave_files:
            print("No .apsave files found in output directory")
            return None
        
        # Use the most recent .apsave file
        apsave_file = max(apsave_files, key=lambda f: f.stat().st_mtime)
        
        try:
            # Add the Archipelago directory to Python path temporarily
            archipelago_path = str(Path(self.ap_directory).resolve())
            if archipelago_path not in sys.path:
                sys.path.insert(0, archipelago_path)
            
            try:
                with open(apsave_file, 'rb') as f:
                    compressed_data = f.read()
                
                # Decompress and unpickle the save data
                decompressed_data = zlib.decompress(compressed_data)
                save_data = pickle.loads(decompressed_data)
                
                print(f"Successfully loaded save data from {apsave_file}")
                return save_data
                
            finally:
                # Remove the Archipelago path from sys.path
                if archipelago_path in sys.path:
                    sys.path.remove(archipelago_path)
            
        except Exception as e:
            print(f"Error loading .apsave file {apsave_file}: {e}")
            
            # Try alternative approach: parse the raw data structure
            try:
                return self.parse_apsave_alternative(apsave_file)
            except Exception as alt_e:
                print(f"Alternative parsing also failed: {alt_e}")
                return None
    
    def parse_apsave_alternative(self, apsave_file):
        """Alternative method to parse .apsave file without full Archipelago dependencies"""
        import pickle
        import zlib
        from pathlib import Path
        
        # Create a custom unpickler that can handle missing modules
        class SafeUnpickler(pickle.Unpickler):
            def find_class(self, module, name):
                # Handle NetUtils classes by creating simple replacements
                if module == 'NetUtils':
                    if name == 'NetworkItem':
                        # Create a simple class to hold NetworkItem data
                        class NetworkItem:
                            def __init__(self, item, location, player, flags=0):
                                self.item = item
                                self.location = location
                                self.player = player
                                self.flags = flags
                        return NetworkItem
                    elif name == 'Hint':
                        # Create a simple class to hold Hint data
                        class Hint:
                            def __init__(self, *args, **kwargs):
                                pass
                        return Hint
                    else:
                        # For other NetUtils classes, create a generic placeholder
                        class GenericNetUtilsClass:
                            def __init__(self, *args, **kwargs):
                                pass
                        return GenericNetUtilsClass
                
                # For other missing modules, try to import normally
                try:
                    return super().find_class(module, name)
                except (ImportError, AttributeError):
                    # If we can't import it, create a generic placeholder
                    class GenericClass:
                        def __init__(self, *args, **kwargs):
                            pass
                    return GenericClass
        
        try:
            with open(apsave_file, 'rb') as f:
                compressed_data = f.read()
            
            # Decompress the data
            decompressed_data = zlib.decompress(compressed_data)
            
            # Use our custom unpickler
            import io
            unpickler = SafeUnpickler(io.BytesIO(decompressed_data))
            save_data = unpickler.load()
            
            print(f"Successfully loaded save data using alternative method from {apsave_file}")
            return save_data
            
        except Exception as e:
            print(f"Alternative parsing failed: {e}")
            raise e
    
    def create_progress_bar(self, percentage: float, length: int = 20) -> str:
        """Create a visual progress bar"""
        filled_length = int(length * percentage / 100)
        bar = "█" * filled_length + "░" * (length - filled_length)
        return f"[{bar}] {percentage:.1f}%"

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
