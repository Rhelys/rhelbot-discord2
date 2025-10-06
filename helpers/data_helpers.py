"""
Data management helper functions for Archipelago Discord bot.
"""

import json
import pickle
import zlib
import logging
import os
import sys
import io
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List
from collections import namedtuple

logger = logging.getLogger(__name__)

def load_game_status(status_file: str = "game_status.json") -> Dict[str, Any]:
    """Load game status from JSON file."""
    if os.path.exists(status_file):
        try:
            with open(status_file, 'r') as f:
                game_status = json.load(f)
                logger.debug(f"Loaded game status from {status_file}")
                return game_status
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading game status from {status_file}: {e}")
            return {"players": {}, "discord_users": {}}
    else:
        logger.debug(f"Game status file {status_file} not found, returning empty dict")
        return {"players": {}, "discord_users": {}}

def save_game_status(game_status: Dict[str, Any], status_file: str = "game_status.json") -> bool:
    """Save game status to JSON file."""
    try:
        # Ensure required keys exist
        if "players" not in game_status:
            game_status["players"] = {}
        if "discord_users" not in game_status:
            game_status["discord_users"] = {}
        
        with open(status_file, 'w') as f:
            json.dump(game_status, f, indent=2)
            logger.debug(f"Saved game status to {status_file}")
            return True
    except Exception as e:
        logger.error(f"Error saving game status to {status_file}: {e}")
        return False

def load_apsave_data(output_directory: str = "./Archipelago/output/", ap_directory: str = "./Archipelago/") -> Optional[Dict[str, Any]]:
    """Load and parse the .apsave file to get current game state."""
    # Look for .apsave files in the output directory
    output_path = Path(output_directory)
    apsave_files = list(output_path.glob("*.apsave"))
    
    if not apsave_files:
        logger.debug("No .apsave files found in output directory")
        return None
    
    # Use the most recent .apsave file
    apsave_file = max(apsave_files, key=lambda f: f.stat().st_mtime)
    
    try:
        # Add the Archipelago directory to Python path temporarily
        archipelago_path = str(Path(ap_directory).resolve())
        if archipelago_path not in sys.path:
            sys.path.insert(0, archipelago_path)
        
        try:
            with open(apsave_file, 'rb') as f:
                compressed_data = f.read()
            
            # Decompress and unpickle the save data
            decompressed_data = zlib.decompress(compressed_data)
            save_data = pickle.loads(decompressed_data)
            
            logger.info(f"Successfully loaded save data from {apsave_file}")
            return save_data
            
        finally:
            # Remove the Archipelago path from sys.path
            if archipelago_path in sys.path:
                sys.path.remove(archipelago_path)
        
    except Exception as e:
        logger.error(f"Error loading .apsave file {apsave_file}: {e}")
        
        # Try alternative approach
        try:
            return parse_apsave_alternative(apsave_file)
        except Exception as alt_e:
            logger.error(f"Alternative parsing also failed: {alt_e}")
            return None

def parse_apsave_alternative(apsave_file: Path) -> Optional[Dict[str, Any]]:
    """Alternative method to parse .apsave file without full Archipelago dependencies."""
    # Create a custom unpickler that can handle missing modules
    class SafeUnpickler(pickle.Unpickler):
        def find_class(self, module, name):
            # Handle NetUtils classes by creating simple replacements
            if module == 'NetUtils':
                if name == 'NetworkItem':
                    class NetworkItem:
                        def __init__(self, item, location, player, flags=0):
                            self.item = item
                            self.location = location
                            self.player = player
                            self.flags = flags
                    return NetworkItem
                elif name == 'Hint':
                    class Hint(namedtuple('Hint', ['receiving_player', 'finding_player', 'location', 'item', 'found', 'entrance', 'item_flags', 'status'])):
                        def __new__(cls, receiving_player=0, finding_player=0, location=0, item=0, found=False, entrance="", item_flags=0, status=0):
                            return super().__new__(cls, receiving_player, finding_player, location, item, found, entrance, item_flags, status)
                        
                        def __repr__(self):
                            return f"Hint(receiving_player={self.receiving_player}, finding_player={self.finding_player}, location={self.location}, item={self.item}, found={self.found}, item_flags={self.item_flags})"
                    
                    return Hint
                elif name == 'HintStatus':
                    class HintStatus:
                        NO_HINT = 0
                        HINT = 1
                        PRIORITY = 2
                        AVOID = 3
                        
                        def __init__(self, value=0):
                            self.value = value
                        
                        def __new__(cls, value=0):
                            obj = object.__new__(cls)
                            obj.value = value
                            return obj
                        
                        def __reduce__(self):
                            return (self.__class__, (self.value,))
                        
                        def __repr__(self):
                            status_names = {0: 'NO_HINT', 1: 'HINT', 2: 'PRIORITY', 3: 'AVOID'}
                            return f"HintStatus.{status_names.get(self.value, 'UNKNOWN')}"
                    return HintStatus
                else:
                    class GenericNetUtilsClass:
                        def __init__(self, *args, **kwargs):
                            pass
                    return GenericNetUtilsClass
            
            # For other missing modules, try to import normally
            try:
                return super().find_class(module, name)
            except (ImportError, AttributeError):
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
        unpickler = SafeUnpickler(io.BytesIO(decompressed_data))
        save_data = unpickler.load()
        
        logger.info(f"Successfully loaded save data using alternative method from {apsave_file}")
        return save_data
        
    except Exception as e:
        logger.error(f"Alternative parsing failed: {e}")
        raise e

def extract_player_data_from_save(save_data: Dict[str, Any]) -> Tuple[Dict[int, Dict[str, str]], Dict[str, Any]]:
    """Extract player and game data from save file when websocket connection is not available."""
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
    
    logger.debug(f"Extracted {len(all_players)} players from save data")
    return all_players, game_data

def save_datapackage_locally(game_data: Dict[str, Any], connection_data: Dict[str, Any], 
                            file_path: str = "datapackage.json") -> bool:
    """
    Save the Archipelago datapackage to a local JSON file for faster lookup.
    
    Args:
        game_data: Dictionary containing game data (item and location mappings)
        connection_data: Dictionary containing connection data (player information)
        file_path: Path to save the datapackage (default: datapackage.json)
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Create a combined data structure for efficient lookup
        datapackage = {
            "game_data": game_data,
            "connection_data": connection_data,
            "timestamp": datetime.now().isoformat(),
            "version": "1.0"
        }
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path) if os.path.dirname(file_path) else '.', exist_ok=True)
        
        # Save to JSON file
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(datapackage, f, indent=2)
            
        logger.info(f"Successfully saved datapackage to {file_path}")
        
        # Log some stats for debugging
        game_count = len(game_data)
        total_items = sum(len(game.get("item_name_to_id", {})) for game in game_data.values())
        total_locations = sum(len(game.get("location_name_to_id", {})) for game in game_data.values())
        player_count = sum(len(conn.get("slot_info", {})) for conn in connection_data.values())
        
        logger.debug(f"Datapackage stats: {game_count} games, {total_items} items, "
                    f"{total_locations} locations, {player_count} players")
        
        return True
    except Exception as e:
        logger.error(f"Error saving datapackage to {file_path}: {e}")
        return False

def parse_yaml_metadata(filepath: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract player name and game from Archipelago YAML file.

    Args:
        filepath: Path to YAML file

    Returns:
        Tuple of (player_name, game_name), both may be None if not found
    """
    try:
        from ruyaml import YAML

        with open(filepath, "r", encoding="utf-8") as yaml_file:
            yaml_object = YAML(typ="safe", pure=True)
            raw_data = yaml_object.load_all(yaml_file)
            data_list = list(raw_data)

            for element in data_list:
                player_name = element.get("name")
                game_name = element.get("game")
                if player_name:
                    return player_name, game_name

            return None, None
    except Exception as e:
        logger.error(f"Error parsing YAML metadata from {filepath}: {e}")
        return None, None

def load_local_datapackage(file_path: str = "datapackage.json") -> Optional[Dict[str, Any]]:
    """
    Load the Archipelago datapackage from a local JSON file.
    
    Args:
        file_path: Path to the datapackage file (default: datapackage.json)
        
    Returns:
        Optional[Dict[str, Any]]: The loaded datapackage or None if not available
    """
    try:
        if not os.path.exists(file_path):
            logger.debug(f"Datapackage file {file_path} not found")
            return None
            
        with open(file_path, 'r', encoding='utf-8') as f:
            datapackage = json.load(f)
            
        # Validate the datapackage structure
        if not all(key in datapackage for key in ["game_data", "connection_data", "timestamp"]):
            logger.warning(f"Invalid datapackage format in {file_path}")
            return None
            
        logger.info(f"Successfully loaded datapackage from {file_path} (created {datapackage.get('timestamp', 'unknown')})")
        
        # Log some stats for debugging
        game_data = datapackage.get("game_data", {})
        connection_data = datapackage.get("connection_data", {})
        
        game_count = len(game_data)
        player_count = sum(len(conn.get("slot_info", {})) for conn in connection_data.values())
        
        logger.debug(f"Loaded datapackage with {game_count} games and {player_count} players")
        
        return datapackage
    except Exception as e:
        logger.error(f"Error loading datapackage from {file_path}: {e}")
        return None

def delete_local_datapackage(file_path: str = "datapackage.json") -> bool:
    """
    Delete the local Archipelago datapackage file.
    
    Args:
        file_path: Path to the datapackage file (default: datapackage.json)
        
    Returns:
        bool: True if successful or file didn't exist, False on error
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Successfully deleted datapackage file {file_path}")
        else:
            logger.debug(f"Datapackage file {file_path} not found, nothing to delete")
        
        return True
    except Exception as e:
        logger.error(f"Error deleting datapackage file {file_path}: {e}")
        return False

def is_datapackage_available(file_path: str = "datapackage.json") -> bool:
    """
    Check if a local datapackage file is available and valid.
    
    Args:
        file_path: Path to the datapackage file (default: datapackage.json)
        
    Returns:
        bool: True if available and valid, False otherwise
    """
    try:
        if not os.path.exists(file_path):
            return False
            
        # Try to read the file to verify it's valid JSON
        with open(file_path, 'r', encoding='utf-8') as f:
            datapackage = json.load(f)
            
        # Check for required keys
        return all(key in datapackage for key in ["game_data", "connection_data", "timestamp"])
    except Exception:
        # If any error occurs, the datapackage is not available
        return False

def get_from_datapackage(key: str, file_path: str = "datapackage.json") -> Optional[Dict[str, Any]]:
    """
    Get a specific part of the datapackage (game_data or connection_data).
    
    Args:
        key: The key to retrieve ("game_data" or "connection_data")
        file_path: Path to the datapackage file (default: datapackage.json)
        
    Returns:
        Optional[Dict[str, Any]]: The requested data or None if not available
    """
    datapackage = load_local_datapackage(file_path)
    if not datapackage:
        return None
        
    return datapackage.get(key, {})

async def fetch_and_save_datapackage(server_url: str, password: str = None, 
                              file_path: str = "datapackage.json") -> bool:
    """
    Fetch the Archipelago datapackage from the server and save it locally.
    
    This is a utility function that combines the fetching operation with the saving operation.
    It delegates to server_helpers.fetch_server_data for the actual server connection.
    
    Args:
        server_url: Archipelago server URL
        password: Server password (optional)
        file_path: Path to save the datapackage (default: datapackage.json)
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Import here to avoid circular imports
        from helpers.server_helpers import fetch_server_data
        
        # Delete any existing datapackage first
        delete_local_datapackage(file_path)
        
        # Fetch server data - directly await the async function
        server_data = await fetch_server_data(server_url, password)
        
        if not server_data:
            logger.error(f"Failed to fetch server data from {server_url}")
            return False
            
        # Extract game_data and players from server_data
        game_data = server_data.get("game_data", {})
        players = server_data.get("players", {})
        
        # Create a compatible connection_data structure
        connection_data = {
            server_url: {
                "slot_info": {str(player_id): player_info for player_id, player_info in players.items()}
            }
        }
        
        # Save the datapackage locally - this returns a boolean, not a coroutine
        result = save_datapackage_locally(game_data, connection_data, file_path)
        return result
    except Exception as e:
        logger.error(f"Error fetching and saving datapackage: {e}")
        return False
