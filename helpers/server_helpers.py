"""
Server management helper functions for Archipelago Discord bot.
"""

import asyncio
import websockets
import json
import logging
import subprocess
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def get_server_password(password_file: str = "server_password.txt") -> str:
    """Read the server password from server_password.txt file."""
    try:
        with open(password_file, "r", encoding="utf-8") as f:
            password = f.read().strip()
            if not password:
                raise ValueError(f"{password_file} file is empty")
            return password
    except FileNotFoundError:
        raise FileNotFoundError(f"{password_file} file not found")
    except Exception as e:
        raise Exception(f"Error reading {password_file}: {e}")

def is_server_running(server_process=None) -> bool:
    """Check if the Archipelago server is currently running."""
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
    
    try:
        import psutil
        
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
                
    except ImportError:
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
    import uuid
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

async def fetch_server_data(server_url: str = "ws://ap.rhelys.com:38281", password: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Connect to server temporarily to fetch player and game data."""
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
                return {
                    "players": all_players,
                    "game_data": game_data
                }
            else:
                logger.warning("Failed to get connection data from server")
                return None
                
        finally:
            await websocket.close()
            
    except Exception as e:
        logger.error(f"Error fetching server data: {e}")
        return None
