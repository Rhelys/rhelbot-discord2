"""
Server management helper functions for Archipelago Discord bot.
"""

import asyncio
import websockets
import json
import logging
import subprocess
import uuid
from typing import Dict, Any, Optional, Tuple
from ruyaml import YAML

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)

def get_server_password(host_file: str = "./Archipelago/host.yaml") -> str:
    """Read the server password from Archipelago host.yaml configuration file."""
    try:
        yaml = YAML()

        with open(host_file, "r", encoding="utf-8") as f:
            config = yaml.load(f)

        password = config.get("server_options", {}).get("password")

        if password is None or password == "":
            raise ValueError(f"No password set in {host_file} (server_options.password is null or empty)")

        return password
    except FileNotFoundError:
        raise FileNotFoundError(f"{host_file} file not found")
    except Exception as e:
        raise Exception(f"Error reading {host_file}: {e}")

def is_server_running(server_process=None) -> bool:
    """Check if the Archipelago server is currently running."""
    if PSUTIL_AVAILABLE:
        try:
            # Check for MultiServer.py processes
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if (proc.info['name'] and 'python' in proc.info['name'].lower() and
                        proc.info['cmdline'] and any('MultiServer.py' in arg for arg in proc.info['cmdline'])):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        except Exception:
            pass

    # Fallback: check if tracked process is still running
    if server_process:
        try:
            # Check if process is still running
            return server_process.poll() is None
        except:
            pass

    return False

def kill_server_processes(server_process=None) -> list:
    """Kill running Archipelago server processes."""
    killed_processes = []

    if PSUTIL_AVAILABLE:
        try:
            # Find and kill the MultiServer.py process
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    # Check if this is a Python process running MultiServer.py
                    if (proc.info['name'] and 'python' in proc.info['name'].lower() and
                        proc.info['cmdline'] and any('MultiServer.py' in arg for arg in proc.info['cmdline'])):

                        logger.info(f"Found MultiServer process: PID {proc.info['pid']}")
                        proc.kill()
                        killed_processes.append(proc.info['pid'])

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            # Also try to terminate the tracked server process if it exists
            if server_process:
                try:
                    # Kill the batch file process and its children
                    parent = psutil.Process(server_process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                    killed_processes.append(server_process.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        except Exception as e:
            logger.error(f"Error stopping server with psutil: {e}")
    else:
        # Fallback method using taskkill on Windows
        try:
            subprocess.run([
                "taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq *MultiServer*"
            ], capture_output=True, text=True)
        except Exception as e:
            logger.error(f"Error stopping server: {e}")

    return killed_processes

def create_connection_message(password: Optional[str] = None, name: str = "Rhelbot", game: str = "") -> Dict[str, Any]:
    """Create a standard Archipelago connection message."""
    return {
        "cmd": "Connect",
        "game": game,
        "password": password,
        "name": name,
        "version": {"major": 0, "minor": 6, "build": 0, "class": "Version"},
        "tags": ["Tracker"],
        "items_handling": 0b000,  # No items handling for tracker
        "uuid": uuid.getnode()
    }

async def connect_to_server(server_url: str, timeout: float = 15.0):
    """Create a websocket connection to the Archipelago server."""
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

async def fetch_server_data(server_url: str = "ws://ap.rhelys.com:38281", password: Optional[str] = None, 
                           save_datapackage: bool = False, file_path: str = "datapackage.json") -> Optional[Dict[str, Any]]:
    """
    Connect to server temporarily to fetch player and game data.
    
    Args:
        server_url: Archipelago server URL
        password: Server password (optional)
        save_datapackage: Whether to save the datapackage locally (default: False)
        file_path: Path to save the datapackage (default: datapackage.json)
    
    Returns:
        Optional[Dict[str, Any]]: Dictionary containing players and game_data, or None on failure
    """
    try:
        logger.debug(f"Attempting to fetch server data from {server_url}")
        
        # If no password provided, read from file
        if password is None:
            try:
                password = get_server_password()
            except Exception as e:
                logger.error(f"Error reading server password: {e}")
                return None
        
        # Connect to the Archipelago websocket server
        websocket = await connect_to_server(server_url)
        
        try:
            # Send connection message
            connect_msg = create_connection_message(password)
            await websocket.send(json.dumps([connect_msg]))
            logger.debug("Sent connection message for data fetch")
            
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
                            logger.debug("Connected to server for data fetch")
                            connection_data = msg
                            
                            # Request DataPackage for games in use
                            slot_info = msg.get("slot_info", {})
                            games_in_use = list(set(player_info.get("game", "") for player_info in slot_info.values()))
                            games_in_use = [game for game in games_in_use if game]
                            
                            if games_in_use:
                                get_data_msg = {"cmd": "GetDataPackage", "games": games_in_use}
                                logger.debug(f"Requesting DataPackage for games: {games_in_use}")
                            else:
                                get_data_msg = {"cmd": "GetDataPackage"}
                                logger.debug("Requesting full DataPackage")
                            
                            await websocket.send(json.dumps([get_data_msg]))
                            
                        elif cmd == "ConnectionRefused":
                            logger.warning(f"Connection refused: {msg.get('errors', [])}")
                            return None
                            
                        elif cmd == "DataPackage":
                            logger.debug("Received DataPackage")
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
                
                logger.info(f"Successfully fetched data for {len(all_players)} players and {len(game_data)} games")
                
                result = {
                    "players": all_players,
                    "game_data": game_data
                }
                
                # Optionally save the datapackage locally
                if save_datapackage:
                    try:
                        from helpers.data_helpers import save_datapackage_locally
                        
                        # Create a compatible connection_data structure
                        connection_data = {
                            server_url: {
                                "slot_info": {str(player_id): player_info for player_id, player_info in all_players.items()}
                            }
                        }
                        
                        save_datapackage_locally(game_data, connection_data)
                        logger.info("Saved datapackage locally after fetch_server_data")
                    except Exception as save_error:
                        logger.error(f"Error saving datapackage: {save_error}")
                
                return result
            else:
                logger.warning("Failed to get connection data from server")
                return None
                
        finally:
            await websocket.close()
            
    except Exception as e:
        logger.error(f"Error fetching server data: {e}")
        return None

async def connect_and_save_datapackage(server_url: str = "ws://ap.rhelys.com:38281", 
                                      password: Optional[str] = None,
                                      file_path: str = "datapackage.json") -> Tuple[bool, str]:
    """
    Connect to the Archipelago server and save the datapackage locally.
    
    This function is specifically designed to handle the datapackage fetching and saving
    as a standalone operation, with appropriate error handling and status reporting.
    
    Args:
        server_url: Archipelago server URL
        password: Server password (optional)
        file_path: Path to save the datapackage (default: datapackage.json)
        
    Returns:
        Tuple[bool, str]: (success, message) - success flag and status message
    """
    try:
        # Try to connect and fetch data
        server_data = await fetch_server_data(server_url, password)
        
        if not server_data:
            return False, "Failed to connect to server or retrieve data"
            
        # Extract game_data and players
        game_data = server_data.get("game_data", {})
        players = server_data.get("players", {})
        
        if not game_data:
            return False, "Connected to server but received no game data"
            
        # Create connection_data structure
        connection_data = {
            server_url: {
                "slot_info": {str(player_id): player_info for player_id, player_info in players.items()}
            }
        }
        
        # Import here to avoid circular imports
        from helpers.data_helpers import save_datapackage_locally, delete_local_datapackage
        
        # Delete any existing datapackage first
        delete_local_datapackage(file_path)
        
        # Save the new datapackage
        if save_datapackage_locally(game_data, connection_data, file_path):
            games_count = len(game_data)
            players_count = len(players)
            return True, f"Successfully saved datapackage with {games_count} games and {players_count} players"
        else:
            return False, "Failed to save datapackage locally"
            
    except Exception as e:
        logger.error(f"Error in connect_and_save_datapackage: {e}")
        return False, f"Error: {str(e)}"
