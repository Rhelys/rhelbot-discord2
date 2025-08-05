"""
Data management helper functions for Archipelago Discord bot.
"""

import json
import pickle
import zlib
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

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
    import sys
    
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
    import io
    from collections import namedtuple
    
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
