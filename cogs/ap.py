import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from os import remove, listdir, rename, path
import subprocess
import asyncio
from asyncio import sleep
import json
import zipfile
from typing import Optional, Dict
from ruyaml import YAML
import shutil
from datetime import datetime
import re
import time
import psutil

# Import all helper functions
from helpers.data_helpers import *
from helpers.lookup_helpers import *
from helpers.server_helpers import *
from helpers.formatting_helpers import *
from helpers.progress_helpers import *
from helpers.message_processors import *
from helpers.progress_display import *
from helpers.websocket_managers import *

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

        # Tracking variables - use bot instance for active_connections to persist across reloads
        # self.active_connections is kept as a property that references bot.active_ap_connections
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

    @property
    def active_connections(self):
        """Reference to bot's persistent connection storage"""
        return self.bot.active_ap_connections

    async def cog_load(self):
        """Called when the cog is loaded - restore any existing connections"""
        print("ApCog loaded - checking for existing connections...")

        # Iterate through existing connections and restore channel references
        for server_url, connection in list(self.active_connections.items()):
            try:
                # Get the channel
                channel = self.bot.get_channel(connection['channel_id'])
                if channel:
                    # Check if task is still running
                    if connection['task'].done():
                        print(f"Task for {server_url} has stopped, removing connection")
                        del self.active_connections[server_url]
                    else:
                        print(f"Restored connection to {server_url} in channel {channel.name}")
                else:
                    print(f"Channel {connection['channel_id']} not found, removing connection {server_url}")
                    connection['task'].cancel()
                    del self.active_connections[server_url]
            except Exception as e:
                print(f"Error restoring connection {server_url}: {e}")
                del self.active_connections[server_url]

        if self.active_connections:
            print(f"Restored {len(self.active_connections)} active connection(s)")
        else:
            print("No active connections to restore")

    async def cog_unload(self):
        """Called when the cog is unloaded - keep tasks running but log the state"""
        print(f"ApCog unloading - {len(self.active_connections)} connection(s) will persist")
        for server_url in self.active_connections:
            print(f"  - {server_url} (task still running)")

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

        # Delegate to the main websocket listener loop
        await websocket_listener_main_loop(
            server_url, channel, password, self.active_connections,
            self.connection_data, self.process_ap_message
        )

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
        """Process and format Archipelago messages for Discord

        Returns:
            bool: True if game completion was detected and tracking should stop, False otherwise
        """
        cmd = msg.get("cmd", "")

        # Debug: Print all received messages to console for troubleshooting
        print(f"AP Message received: {cmd} - {msg}")

        if cmd == "Connected":
            await process_connected_message(msg, channel, self.connection_data)

        elif cmd == "ConnectionRefused":
            await process_connection_refused_message(msg, channel)

        elif cmd == "ReceivedItems":
            await process_received_items_message(msg, channel)

        elif cmd == "LocationInfo":
            await process_location_info_message(msg, channel)

        elif cmd == "PrintJSON":
            # Handle chat messages and game events
            msg_type = msg.get("type", "")
            data = msg.get("data", [])

            # Skip chat messages from players
            if msg_type == "Chat":
                print(f"Skipping chat message: {data}")
                return False

            elif msg_type == "ItemSend":
                await process_item_send_message(
                    data, channel, self.player_progress, self.output_directory, self.AP_DIR,
                    self.lookup_player_name, self.lookup_player_game,
                    self.lookup_item_name, self.lookup_location_name, self.is_player_completed
                )

            elif msg_type in ["ItemReceive"]:
                # Skip item receive messages from players
                print(f"Skipping ItemReceive message: {data}")
                return False

            elif msg_type in ["Goal", "Release", "Collect", "Countdown"]:
                await process_game_event_message(msg_type, data, channel)

            elif msg_type in ["Tutorial", "ServerChat"]:
                # Check if this is a game completion message
                is_complete = await process_server_message(msg_type, data, channel)
                if is_complete:
                    print("Game completion detected, signaling to stop tracking")
                    return True  # Signal that tracking should stop

            else:
                await process_filtered_message(data, channel)

        elif cmd == "RoomUpdate":
            await process_room_update_message(msg, channel)

        elif cmd == "RoomInfo":
            await process_room_info_message(msg, channel)

        elif cmd == "DataPackage":
            await process_data_package_message(msg, channel, self.game_data)

        # Handle any other message types by showing the command type
        else:
            await process_unknown_message(cmd, msg, channel)

        return False  # No completion detected

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

        # Track the connection (stored in bot instance to persist across cog reloads)
        self.active_connections[server_url] = {
            "task": task,
            "channel_id": channel_id_int,
            "password": password,  # Store password for automatic reconnection
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
    /ap newgame - Generates the game files from uploaded player files and then starts the server. Optionally
                takes in a pre-generated file to start up.
                
    Parameters: [Optional] apfile: Generated .zip file from Archipelago to start with the server
    
    """

    @app_commands.command(
        name="newgame",
        description="Starts the game. Either generates or takes an optional "
        "pre-generated game file.",
    )
    @app_commands.describe(apfile="Pre-generated zip file for an Archipelago game")
    async def ap_newgame(
        self, interaction: discord.Interaction, apfile: Optional[discord.Attachment]
    ) -> None:
        await interaction.response.send_message(
            "Attempting to start Archipelago server, hold please...\nError messages will be sent to this channel"
        )

        def outputfiles():
            return listdir(self.output_directory)

        for file in outputfiles():
            remove(f"{self.output_directory}/{file}")
            
        # Delete any existing datapackage to ensure clean start
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
            
            # Start the generation process in an interactive window so user can watch progress
            start_time = time.time()
            
            # Run the generation in a new interactive command window
            process = subprocess.Popen([
                "cmd", "/c", "start", "cmd", "/k", 
                "python", "./Archipelago/Generate.py"
            ], shell=True)
            
            print(f"Started generation process with PID: {process.pid}")
            
            # Wait for generation to complete by monitoring for output files
            # Since the process launches in a separate window, we can't track it directly
            generation_timeout = 1200  # 20 minutes timeout
            check_interval = 10  # Check every 10 seconds
            elapsed_time = 0
            
            await interaction.edit_original_response(
                content="🔄 Generation running in interactive window... Monitoring for completion..."
            )
            
            while elapsed_time < generation_timeout:
                await sleep(check_interval)
                elapsed_time += check_interval
                
                # Check if generation has produced output files
                try:
                    output_files = listdir(self.output_directory)
                    zip_files = [f for f in output_files if f.endswith('.zip')]
                    
                    if zip_files:
                        # Generation completed successfully
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
                        content="❌ Generation timed out after 5 minutes. Check the generation window for details."
                    )
                else:
                    await interaction.edit_original_response(
                        content="❌ Generation failed - no output file was created. Check the generation window for details."
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
            server_password = get_server_password()
            server_message = f"Archipelago server started.\nServer: ap.rhelys.com\nPort: 38281\nPassword: {server_password}"
            await interaction.edit_original_response(content=server_message)
            
            # After server is started, fetch and save the datapackage
            try:
                
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
            status_parts.append("📡 **HTTPS Connection**: ap.rhelys.com:38288")
        else:
            status_parts.append("🔴 **Server Status**: Not running")
        
        # Player status
        if current_players:
            playerlist = list(current_players.keys())
            status_parts.append(f"👥 **Current Players**: {', '.join(playerlist)}")
        else:
            status_parts.append("👥 **Current Players**: None")

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
        description="Stops the currently running Archipelago server and untracks all connections",
    )
    async def ap_stop(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            # First, untrack all active connections
            untracked_servers = []
            if self.active_connections:
                for server_url in list(self.active_connections.keys()):
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
                    untracked_servers.append(server_url)

            untrack_message = f"\nUntracked servers: {', '.join(untracked_servers)}" if untracked_servers else ""

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
                    delete_success = delete_local_datapackage()
                    datapackage_message = "\nDatapackage cleaned up successfully." if delete_success else ""
                    logger.info(f"Deleted datapackage on server stop: {delete_success}")
                except Exception as dp_error:
                    datapackage_message = f"\nWarning: Failed to clean up datapackage: {str(dp_error)}"
                    logger.error(f"Error deleting datapackage on server stop: {dp_error}")

                await interaction.followup.send(
                    f"✅ Successfully stopped Archipelago server.\n"
                    f"Killed processes: {', '.join(map(str, killed_processes))}{datapackage_message}{untrack_message}"
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
                server_password = get_server_password()
                restart_message = "✅ Archipelago server restarted successfully!\n" \
                                 f"Server: ap.rhelys.com\nPort: 38281\nPassword: {server_password}"
                
                # After server is restarted, fetch and save a fresh datapackage
                try:
                            
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
        if not self.is_server_running():
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
                target_players = resolved_player
                await interaction.followup.send(f"ℹ️ Showing progress for all your players: {', '.join(resolved_player)}")
            else:
                target_players = [resolved_player]

        # Check if we have active connection data
        has_active_connection = bool(self.connection_data and self.game_data and self.player_progress)

        # Load save data
        save_data = load_apsave_data(self.output_directory, self.ap_directory)
        if not save_data:
            await interaction.followup.send("❌ Could not load save data. Make sure the Archipelago server has a save file.")
            return

        # Validate save file timestamp if no active connection
        if not validate_save_file_timestamp(self.output_directory, self.connection_data, self.game_data, self.player_progress):
            await interaction.edit_original_response(
                content="⚠️ **Warning**: Save file data may be from a previous game session.\n\n"
            )

        # Load and validate game data
        all_players, game_data = await load_and_validate_game_data(
            interaction, self.connection_data, self.game_data, save_data,
            self.fetch_server_data, self.extract_player_data_from_save
        )

        if not all_players:
            await interaction.followup.send("❌ No players found in the current game.")
            return

        # Set up progress tracking
        show_specific_players = (target_players is not None)
        location_checks = save_data.get("location_checks", {})

        # Check for save file mismatch and merge real-time data
        await check_save_file_mismatch(interaction, has_active_connection, all_players, location_checks)
        location_checks = merge_real_time_tracking_data(location_checks, self.player_progress)

        # Parse activity timers
        activity_timer_dict = parse_activity_timers(save_data.get("client_activity_timers", ()))

        # Generate player progress data
        player_progress_data = get_player_progress_data(
            all_players, location_checks, activity_timer_dict, target_players,
            show_specific_players, lambda pid: get_player_total_locations(pid, save_data),
            self.create_progress_bar
        )

        # Handle case where specific players not found
        if show_specific_players and not player_progress_data:
            error_message = format_progress_error_message(original_player, target_players, all_players)
            await interaction.followup.send(error_message)
            return

        # Sort and format progress data
        player_progress_data.sort(key=lambda x: x[0])
        progress_lines = []

        # Add header
        if has_active_connection:
            progress_lines.append("📊 **Player Progress Report** (Live Tracking)\n")
        else:
            progress_lines.append("📊 **Player Progress Report** (Save File Data)\n")

        # Add player progress lines
        for _, player_line in player_progress_data:
            progress_lines.append(player_line)

        # Calculate and add total progress if not showing specific players
        if not target_players:
            total_checked, total_locations, overall_percentage = calculate_total_game_progress(
                all_players, location_checks, lambda pid: get_player_total_locations(pid, save_data)
            )

            if total_locations > 0:
                total_progress_bar = self.create_progress_bar(overall_percentage)
                progress_lines.extend([
                    "─" * 40,
                    "\n📈 **Total Game Progress**",
                    f"\n└ {total_checked}/{total_locations} locations ({overall_percentage:.1f}%)",
                    f"└ {total_progress_bar}"
                ])

        # Send progress report in chunks if needed
        progress_message = "\n".join(progress_lines)
        if len(progress_message) > 2000:
            chunks = create_progress_sections(progress_lines[1:])  # Skip header
            for i, chunk in enumerate(chunks):
                content = "📊 **Player Progress Report**\n\n" + chunk if i == 0 else chunk
                if i == 0:
                    await interaction.followup.send(content)
                else:
                    await interaction.channel.send(content)
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
        return create_connection_message(password)

    async def _connect_to_server(self, server_url: str, timeout: float = 15.0):
        """
        Create a websocket connection to the Archipelago server
        (Delegating to helpers.server_helpers.connect_to_server)
        """
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
        return await fetch_server_data(server_url, password, save_datapackage)
    
    
    
    
    def create_progress_bar(self, percentage: float, length: int = 20) -> str:
        """
        Create a visual progress bar
        (Delegating to helpers.formatting_helpers.create_progress_bar)
        """
        return create_progress_bar(percentage, length)
    
    def get_player_hint_points(self, player_id: int, save_data: dict) -> int:
        """
        Get the current hint points for a specific player
        (Delegating to helpers.progress_helpers.get_player_hint_points)
        """
        return get_player_hint_points(player_id, save_data, get_player_total_locations)
    
    def get_hint_cost(self, player_id: int, save_data: dict) -> int:
        """
        Get the cost of the next hint for a specific player
        (Delegating to helpers.progress_helpers.get_hint_cost)
        """
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
        player="Optional: Show hints only for a specific player and their hint points/cost",
        exclude_found="Optional: Exclude hints for items that have already been found (default: True)"
    )
    async def ap_hints(self, interaction: discord.Interaction, player: Optional[str] = None, exclude_found: bool = True):
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
                # Apply exclude_found filter if requested
                if exclude_found and hasattr(hint, 'found') and hint.found:
                    continue  # Skip found hints when exclude_found is True
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
                    # Apply exclude_found filter if requested
                    if exclude_found and simple_hint.found:
                        continue  # Skip found hints when exclude_found is True
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
                # Apply exclude_found filter if requested
                if exclude_found and simple_hint.found:
                    continue  # Skip found hints when exclude_found is True
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
