import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import websockets
from websockets.protocol import State
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import uuid
from ruyaml import YAML

# Import helper functions from the ap.py cog
from helpers.server_helpers import get_server_password, is_server_running, connect_to_server, get_server_port
from helpers.lookup_helpers import lookup_item_name, lookup_player_name

donkeyServer = discord.Object(id=591625815528177690)

@app_commands.guilds(donkeyServer)
class ApAdminCog(commands.GroupCog, group_name="apadmin"):
    """Archipelago Admin Commands - Requires admin password authentication"""
    
    # Class constants
    DEFAULT_SERVER_URL = "ws://ap.rhelys.com:38281"
    AUTHORIZED_USER_ID = 187800991675056129  # Only this Discord user can use admin commands
    
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()
        
        # Admin session tracking
        self.admin_sessions: Dict[str, Dict] = {}  # server_url -> session_info
        self.game_data: Dict[str, Dict] = {}  # Shared game data
        
        logger = logging.getLogger(__name__)
    
    def is_authorized_user(self, user_id: int) -> bool:
        """Check if the user is authorized to use admin commands."""
        return user_id == self.AUTHORIZED_USER_ID
    
    def get_admin_password(self, host_file: str = "./Archipelago/host.yaml") -> str:
        """Read the admin password from Archipelago host.yaml configuration file."""
        try:
            yaml = YAML()

            with open(host_file, "r", encoding="utf-8") as f:
                config = yaml.load(f)

            password = config.get("server_options", {}).get("server_password")

            # Treat None (null in YAML) as empty string (no password)
            if password is None:
                return ""

            return str(password)
        except FileNotFoundError:
            raise FileNotFoundError(f"{host_file} file not found")
        except Exception as e:
            raise Exception(f"Error reading {host_file}: {e}")
    
    async def connect_to_server(self, server_url: str, timeout: float = 15.0):
        """Create a websocket connection to the Archipelago server"""
        try:
            websocket = await asyncio.wait_for(
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
            return websocket
        except Exception as e:
            raise Exception(f"Failed to connect to server: {e}")
    
    async def check_admin_response(self, response_text: str) -> bool:
        """
        Check if a server response indicates successful admin login.
        """
        try:
            response = json.loads(response_text)
            
            # Handle response as list (common Archipelago format)
            if isinstance(response, list):
                for msg in response:
                    if isinstance(msg, dict) and msg.get("cmd") == "Print":
                        print_data = msg.get("data", [])
                        for item in print_data:
                            if isinstance(item, dict) and "text" in item:
                                text = item["text"].lower()
                                if ("admin" in text and ("logged in" in text or "authenticated" in text or "login successful" in text)) or \
                                   ("login successful" in text and ("server" in text or "command" in text)) or \
                                   ("administrator" in text) or ("admin mode" in text) or \
                                   ("admin privileges" in text) or ("admin session" in text) or \
                                   ("welcome admin" in text) or ("admin authenticated" in text):
                                    print(f"Admin login success detected: {item['text']}")
                                    return True
                            elif isinstance(item, str) and "admin" in item.lower():
                                if "logged in" in item.lower() or "authenticated" in item.lower() or \
                                   "administrator" in item.lower() or "admin mode" in item.lower():
                                    print(f"Admin login success detected: {item}")
                                    return True
                    elif isinstance(msg, dict) and msg.get("cmd") == "PrintJSON":
                        # Handle PrintJSON format
                        print_data = msg.get("data", [])
                        for item in print_data:
                            if isinstance(item, dict) and "text" in item:
                                text = item["text"].lower()
                                if ("admin" in text and ("logged in" in text or "authenticated" in text or "login successful" in text)) or \
                                   ("login successful" in text and ("server" in text or "command" in text)) or \
                                   ("administrator" in text) or ("admin mode" in text) or \
                                   ("admin privileges" in text) or ("welcome admin" in text):
                                    print(f"Admin login success detected: {item['text']}")
                                    return True
                            elif isinstance(item, str) and "admin" in item.lower():
                                if "logged in" in item.lower() or "authenticated" in item.lower() or \
                                   "administrator" in item.lower() or "admin mode" in item.lower():
                                    print(f"Admin login success detected: {item}")
                                    return True
            
            # Handle response as single object
            elif isinstance(response, dict):
                if response.get("cmd") == "Print":
                    print_data = response.get("data", [])
                    for item in print_data:
                        if isinstance(item, dict) and "text" in item:
                            text = item["text"].lower()
                            if ("admin" in text and ("logged in" in text or "authenticated" in text)) or \
                               ("login successful" in text and ("server" in text or "command" in text)) or \
                               ("administrator" in text) or ("admin mode" in text):
                                print(f"Admin login success detected: {item['text']}")
                                return True
                        elif isinstance(item, str) and "admin" in item.lower():
                            if "logged in" in item.lower() or "authenticated" in item.lower():
                                print(f"Admin login success detected: {item}")
                                return True
            
            return False
            
        except json.JSONDecodeError:
            # Check if it's a plain text success message
            text_lower = response_text.lower()
            if ("admin" in text_lower and ("logged in" in text_lower or "authenticated" in text_lower)) or \
               ("login successful" in text_lower and ("server" in text_lower or "command" in text_lower)):
                print(f"Admin login success detected in plain text: {response_text}")
                return True
            return False
    
    async def admin_login(self, websocket, admin_password: str) -> bool:
        """
        Perform admin login to the Archipelago server.
        Returns True if login successful, False otherwise.
        """
        try:
            # Send admin login command as JSON message (in array format like ap.py)
            login_message = {
                "cmd": "Say",
                "text": f"!admin login {admin_password}"
            }
            print(f"Sending admin login command: {json.dumps([login_message])}")
            await websocket.send(json.dumps([login_message]))
            
            # Wait for multiple responses as admin login might come after join message
            print("Waiting for admin login response...")
            admin_confirmed = False
            attempts = 0
            max_attempts = 5
            
            while attempts < max_attempts and not admin_confirmed:
                try:
                    response_text = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    print(f"Admin login response {attempts + 1}: {response_text}")
                    
                    # Check this response for admin confirmation
                    if await self.check_admin_response(response_text):
                        admin_confirmed = True
                        break
                        
                    attempts += 1
                    
                except asyncio.TimeoutError:
                    attempts += 1
                    print(f"No response on attempt {attempts}")
                    continue
            
            # If no explicit admin confirmation, try a test admin command
            if not admin_confirmed:
                print("No explicit admin confirmation, testing with basic admin command...")
                try:
                    test_message = {"cmd": "Say", "text": "!admin"}
                    await websocket.send(json.dumps([test_message]))
                    
                    test_response = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                    print(f"Admin test response: {test_response}")
                    
                    # If we get any admin-related response, consider it successful
                    if await self.check_admin_response(test_response):
                        admin_confirmed = True
                        print("Admin login confirmed via test command")
                    else:
                        # Check for admin command help or any admin-related text
                        if "admin" in test_response.lower() and ("command" in test_response.lower() or "help" in test_response.lower()):
                            admin_confirmed = True
                            print("Admin login confirmed - received admin help response")
                        
                except asyncio.TimeoutError:
                    print("No response to admin test command")
            
            return admin_confirmed
                
        except asyncio.TimeoutError:
            print("Admin login timeout")
            return False
        except Exception as e:
            print(f"Admin login error: {e}")
            return False
    
    async def get_admin_session(self, server_url: str = None) -> Optional[Dict]:
        """
        Get or create an admin session for the specified server.
        Returns session info with websocket connection if successful.
        """
        if server_url is None:
            server_url = self.DEFAULT_SERVER_URL
        
        # Check if we have an active admin session
        session = self.admin_sessions.get(server_url)
        if session and session.get('websocket'):
            # Check if websocket is still open
            ws = session['websocket']
            try:
                # For websockets library, check if connection is open
                if hasattr(ws, 'closed'):
                    is_closed = ws.closed
                elif hasattr(ws, 'state'):
                    is_closed = ws.state != State.OPEN
                else:
                    # Fallback: assume closed if we can't check
                    is_closed = True

                if not is_closed:
                    print(f"Reusing existing admin session from {session.get('logged_in_at')}")
                    return session
            except:
                pass  # Session is invalid, will be cleaned up below
        
        # Clean up any invalid sessions
        if session:
            print("Cleaning up invalid admin session")
            if session.get('websocket'):
                try:
                    await session['websocket'].close()
                except:
                    pass
            del self.admin_sessions[server_url]
        
        try:
            # Get both passwords
            admin_password = self.get_admin_password()
            server_password = get_server_password()  # Use server password for initial connection
            
            # Connect to server
            websocket = await self.connect_to_server(server_url)
            
            # First establish a basic connection with server password (matching ap.py format exactly)
            connect_msg = {
                "cmd": "Connect",
                "game": "",
                "password": server_password,  # Use server password for initial connection
                "name": "Rhelbot",
                "version": {"major": 0, "minor": 6, "build": 0, "class": "Version"},
                "tags": ["Tracker"],
                "items_handling": 0b000,  # No items handling for tracker
                "uuid": uuid.getnode()
            }
            await websocket.send(json.dumps([connect_msg]))
            print("Sent connection message")
            
            # Wait for connection confirmation with message loop (like ap.py)
            connection_confirmed = False
            timeout_counter = 0
            max_timeout = 15  # 15 seconds total timeout (reduced from 30)
            
            while timeout_counter < max_timeout and not connection_confirmed:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    
                    try:
                        data = json.loads(message)
                        print(f"Admin connection received message: {data}")
                        
                        # Handle list of messages
                        if isinstance(data, list):
                            for msg in data:
                                if isinstance(msg, dict):
                                    msg_cmd = msg.get("cmd", "")
                                    
                                    if msg_cmd == "Connected":
                                        connection_confirmed = True
                                        print("Admin connection confirmed via Connected message")
                                        break
                                    elif msg_cmd == "ConnectionRefused":
                                        errors = msg.get("errors", ["Unknown error"])
                                        print(f"Admin connection refused: {', '.join(errors)}")
                                        await websocket.close()
                                        return None
                                    elif msg_cmd == "PrintJSON":
                                        # Sometimes the connection is working but we get other messages first
                                        msg_type = msg.get("type", "")
                                        if msg_type == "Join":
                                            print("Detected Join message - connection appears successful")
                                            connection_confirmed = True
                                            break
                        
                        # Handle single message
                        elif isinstance(data, dict):
                            msg_cmd = data.get("cmd", "")
                            if msg_cmd == "Connected":
                                connection_confirmed = True
                                print("Admin connection confirmed via Connected message")
                            elif msg_cmd == "ConnectionRefused":
                                errors = data.get("errors", ["Unknown error"])
                                print(f"Admin connection refused: {', '.join(errors)}")
                                await websocket.close()
                                return None
                            elif msg_cmd == "PrintJSON":
                                msg_type = data.get("type", "")
                                if msg_type == "Join":
                                    print("Detected Join message - connection appears successful")
                                    connection_confirmed = True
                        
                        if connection_confirmed:
                            break
                            
                    except json.JSONDecodeError:
                        print(f"Non-JSON message received: {message}")
                        # Continue listening for proper messages
                        
                except asyncio.TimeoutError:
                    timeout_counter += 1
                    print(f"Waiting for connection confirmation... ({timeout_counter}s)")
                    continue
            
            if not connection_confirmed:
                print("Connection confirmation timeout - attempting admin login anyway")
                # Don't close the websocket, try to proceed - it might still work
            
            # Wait a moment for connection to stabilize
            await asyncio.sleep(1.0)
            
            # Now perform admin login with admin password
            print(f"Attempting admin login with password: {admin_password[:3]}...")
            login_success = await self.admin_login(websocket, admin_password)
            
            if login_success:
                # Store admin session
                session_info = {
                    'websocket': websocket,
                    'server_url': server_url,
                    'logged_in_at': datetime.now(),
                    'last_used': datetime.now()
                }
                self.admin_sessions[server_url] = session_info
                return session_info
            else:
                await websocket.close()
                return None
                
        except Exception as e:
            print(f"Failed to create admin session: {e}")
            return None
    
    async def send_admin_command(self, command: str, server_url: str = None) -> Optional[str]:
        """
        Send an admin command to the server and return the response.
        """
        print(f"send_admin_command called with: {command}")
        print(f"Current admin sessions: {list(self.admin_sessions.keys())}")
        session = await self.get_admin_session(server_url)
        if not session:
            print("No admin session available")
            return None
        print(f"Got admin session: {session.keys()}")
        
        try:
            websocket = session['websocket']
            print(f"Using websocket: {websocket}")

            # Check if websocket is still connected
            try:
                if hasattr(websocket, 'closed'):
                    is_closed = websocket.closed
                elif hasattr(websocket, 'state'):
                    is_closed = websocket.state != State.OPEN
                else:
                    # If we can't determine state, try to proceed
                    is_closed = False

                if is_closed:
                    print("Websocket is closed, admin session expired")
                    if server_url in self.admin_sessions:
                        del self.admin_sessions[server_url]
                    return None
            except Exception as e:
                print(f"Error checking websocket state: {e}")
                # If we can't check, assume it's still open and let the send fail if needed
            
            # Send the command as JSON message (in array format like ap.py)
            command_message = {
                "cmd": "Say",
                "text": command
            }
            print(f"Sending command message: {json.dumps([command_message])}")
            await websocket.send(json.dumps([command_message]))
            
            # Wait for response - need to handle command echo vs actual result
            print("Waiting for command response...")
            response_text = await asyncio.wait_for(websocket.recv(), timeout=15.0)
            print(f"Raw command response 1: {response_text}")
            
            # Check if this is just the command echo - if so, wait for the actual result
            is_command_echo = False
            try:
                echo_data = json.loads(response_text)
                if isinstance(echo_data, list):
                    for msg in echo_data:
                        if isinstance(msg, dict) and msg.get("cmd") == "PrintJSON":
                            data_items = msg.get("data", [])
                            for item in data_items:
                                if isinstance(item, dict) and "text" in item:
                                    text = item["text"]
                                    # Check if this is our command being echoed back
                                    if text.startswith("Rhelbot:") and command in text:
                                        is_command_echo = True
                                        print("Detected command echo, waiting for actual result...")
                                        break
            except json.JSONDecodeError:
                pass
            
            # If it was a command echo, wait for the next message (the actual result)
            if is_command_echo:
                try:
                    response_text = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    print(f"Raw command response 2 (actual result): {response_text}")
                except asyncio.TimeoutError:
                    print("Timeout waiting for actual command result")
                    # Fall back to using the echo if no result comes
            
            # Update last used timestamp
            session['last_used'] = datetime.now()
            
            # Try to parse JSON response
            try:
                response = json.loads(response_text)
                text_parts = []
                
                # Handle response as list (common Archipelago format)
                if isinstance(response, list):
                    for msg in response:
                        if isinstance(msg, dict):
                            # Extract meaningful text from Print commands
                            if msg.get("cmd") == "Print":
                                print_data = msg.get("data", [])
                                for item in print_data:
                                    if isinstance(item, dict) and "text" in item:
                                        text_parts.append(item["text"])
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                            elif msg.get("cmd") == "PrintJSON":
                                # Handle PrintJSON format
                                print_data = msg.get("data", [])
                                for item in print_data:
                                    if isinstance(item, dict) and "text" in item:
                                        text_parts.append(item["text"])
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                
                # Handle response as single object
                elif isinstance(response, dict):
                    if response.get("cmd") == "Print":
                        print_data = response.get("data", [])
                        for item in print_data:
                            if isinstance(item, dict) and "text" in item:
                                text_parts.append(item["text"])
                            elif isinstance(item, str):
                                text_parts.append(item)
                
                if text_parts:
                    result = " ".join(text_parts)
                    print(f"Extracted text from response: {result}")
                    return result
                
                # For other response types, return the JSON as formatted string
                formatted_json = json.dumps(response, indent=2)
                print(f"Returning formatted JSON: {formatted_json}")
                return formatted_json
                
            except json.JSONDecodeError:
                # If not JSON, return as-is
                return response_text
            
        except Exception as e:
            print(f"Failed to send admin command: {e}")
            # Remove failed session
            if server_url in self.admin_sessions:
                del self.admin_sessions[server_url]
            return None
    
    @app_commands.command(
        name="release",
        description="Send out the remaining items from a player to their intended recipients"
    )
    @app_commands.describe(
        player_name="The player whose remaining items should be released",
        game_number="Game slot to execute command on (1-3, default: 1)"
    )
    async def admin_release(self, interaction: discord.Interaction, player_name: str, game_number: int = 1):
        # Validate game_number
        if not 1 <= game_number <= 3:
            await interaction.response.send_message(
                f"❌ Invalid game number. Please choose between 1 and 3. You provided: {game_number}"
            )
            return

        await interaction.response.defer()

        # Check if user is authorized
        if not self.is_authorized_user(interaction.user.id):
            await interaction.followup.send("❌ You are not authorized to use admin commands.")
            return

        # Check if server is running
        if not is_server_running():
            await interaction.followup.send("❌ Archipelago server is not running.")
            return

        try:
            # Get server URL for the specific game
            server_port = get_server_port(game_number=game_number)
            server_url = f"ws://ap.rhelys.com:{server_port}"

            # Send the release command
            command = f"!admin /release {player_name}"
            print(f"Executing admin release command on Game {game_number}: {command}")
            response = await self.send_admin_command(command, server_url=server_url)
            print(f"Admin release response: {response}")
            
            if response is None:
                await interaction.followup.send("❌ Failed to connect to server or authenticate as admin.")
                return
            
            # Format the response
            if "error" in response.lower() or "failed" in response.lower():
                await interaction.followup.send(f"❌ Release command failed: {response}")
            else:
                await interaction.followup.send(f"✅ Released remaining items for **{player_name}**\n```{response}```")
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error executing release command: {str(e)}")
    
    @app_commands.command(
        name="send",
        description="Send a specific item to the specified player"
    )
    @app_commands.describe(
        player_name="The player who should receive the item",
        item_name="The name of the item to send",
        game_number="Game slot to execute command on (1-3, default: 1)"
    )
    async def admin_send(self, interaction: discord.Interaction, player_name: str, item_name: str, game_number: int = 1):
        # Validate game_number
        if not 1 <= game_number <= 3:
            await interaction.response.send_message(
                f"❌ Invalid game number. Please choose between 1 and 3. You provided: {game_number}"
            )
            return

        await interaction.response.defer()

        # Check if user is authorized
        if not self.is_authorized_user(interaction.user.id):
            await interaction.followup.send("❌ You are not authorized to use admin commands.")
            return

        # Check if server is running
        if not is_server_running():
            await interaction.followup.send("❌ Archipelago server is not running.")
            return

        try:
            # Get server URL for the specific game
            server_port = get_server_port(game_number=game_number)
            server_url = f"ws://ap.rhelys.com:{server_port}"

            # Send the item command
            command = f"!admin /send {player_name} {item_name}"
            print(f"Executing admin send command on Game {game_number}: {command}")
            response = await self.send_admin_command(command, server_url=server_url)
            print(f"Admin send response: {response}")
            
            if response is None:
                await interaction.followup.send("❌ Failed to connect to server or authenticate as admin.")
                return
            
            # Format the response
            if "error" in response.lower() or "failed" in response.lower():
                await interaction.followup.send(f"❌ Send command failed: {response}")
            else:
                await interaction.followup.send(f"✅ Sent **{item_name}** to **{player_name}**\n```{response}```")
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error executing send command: {str(e)}")
    
    @app_commands.command(
        name="send_multiple",
        description="Send multiples of an item to the specified player"
    )
    @app_commands.describe(
        amount="The number of items to send",
        player_name="The player who should receive the items",
        item_name="The name of the item to send"
    )
    async def admin_send_multiple(self, interaction: discord.Interaction, amount: int, player_name: str, item_name: str):
        await interaction.response.defer()

        # Check if user is authorized
        if not self.is_authorized_user(interaction.user.id):
            await interaction.followup.send("❌ You are not authorized to use admin commands.")
            return

        # Check if server is running
        if not is_server_running():
            await interaction.followup.send("❌ Archipelago server is not running.")
            return

        try:
            # Send the send_multiple command
            command = f"!admin /send_multiple {amount} {player_name} {item_name}"
            print(f"Executing admin send_multiple command: {command}")
            response = await self.send_admin_command(command)
            print(f"Admin send_multiple response: {response}")

            if response is None:
                await interaction.followup.send("❌ Failed to connect to server or authenticate as admin.")
                return

            # Format the response
            if "error" in response.lower() or "failed" in response.lower():
                await interaction.followup.send(f"❌ Send multiple command failed: {response}")
            else:
                await interaction.followup.send(f"✅ Sent {amount}x **{item_name}** to **{player_name}**\n```{response}```")

        except Exception as e:
            await interaction.followup.send(f"❌ Error executing send_multiple command: {str(e)}")

    @app_commands.command(
        name="hint",
        description="Send out a hint for a player's item to their team"
    )
    @app_commands.describe(
        player_name="The player whose item should be hinted",
        item_name="The name of the item to hint"
    )
    async def admin_hint(self, interaction: discord.Interaction, player_name: str, item_name: str):
        await interaction.response.defer()

        # Check if user is authorized
        if not self.is_authorized_user(interaction.user.id):
            await interaction.followup.send("❌ You are not authorized to use admin commands.")
            return

        # Check if server is running
        if not is_server_running():
            await interaction.followup.send("❌ Archipelago server is not running.")
            return

        try:
            # Send the hint command
            command = f"!admin /hint {player_name} {item_name}"
            print(f"Executing admin hint command: {command}")
            response = await self.send_admin_command(command)
            print(f"Admin hint response: {response}")

            if response is None:
                await interaction.followup.send("❌ Failed to connect to server or authenticate as admin.")
                return

            # Format the response
            if "error" in response.lower() or "failed" in response.lower():
                await interaction.followup.send(f"❌ Hint command failed: {response}")
            else:
                await interaction.followup.send(f"✅ Sent hint for **{item_name}** belonging to **{player_name}**\n```{response}```")

        except Exception as e:
            await interaction.followup.send(f"❌ Error executing hint command: {str(e)}")

    @app_commands.command(
        name="hint_location",
        description="Send out a hint for a player's location to their team"
    )
    @app_commands.describe(
        player_name="The player whose location should be hinted",
        location_name="The name of the location to hint"
    )
    async def admin_hint_location(self, interaction: discord.Interaction, player_name: str, location_name: str):
        await interaction.response.defer()

        # Check if user is authorized
        if not self.is_authorized_user(interaction.user.id):
            await interaction.followup.send("❌ You are not authorized to use admin commands.")
            return

        # Check if server is running
        if not is_server_running():
            await interaction.followup.send("❌ Archipelago server is not running.")
            return

        try:
            # Send the hint_location command
            command = f"!admin /hint_location {player_name} {location_name}"
            print(f"Executing admin hint_location command: {command}")
            response = await self.send_admin_command(command)
            print(f"Admin hint_location response: {response}")

            if response is None:
                await interaction.followup.send("❌ Failed to connect to server or authenticate as admin.")
                return

            # Format the response
            if "error" in response.lower() or "failed" in response.lower():
                await interaction.followup.send(f"❌ Hint location command failed: {response}")
            else:
                await interaction.followup.send(f"✅ Sent hint for **{location_name}** belonging to **{player_name}**\n```{response}```")

        except Exception as e:
            await interaction.followup.send(f"❌ Error executing hint_location command: {str(e)}")

    @app_commands.command(
        name="send_location",
        description="Send out item from a player's location as though they checked it"
    )
    @app_commands.describe(
        player_name="The player whose location should be checked",
        location_name="The name of the location to check"
    )
    async def admin_send_location(self, interaction: discord.Interaction, player_name: str, location_name: str):
        await interaction.response.defer()

        # Check if user is authorized
        if not self.is_authorized_user(interaction.user.id):
            await interaction.followup.send("❌ You are not authorized to use admin commands.")
            return

        # Check if server is running
        if not is_server_running():
            await interaction.followup.send("❌ Archipelago server is not running.")
            return

        try:
            # Send the send_location command
            command = f"!admin /send_location {player_name} {location_name}"
            print(f"Executing admin send_location command: {command}")
            response = await self.send_admin_command(command)
            print(f"Admin send_location response: {response}")

            if response is None:
                await interaction.followup.send("❌ Failed to connect to server or authenticate as admin.")
                return

            # Format the response
            if "error" in response.lower() or "failed" in response.lower():
                await interaction.followup.send(f"❌ Send location command failed: {response}")
            else:
                await interaction.followup.send(f"✅ Checked location **{location_name}** for **{player_name}**\n```{response}```")

        except Exception as e:
            await interaction.followup.send(f"❌ Error executing send_location command: {str(e)}")

    @app_commands.command(
        name="status",
        description="Check admin session status and connection"
    )
    async def admin_status(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Check if user is authorized
        if not self.is_authorized_user(interaction.user.id):
            await interaction.followup.send("❌ You are not authorized to use admin commands.")
            return

        status_lines = []
        status_lines.append("🔧 **Admin Session Status**\n")

        if not self.admin_sessions:
            status_lines.append("❌ No active admin sessions")
        else:
            for server_url, session in self.admin_sessions.items():
                websocket = session.get('websocket')
                if websocket:
                    try:
                        if hasattr(websocket, 'closed'):
                            is_closed = websocket.closed
                        elif hasattr(websocket, 'state'):
                            is_closed = websocket.state != State.OPEN
                        else:
                            is_closed = False

                        if not is_closed:
                            logged_in_at = session.get('logged_in_at', 'Unknown')
                            last_used = session.get('last_used', 'Unknown')
                            status_lines.append(f"✅ **{server_url}**")
                            status_lines.append(f"   └ Logged in: {logged_in_at}")
                            status_lines.append(f"   └ Last used: {last_used}")
                        else:
                            status_lines.append(f"❌ **{server_url}** (Connection closed)")
                    except:
                        status_lines.append(f"❌ **{server_url}** (Connection closed)")
                else:
                    status_lines.append(f"❌ **{server_url}** (No websocket)")

        # Check server running status
        server_running = is_server_running()
        status_lines.append(f"\n🖥️ **Server Status**: {'✅ Running' if server_running else '❌ Not running'}")

        status_message = "\n".join(status_lines)
        await interaction.followup.send(status_message)
    
    @app_commands.command(
        name="disconnect",
        description="Disconnect admin session and clear authentication"
    )
    async def admin_disconnect(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Check if user is authorized
        if not self.is_authorized_user(interaction.user.id):
            await interaction.followup.send("❌ You are not authorized to use admin commands.")
            return
        
        disconnected_count = 0
        
        for server_url, session in list(self.admin_sessions.items()):
            websocket = session.get('websocket')
            if websocket:
                try:
                    # Check if websocket is open before attempting to close
                    if hasattr(websocket, 'closed'):
                        is_closed = websocket.closed
                    elif hasattr(websocket, 'state'):
                        is_closed = websocket.state != State.OPEN
                    else:
                        is_closed = False

                    if not is_closed:
                        await websocket.close()
                        disconnected_count += 1
                except:
                    pass
            del self.admin_sessions[server_url]
        
        if disconnected_count > 0:
            await interaction.followup.send(f"✅ Disconnected {disconnected_count} admin session(s)")
        else:
            await interaction.followup.send("ℹ️ No active admin sessions to disconnect")

async def setup(bot):
    print(f"Entering APAdmin cog setup\n")
    await bot.add_cog(ApAdminCog(bot))
    print("APAdmin cog setup complete\n")