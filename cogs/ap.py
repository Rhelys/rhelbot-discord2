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

    def _lookup_in_mapping(self, mapping: dict, lookup_id: int, mapping_name: str) -> str:
        """Generic lookup function for ID to name mappings"""
        for name, id_value in mapping.items():
            if str(id_value) == str(lookup_id):
                print(f"Found match: {name}")
                return name
        return None

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
        
        result = self._lookup_in_mapping(item_mapping, item_id, "item")
        if result:
            return result
        
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
        
        result = self._lookup_in_mapping(location_mapping, location_id, "location")
        if result:
            return result
        
        print(f"No match found for location ID {location_id}")
        return f"Location {location_id}"
    
    def _lookup_player_info(self, player_id: int, info_key: str, default_value: str) -> str:
        """Generic function to look up player information from connection data"""
        for server_url, conn_data in self.connection_data.items():
            slot_info = conn_data.get("slot_info", {})
            for slot_id, player_info in slot_info.items():
                if str(slot_id) == str(player_id):
                    return player_info.get(info_key, default_value)
        return default_value
    
    def lookup_player_name(self, player_id: int) -> str:
        """Look up player name from ID using connection data"""
        return self._lookup_player_info(player_id, "name", f"Player {player_id}")
    
    def lookup_player_game(self, player_id: int) -> str:
        """Look up player's game from ID using connection data"""
        return self._lookup_player_info(player_id, "game", "Unknown")

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
                    
                    # Track location check for progress tracking (but don't send message)
                    if sender_id and location_id:
                        sender_id_int = int(sender_id)
                        location_id_int = int(location_id)
                        
                        # Initialize player progress if not exists
                        if sender_id_int not in self.player_progress:
                            self.player_progress[sender_id_int] = set()
                        
                        # Add this location to the player's checked locations
                        self.player_progress[sender_id_int].add(location_id_int)
                        print(f"Tracked location check: Player {sender_id_int} checked location {location_id_int}")
                    
                    # Skip sending messages about player item sends/receives
                    print(f"Skipping ItemSend message from player {sender_id} to player {recipient_id}")
                        
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
                # Keep server messages and tutorials
                text = "".join([item.get("text", "") for item in data])
                if text:
                    await channel.send(f"ℹ️ {text}")
                    
            else:
                # For other message types, check if they're player-related before sending
                text = "".join([item.get("text", "") for item in data])
                if text:
                    # Skip messages that appear to be player-related
                    text_lower = text.lower()
                    if any(keyword in text_lower for keyword in ["player", "sent", "received", "found", "checked"]):
                        print(f"Skipping player-related message: {text}")
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
            "Attempting to start Archipelago server, hold please...\nError messages will be sent to this channel"
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
            
            # Start the generation process in an interactive window so user can watch progress
            import time
            start_time = time.time()
            
            # Run the generation in a new interactive command window
            process = subprocess.Popen([
                "cmd", "/c", "start", "cmd", "/k", 
                "python", "./Archipelago/Generate.py", "&&", "pause"
            ], shell=True)
            
            print(f"Started generation process with PID: {process.pid}")
            
            # Wait for the process to complete
            while process.poll() is None:
                await sleep(5)  # Check every 5 seconds
            
            # Check if generation completed successfully by looking for output files
            import time
            await sleep(3)  # Give files time to be created
            
            output_files = listdir(self.output_directory)
            zip_files = [f for f in output_files if f.endswith('.zip')]
            
            if not zip_files:
                await interaction.edit_original_response(
                    content="Generation failed - no output file was created. Check the generation window for details."
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
        server_message = "Archipelago server started.\nServer: ap.rhelys.com\nPort: 38281\nPassword: 1440"
        await interaction.edit_original_response(content=server_message)

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
        
        # Check if server is running first
        server_running = self.is_server_running()
        
        if not server_running:
            await interaction.followup.send("❌ Archipelago server is not running. Use `/ap start` to start the server first.")
            return
        
        # Try to load progress from .apsave file
        save_data = self.load_apsave_data()
        if not save_data:
            await interaction.followup.send("❌ Could not load save data. Make sure the Archipelago server has a save file.")
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
        progress_lines.append("📊 **Player Progress Report**\n")
        
        # Get location checks from save data
        location_checks = save_data.get("location_checks", {})
        
        # Filter out "Rhelbot" and create a list for sorting
        player_progress_data = []
        
        for player_id, player_info in all_players.items():
            player_name = player_info["name"]
            player_game = player_info["game"]
            
            # Skip the Rhelbot tracker
            if player_name.lower() == "rhelbot":
                continue
            
            # Get checked locations for this player from save data
            # location_checks format: {(team, slot): set of location_ids}
            checked_locations = location_checks.get((0, player_id), set())  # Assuming team 0
            checked_count = len(checked_locations)
            
            # Get total locations for this player from the actual multiworld data
            total_locations = self.get_player_total_locations(player_id, save_data)
            
            # Calculate percentage
            if total_locations > 0:
                percentage = (checked_count / total_locations) * 100
                is_complete = percentage >= 100.0
                
                # Add checkmark for 100% completion after the game name
                completion_indicator = " ✅" if is_complete else ""
                
                progress_bar = self.create_progress_bar(percentage)
                player_line = (
                    f"**{player_name}** ({player_game}) {completion_indicator}\n"
                    f"└ {checked_count}/{total_locations} locations ({percentage:.1f}%)\n"
                    f"└ {progress_bar}\n"
                )
            else:
                player_line = (
                    f"**{player_name}** ({player_game})\n"
                    f"└ {checked_count}/? locations (No location data available)\n"
                )
            
            # Store for sorting
            player_progress_data.append((player_name.lower(), player_line))
        
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
            player_total_locations = self.get_player_total_locations(player_id, save_data)
            
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
                        # Create a proper Hint class that matches the NetUtils.Hint structure
                        # NetUtils.Hint is a NamedTuple, so we need to handle it differently
                        from collections import namedtuple
                        
                        # Create a NamedTuple-like class that can handle pickle reconstruction
                        class Hint(namedtuple('Hint', ['receiving_player', 'finding_player', 'location', 'item', 'found', 'entrance', 'item_flags', 'status'])):
                            def __new__(cls, receiving_player=0, finding_player=0, location=0, item=0, found=False, entrance="", item_flags=0, status=0):
                                return super().__new__(cls, receiving_player, finding_player, location, item, found, entrance, item_flags, status)
                            
                            def __repr__(self):
                                return f"Hint(receiving_player={self.receiving_player}, finding_player={self.finding_player}, location={self.location}, item={self.item}, found={self.found}, item_flags={self.item_flags})"
                        
                        return Hint
                    elif name == 'HintStatus':
                        # Create a proper HintStatus enum-like class that handles pickle reconstruction
                        class HintStatus:
                            NO_HINT = 0
                            HINT = 1
                            PRIORITY = 2
                            AVOID = 3
                            
                            def __init__(self, value=0):
                                self.value = value
                            
                            def __new__(cls, value=0):
                                # Handle both normal construction and pickle reconstruction
                                obj = object.__new__(cls)
                                obj.value = value
                                return obj
                            
                            def __reduce__(self):
                                # Support for pickle
                                return (self.__class__, (self.value,))
                            
                            def __repr__(self):
                                status_names = {0: 'NO_HINT', 1: 'HINT', 2: 'PRIORITY', 3: 'AVOID'}
                                return f"HintStatus.{status_names.get(self.value, 'UNKNOWN')}"
                        return HintStatus
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
    
    def is_server_running(self) -> bool:
        """Check if the Archipelago server is currently running"""
        try:
            import psutil
            
            # Check for MultiServer.py processes
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if (proc.info['name'] and 'python' in proc.info['name'].lower() and 
                        proc.info['cmdline'] and any('MultiServer.py' in arg for arg in proc.info['cmdline'])):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
                    
        except ImportError:
            # Fallback: check if tracked process is still running
            if self.server_process:
                try:
                    # Check if process is still running
                    return self.server_process.poll() is None
                except:
                    pass
        
        return False
    
    def extract_player_data_from_save(self, save_data):
        """Extract player and game data from save file when websocket connection is not available"""
        all_players = {}
        game_data = {}
        
        # Try to extract player names from connect_names in save data
        connect_names = save_data.get("connect_names", {})
        
        # connect_names format: {player_name: (team, slot)}
        for player_name, (team, slot) in connect_names.items():
            if team == 0:  # Assuming team 0
                all_players[slot] = {
                    "name": player_name,
                    "game": "Unknown"  # We can't easily get game info from save file alone
                }
        
        # If we couldn't get players from connect_names, try to infer from location_checks
        if not all_players:
            location_checks = save_data.get("location_checks", {})
            for (team, slot) in location_checks.keys():
                if team == 0:  # Assuming team 0
                    all_players[slot] = {
                        "name": f"Player {slot}",
                        "game": "Unknown"
                    }
        
        # Note: We can't easily extract game data from the save file since it doesn't contain
        # the full DataPackage. This would require loading the original .archipelago file.
        # For now, we'll return empty game_data and show progress without total counts.
        
        return all_players, game_data
    
    def _create_connection_message(self, password: str = None) -> dict:
        """Create a standard Archipelago connection message"""
        import uuid
        return {
            "cmd": "Connect",
            "game": "",
            "password": password,
            "name": "Rhelbot",
            "version": {"major": 0, "minor": 6, "build": 0, "class": "Version"},
            "tags": ["Tracker"],
            "items_handling": 0b000,
            "uuid": uuid.getnode()
        }

    async def _connect_to_server(self, server_url: str, timeout: float = 15.0):
        """Create a websocket connection to the Archipelago server"""
        return await asyncio.wait_for(
            websockets.connect(
                server_url, 
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10,
                max_size=None,
                compression="deflate"
            ),
            timeout=timeout
        )

    async def fetch_server_data(self, server_url: str = "ws://ap.rhelys.com:38281", password: str = "1440"):
        """Connect to server temporarily to fetch player and game data"""
        try:
            print(f"Attempting to fetch server data from {server_url}")
            
            # Connect to the Archipelago websocket server
            websocket = await self._connect_to_server(server_url)
            
            try:
                # Send connection message
                connect_msg = self._create_connection_message(password)
                await websocket.send(json.dumps([connect_msg]))
                print("Sent connection message for data fetch")
                
                # Wait for connection confirmation and collect data
                connection_data = None
                game_data = {}
                timeout_counter = 0
                max_timeout = 30  # 30 seconds total timeout
                
                while timeout_counter < max_timeout:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        data = json.loads(message)
                        
                        for msg in data:
                            cmd = msg.get("cmd", "")
                            
                            if cmd == "Connected":
                                print("Connected to server for data fetch")
                                connection_data = msg
                                
                                # Request DataPackage for games in use
                                slot_info = msg.get("slot_info", {})
                                games_in_use = list(set(player_info.get("game", "") for player_info in slot_info.values()))
                                games_in_use = [game for game in games_in_use if game]
                                
                                if games_in_use:
                                    get_data_msg = {"cmd": "GetDataPackage", "games": games_in_use}
                                    print(f"Requesting DataPackage for games: {games_in_use}")
                                else:
                                    get_data_msg = {"cmd": "GetDataPackage"}
                                    print("Requesting full DataPackage")
                                
                                await websocket.send(json.dumps([get_data_msg]))
                                
                            elif cmd == "ConnectionRefused":
                                print(f"Connection refused: {msg.get('errors', [])}")
                                return None
                                
                            elif cmd == "DataPackage":
                                print("Received DataPackage")
                                games = msg.get("data", {}).get("games", {})
                                game_data = games
                                
                                # If we have both connection data and game data, we're done
                                if connection_data and game_data:
                                    break
                        
                        # If we have both pieces of data, break out of the timeout loop
                        if connection_data and game_data:
                            break
                            
                    except asyncio.TimeoutError:
                        timeout_counter += 1
                        continue
                
                # Process the collected data
                if connection_data:
                    all_players = {}
                    slot_info = connection_data.get("slot_info", {})
                    
                    for slot_id, player_info in slot_info.items():
                        player_id = int(slot_id)
                        all_players[player_id] = {
                            "name": player_info.get("name", f"Player {player_id}"),
                            "game": player_info.get("game", "Unknown")
                        }
                    
                    print(f"Successfully fetched data for {len(all_players)} players and {len(game_data)} games")
                    return {
                        "players": all_players,
                        "game_data": game_data
                    }
                else:
                    print("Failed to get connection data from server")
                    return None
                    
            finally:
                await websocket.close()
                
        except Exception as e:
            print(f"Error fetching server data: {e}")
            return None
    
    def get_player_total_locations(self, player_id: int, save_data: dict) -> int:
        """Get the actual total number of locations for a specific player from the multiworld data"""
        try:
            print(f"DEBUG: Attempting to get total locations for player {player_id}")
            print(f"DEBUG: Available save_data keys: {list(save_data.keys())}")
            
            # Method 1: Check the multiworld object for location data
            if "multiworld" in save_data:
                multiworld = save_data["multiworld"]
                print(f"DEBUG: Found multiworld object, type: {type(multiworld)}")
                print(f"DEBUG: Multiworld attributes: {[attr for attr in dir(multiworld) if not attr.startswith('_')]}")
                
                try:
                    # Try to access worlds array
                    if hasattr(multiworld, 'worlds'):
                        print(f"DEBUG: Found worlds attribute, length: {len(multiworld.worlds) if hasattr(multiworld.worlds, '__len__') else 'unknown'}")
                        if len(multiworld.worlds) > player_id:
                            world = multiworld.worlds[player_id]
                            print(f"DEBUG: Found world for player {player_id}, type: {type(world)}")
                            print(f"DEBUG: World attributes: {[attr for attr in dir(world) if not attr.startswith('_')]}")
                            
                            # Try different location attributes
                            if hasattr(world, 'location_table'):
                                locations = world.location_table
                                print(f"DEBUG: Found location_table with {len(locations)} locations")
                                return len(locations)
                            elif hasattr(world, 'locations'):
                                locations = world.locations
                                print(f"DEBUG: Found locations with {len(locations)} locations")
                                return len(locations)
                            elif hasattr(world, 'location_count'):
                                count = world.location_count
                                print(f"DEBUG: Found world.location_count: {count}")
                                return count
                    
                    # Try to access location counts directly from multiworld
                    if hasattr(multiworld, 'location_count'):
                        print(f"DEBUG: Found multiworld.location_count, type: {type(multiworld.location_count)}")
                        if hasattr(multiworld.location_count, '__getitem__'):
                            try:
                                count = multiworld.location_count[player_id]
                                print(f"DEBUG: Found location_count[{player_id}]: {count}")
                                return count
                            except (KeyError, IndexError) as e:
                                print(f"DEBUG: Could not access location_count[{player_id}]: {e}")
                    
                    # Try to get all locations and count those belonging to this player
                    if hasattr(multiworld, 'get_locations'):
                        try:
                            all_locations = multiworld.get_locations()
                            player_locations = [loc for loc in all_locations if getattr(loc, 'player', None) == player_id]
                            if player_locations:
                                print(f"DEBUG: Found {len(player_locations)} locations via get_locations() for player {player_id}")
                                return len(player_locations)
                        except Exception as e:
                            print(f"DEBUG: get_locations() failed: {e}")
                    
                except Exception as e:
                    print(f"DEBUG: Error accessing multiworld data: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print("DEBUG: No 'multiworld' key found in save_data")
            
            # Method 2: Look for location-related data structures in save_data
            # Note: location_checks contains CHECKED locations, not total locations
            location_related_keys = [key for key in save_data.keys() if 'location' in key.lower()]
            print(f"DEBUG: Location-related keys in save_data: {location_related_keys}")
            
            for key in location_related_keys:
                value = save_data[key]
                print(f"DEBUG: Examining {key}, type: {type(value)}")
                
                # Skip location_checks as it contains checked locations, not total locations
                if key == 'location_checks':
                    if isinstance(value, dict):
                        player_keys = [k for k in value.keys() if isinstance(k, tuple) and len(k) == 2 and k[1] == player_id]
                        if player_keys:
                            player_data = value[player_keys[0]]
                            if isinstance(player_data, (list, set)):
                                print(f"DEBUG: Found {len(player_data)} CHECKED locations in {key} for player {player_id} (not total)")
                        elif player_id in value:
                            player_data = value[player_id]
                            if isinstance(player_data, (list, set)):
                                print(f"DEBUG: Found {len(player_data)} CHECKED locations in {key}[{player_id}] (not total)")
                    continue
                
                # Look for other location-related data that might contain totals
                if isinstance(value, dict):
                    # Look for player-specific data
                    player_keys = [k for k in value.keys() if isinstance(k, tuple) and len(k) == 2 and k[1] == player_id]
                    if player_keys:
                        player_data = value[player_keys[0]]
                        if isinstance(player_data, (list, set)):
                            print(f"DEBUG: Found {len(player_data)} locations in {key} for player {player_id}")
                            return len(player_data)
                    
                    # Also check for direct player_id keys
                    if player_id in value:
                        player_data = value[player_id]
                        if isinstance(player_data, (list, set)):
                            print(f"DEBUG: Found {len(player_data)} locations in {key}[{player_id}]")
                            return len(player_data)
            
            # Method 3: Try to extract from archipelago file if it exists
            archipelago_file = self.find_archipelago_file()
            if archipelago_file:
                print(f"DEBUG: Found .archipelago file: {archipelago_file}")
                location_count = self.get_locations_from_archipelago_file(archipelago_file, player_id)
                if location_count > 0:
                    print(f"DEBUG: Got {location_count} locations from .archipelago file")
                    return location_count
            else:
                print("DEBUG: No .archipelago file found")
            
            print(f"DEBUG: Could not determine total locations for player {player_id}")
            return 0
            
        except Exception as e:
            print(f"DEBUG: Error getting total locations for player {player_id}: {e}")
            import traceback
            traceback.print_exc()
            return 0
    
    def find_archipelago_file(self):
        """Find the .archipelago file in the output directory or extract it from donkey.zip"""
        from pathlib import Path
        import zipfile
        
        output_path = Path(self.output_directory)
        
        # First check if .archipelago file already exists in output directory
        archipelago_files = list(output_path.glob("*.archipelago"))
        if archipelago_files:
            # Return the most recent .archipelago file
            return max(archipelago_files, key=lambda f: f.stat().st_mtime)
        
        # If not found, try to extract it from donkey.zip
        donkey_zip_path = output_path / "donkey.zip"
        if donkey_zip_path.exists():
            try:
                print(f"DEBUG: Looking for .archipelago file in {donkey_zip_path}")
                with zipfile.ZipFile(donkey_zip_path, 'r') as zip_file:
                    # Look for .archipelago files in the zip
                    archipelago_files_in_zip = [f for f in zip_file.namelist() if f.endswith('.archipelago')]
                    
                    if archipelago_files_in_zip:
                        archipelago_file_in_zip = archipelago_files_in_zip[0]
                        print(f"DEBUG: Found {archipelago_file_in_zip} in donkey.zip")
                        
                        # Extract it to the output directory
                        extracted_path = output_path / Path(archipelago_file_in_zip).name
                        with zip_file.open(archipelago_file_in_zip) as source:
                            with open(extracted_path, 'wb') as target:
                                target.write(source.read())
                        
                        print(f"DEBUG: Extracted .archipelago file to {extracted_path}")
                        return extracted_path
                    else:
                        print("DEBUG: No .archipelago file found in donkey.zip")
                        
            except Exception as e:
                print(f"DEBUG: Error extracting .archipelago file from donkey.zip: {e}")
        else:
            print("DEBUG: donkey.zip not found")
        
        return None
    
    def get_locations_from_archipelago_file(self, archipelago_file, player_id: int) -> int:
        """Extract location count for a specific player from the .archipelago file"""
        try:
            import zlib
            import pickle
            import io
            
            with open(archipelago_file, 'rb') as f:
                raw_data = f.read()
            
            # .archipelago files are zlib compressed pickle files with a 1-byte header
            # Skip the first byte and decompress with zlib, then unpickle
            try:
                skipped_data = raw_data[1:]  # Skip first byte
                decompressed_data = zlib.decompress(skipped_data)
                
                # Use a custom unpickler that can handle missing modules
                class ArchipelagoUnpickler(pickle.Unpickler):
                    def find_class(self, module, name):
                        # Handle missing modules by creating generic placeholders
                        if module in ['NetUtils', 'worlds', 'BaseClasses']:
                            # Create a generic class that can hold data
                            class GenericClass:
                                def __init__(self, *args, **kwargs):
                                    # Store all arguments as attributes
                                    for i, arg in enumerate(args):
                                        setattr(self, f'arg_{i}', arg)
                                    for key, value in kwargs.items():
                                        setattr(self, key, value)
                                
                                def __getitem__(self, key):
                                    # Allow dictionary-like access
                                    return getattr(self, key, None)
                                
                                def __setitem__(self, key, value):
                                    setattr(self, key, value)
                                
                                def get(self, key, default=None):
                                    return getattr(self, key, default)
                                
                                def keys(self):
                                    return [attr for attr in dir(self) if not attr.startswith('_')]
                                
                                def values(self):
                                    return [getattr(self, attr) for attr in self.keys()]
                                
                                def items(self):
                                    return [(attr, getattr(self, attr)) for attr in self.keys()]
                                
                                def __len__(self):
                                    return len(self.keys())
                                
                                def __contains__(self, key):
                                    return hasattr(self, key)
                            
                            return GenericClass
                        
                        # For other modules, try to import normally
                        try:
                            return super().find_class(module, name)
                        except (ImportError, AttributeError):
                            # If we can't import it, create a generic placeholder
                            class GenericClass:
                                def __init__(self, *args, **kwargs):
                                    pass
                            return GenericClass
                
                unpickler = ArchipelagoUnpickler(io.BytesIO(decompressed_data))
                multidata = unpickler.load()
                
                print(f"DEBUG: Successfully parsed .archipelago file, type: {type(multidata)}")
                
            except Exception as e:
                print(f"Error parsing .archipelago file: {e}")
                return 0
            
            # Look for location data in the multidata
            print(f"DEBUG: Looking for location data in multidata")
            if hasattr(multidata, '__getitem__') or isinstance(multidata, dict):
                # Try different ways to access the data
                locations_data = None
                
                # Method 1: Direct 'locations' key
                try:
                    if 'locations' in multidata:
                        locations_data = multidata['locations']
                        print(f"DEBUG: Found 'locations' key, type: {type(locations_data)}")
                except:
                    pass
                
                # Method 2: Check for location_table or similar
                if not locations_data:
                    for key in ['location_table', 'location_tables', 'world_locations']:
                        try:
                            if key in multidata:
                                locations_data = multidata[key]
                                print(f"DEBUG: Found '{key}' key, type: {type(locations_data)}")
                                break
                        except:
                            continue
                
                # Method 3: Look through all keys for location-related data
                if not locations_data:
                    try:
                        if hasattr(multidata, 'keys'):
                            all_keys = list(multidata.keys())
                        elif hasattr(multidata, '__dict__'):
                            all_keys = list(multidata.__dict__.keys())
                        else:
                            all_keys = []
                        
                        print(f"DEBUG: All keys in multidata: {all_keys}")
                        
                        for key in all_keys:
                            if 'location' in str(key).lower():
                                try:
                                    locations_data = multidata[key]
                                    print(f"DEBUG: Found location-related key '{key}', type: {type(locations_data)}")
                                    break
                                except:
                                    continue
                    except Exception as e:
                        print(f"DEBUG: Error examining multidata keys: {e}")
                
                # Now try to extract player-specific location count
                if locations_data:
                    print(f"DEBUG: Processing locations_data for player {player_id}")
                    
                    # If locations_data is a dict-like object
                    if hasattr(locations_data, '__getitem__'):
                        # Try different player ID formats
                        for pid in [player_id, str(player_id), (0, player_id)]:
                            try:
                                if pid in locations_data:
                                    player_locations = locations_data[pid]
                                    if hasattr(player_locations, '__len__'):
                                        count = len(player_locations)
                                        print(f"DEBUG: Found {count} locations for player {player_id} using key {pid}")
                                        return count
                            except Exception as e:
                                print(f"DEBUG: Error accessing locations_data[{pid}]: {e}")
                                continue
                    
                    # If locations_data is a list or other iterable
                    elif hasattr(locations_data, '__len__'):
                        try:
                            if len(locations_data) > player_id:
                                player_locations = locations_data[player_id]
                                if hasattr(player_locations, '__len__'):
                                    count = len(player_locations)
                                    print(f"DEBUG: Found {count} locations for player {player_id} from list index")
                                    return count
                        except Exception as e:
                            print(f"DEBUG: Error accessing locations_data[{player_id}]: {e}")
                
                print(f"DEBUG: Could not find location data for player {player_id}")
            else:
                print(f"DEBUG: multidata is not dict-like: {type(multidata)}")
            
            return 0
                
        except Exception as e:
            print(f"Error reading .archipelago file: {e}")
            import traceback
            traceback.print_exc()
            return 0
    
    def create_progress_bar(self, percentage: float, length: int = 20) -> str:
        """Create a visual progress bar"""
        filled_length = int(length * percentage / 100)
        bar = "█" * filled_length + "░" * (length - filled_length)
        return f"[{bar}] {percentage:.1f}%"
    
    def is_priority_status(self, status) -> bool:
        """
        Determine if a hint status indicates priority.
        Based on Archipelago's hint status system:
        - Found hints are always included (handled separately)
        - Priority hints should be included
        - "No Priority" and "Avoid" hints should be excluded
        
        Status values in Archipelago (from NetUtils.py):
        - HintStatus.NO_HINT = 0
        - HintStatus.HINT = 1  
        - HintStatus.PRIORITY = 2
        - HintStatus.AVOID = 3
        """
        # Handle different status representations
        if hasattr(status, 'value'):
            # If it's an enum-like object, get the value
            status_value = status.value
        elif isinstance(status, int):
            # If it's already an integer
            status_value = status
        else:
            # If it's something else, try to convert or default to 0
            try:
                status_value = int(status)
            except (ValueError, TypeError):
                status_value = 0
        
        # Include hints with HINT (1) or PRIORITY (2) status
        # Exclude NO_HINT (0) and AVOID (3) status
        return status_value in [1, 2]
    
    def get_player_hint_points(self, player_id: int, save_data: dict) -> int:
        """Get the current hint points for a specific player"""
        try:
            # In Archipelago, hint points are calculated as:
            # total_locations * hint_points_percentage - hints_used * hint_cost
            # But since we don't have the exact formula, we'll calculate based on available data
            
            # Method 1: Check if there's a direct hint_points field in save_data
            if "hint_points" in save_data:
                hint_points = save_data["hint_points"]
                if isinstance(hint_points, dict):
                    # Check for player-specific hint points
                    if player_id in hint_points:
                        return hint_points[player_id]
                    elif str(player_id) in hint_points:
                        return hint_points[str(player_id)]
                    # Check for (team, slot) format
                    elif (0, player_id) in hint_points:
                        return hint_points[(0, player_id)]
            
            # Method 2: Calculate based on hints_used and checked locations
            # Based on feedback: hint_points = checked_locations - (hints_used * hint_cost)
            hints_used_data = save_data.get("hints_used", {})
            hints_used = 0
            
            # Check for hints used by this player
            if (0, player_id) in hints_used_data:
                hints_used = hints_used_data[(0, player_id)]
            elif player_id in hints_used_data:
                hints_used = hints_used_data[player_id]
            elif str(player_id) in hints_used_data:
                hints_used = hints_used_data[str(player_id)]
            
            print(f"DEBUG: Player {player_id} has used {hints_used} hints")
            
            # Get checked locations for this player
            location_checks = save_data.get("location_checks", {})
            checked_locations = 0
            
            # Check for checked locations by this player
            if (0, player_id) in location_checks:
                checked_locations_set = location_checks[(0, player_id)]
                checked_locations = len(checked_locations_set) if hasattr(checked_locations_set, '__len__') else 0
            elif player_id in location_checks:
                checked_locations_set = location_checks[player_id]
                checked_locations = len(checked_locations_set) if hasattr(checked_locations_set, '__len__') else 0
            elif str(player_id) in location_checks:
                checked_locations_set = location_checks[str(player_id)]
                checked_locations = len(checked_locations_set) if hasattr(checked_locations_set, '__len__') else 0
            
            print(f"DEBUG: Player {player_id} has checked {checked_locations} locations")
            
            # Get total locations for this player to calculate hint cost
            total_locations = self.get_player_total_locations(player_id, save_data)
            
            if total_locations > 0:
                # Calculate hint cost (5% of total locations per hint as we determined earlier)
                hint_cost = max(10, int(total_locations * 0.05))
                
                # Calculate hint points based on the correct formula:
                # hint_points = checked_locations - (hints_used * hint_cost)
                remaining_points = checked_locations - (hints_used * hint_cost)
                
                print(f"DEBUG: Player {player_id} calculation: {checked_locations} checked - ({hints_used} * {hint_cost}) = {remaining_points}")
                
                return max(0, remaining_points)  # Don't return negative points
            
            # Method 3: Check multiworld object for hint points
            if "multiworld" in save_data:
                multiworld = save_data["multiworld"]
                if hasattr(multiworld, 'hint_points'):
                    hint_points = multiworld.hint_points
                    if hasattr(hint_points, '__getitem__'):
                        try:
                            return hint_points[player_id]
                        except (KeyError, IndexError):
                            pass
                
                # Check for worlds-specific hint points
                if hasattr(multiworld, 'worlds') and len(multiworld.worlds) > player_id:
                    world = multiworld.worlds[player_id]
                    if hasattr(world, 'hint_points'):
                        return world.hint_points
            
            # Method 4: Look for any field containing "hint" and "point"
            for key, value in save_data.items():
                if "hint" in key.lower() and "point" in key.lower():
                    if isinstance(value, dict):
                        if player_id in value:
                            return value[player_id]
                        elif str(player_id) in value:
                            return value[str(player_id)]
                        elif (0, player_id) in value:
                            return value[(0, player_id)]
            
            # Default: return 0 if no hint points found
            print(f"DEBUG: Could not calculate hint points for player {player_id}, returning 0")
            return 0
            
        except Exception as e:
            print(f"Error getting hint points for player {player_id}: {e}")
            return 0
    
    def get_hint_cost(self, player_id: int, save_data: dict) -> int:
        """Get the cost of the next hint for a specific player"""
        try:
            # Look for hint cost in the save data
            # Hint cost is typically calculated based on how many hints the player already has
            
            # Method 1: Check if there's a hint_cost or similar field in save_data
            if "hint_cost" in save_data:
                hint_cost = save_data["hint_cost"]
                if isinstance(hint_cost, dict):
                    if player_id in hint_cost:
                        return hint_cost[player_id]
                    elif str(player_id) in hint_cost:
                        return hint_cost[str(player_id)]
                    elif (0, player_id) in hint_cost:
                        return hint_cost[(0, player_id)]
            
            # Method 2: Check multiworld object for hint cost
            if "multiworld" in save_data:
                multiworld = save_data["multiworld"]
                if hasattr(multiworld, 'hint_cost'):
                    hint_cost = multiworld.hint_cost
                    if hasattr(hint_cost, '__getitem__'):
                        try:
                            return hint_cost[player_id]
                        except (KeyError, IndexError):
                            pass
                
                # Check for worlds-specific hint cost
                if hasattr(multiworld, 'worlds') and len(multiworld.worlds) > player_id:
                    world = multiworld.worlds[player_id]
                    if hasattr(world, 'hint_cost'):
                        return world.hint_cost
            
            # Method 3: Calculate hint cost based on existing hints and player's total locations
            # In Archipelago, hint cost is typically based on the percentage of locations in the player's game
            # Common formula: (total_locations_in_player_game / 100) * 10, with a minimum cost
            
            # Get the total number of locations for this player's game
            total_locations = self.get_player_total_locations(player_id, save_data)
            
            if total_locations > 0:
                # Calculate cost based on total locations in the player's game
                # Server is configured to provide a hint for every 5% of available locations
                # So hint cost = 5% of total locations, with a minimum of 10
                calculated_cost = max(10, int(total_locations * 0.05))
                print(f"DEBUG: Calculated hint cost for player {player_id}: {calculated_cost} (5% of {total_locations} total locations)")
                return calculated_cost
            else:
                # Fallback: Count how many hints this player already has and use old formula
                hints_data = save_data.get("hints", {})
                player_hint_count = 0
                
                for hint_set in hints_data.values():
                    if isinstance(hint_set, set):
                        for hint in hint_set:
                            if hasattr(hint, 'receiving_player') and hint.receiving_player == player_id:
                                player_hint_count += 1
                    elif isinstance(hint_set, (list, tuple)):
                        for hint in hint_set:
                            if hasattr(hint, 'receiving_player') and hint.receiving_player == player_id:
                                player_hint_count += 1
                
                # Use fallback formula: 10 + (hints_owned * 10)
                base_cost = 10
                increment = 10
                calculated_cost = base_cost + (player_hint_count * increment)
                print(f"DEBUG: Fallback hint cost calculation for player {player_id}: {calculated_cost} (based on {player_hint_count} existing hints)")
                return calculated_cost
            
        except Exception as e:
            print(f"Error getting hint cost for player {player_id}: {e}")
            return 10  # Default cost

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
        server_running = self.is_server_running()
        
        if not server_running:
            await interaction.followup.send("❌ Archipelago server is not running. Use `/ap start` to start the server first.")
            return
        
        # Load save data to get hints
        save_data = self.load_apsave_data()
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
                all_players, game_data = self.extract_player_data_from_save(save_data)
        
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
        
        # If a specific player is requested, filter hints and show hint points/cost
        if player:
            # Find the player ID by name (case-insensitive)
            target_player_id = None
            target_player_name = None
            
            for player_id, player_info in all_players.items():
                if player_info["name"].lower() == player.lower():
                    target_player_id = player_id
                    target_player_name = player_info["name"]
                    break
            
            if target_player_id is None:
                # List available players for reference
                available_players = [info["name"] for info in all_players.values() if info["name"].lower() != "rhelbot"]
                await interaction.followup.send(
                    f"❌ Player '{player}' not found.\n"
                    f"Available players: {', '.join(available_players)}"
                )
                return
            
            # Filter hints for this specific player (as the finding player)
            player_hints = [hint for hint in key_item_hints if hint.finding_player == target_player_id]
            
            # Get hint points and cost information (always show these)
            hint_points = self.get_player_hint_points(target_player_id, save_data)
            hint_cost = self.get_hint_cost(target_player_id, save_data)
            
            # Build the message for specific player
            hint_lines = []
            hint_lines.append(f"🔑 **Key Item Hints for {target_player_name}**")
            hint_lines.append(f"💰 **Hint Points**: {hint_points}")
            hint_lines.append(f"💸 **Next Hint Cost**: {hint_cost}")
            hint_lines.append("")
            
            if not player_hints:
                hint_lines.append("📝 No hints found for this player.")
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
