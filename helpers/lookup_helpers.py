"""
Lookup and name resolution helper functions for Archipelago Discord bot.
"""

import logging
import os
from typing import Dict, Any, Optional
from helpers.data_helpers import get_from_datapackage

logger = logging.getLogger(__name__)

# Default datapackage file path
DEFAULT_DATAPACKAGE_PATH = "datapackage.json"

def lookup_in_mapping(mapping: dict, lookup_id: int, mapping_name: str) -> Optional[str]:
    """Generic lookup function for ID to name mappings."""
    for name, id_value in mapping.items():
        if str(id_value) == str(lookup_id):
            logger.debug(f"Found match: {name}")
            return name
    return None

def lookup_item_name(game: str, item_id: int, game_data: Dict[str, Any] = None, 
                    file_path: str = DEFAULT_DATAPACKAGE_PATH) -> str:
    """
    Look up item name from ID using game data.
    
    This function first tries to use local datapackage if available,
    then falls back to provided game_data.
    
    Args:
        game: Game name
        item_id: Item ID to look up
        game_data: Optional game data from server (fallback)
        file_path: Path to local datapackage file
        
    Returns:
        str: Item name or "Item {item_id}" if not found
    """
    logger.debug(f"Looking up item: game='{game}', item_id={item_id}")
    
    # Try to use the local datapackage first if game_data wasn't provided
    local_data_used = False
    if not game_data and os.path.exists(file_path):
        try:
            local_game_data = get_from_datapackage("game_data", file_path)
            if local_game_data:
                logger.debug("Using local datapackage for item lookup")
                game_data = local_game_data
                local_data_used = True
        except Exception as e:
            logger.debug(f"Error using local datapackage: {e}")
    
    # Proceed with lookup using available game_data
    if not game_data:
        logger.debug("No game data available")
        return f"Item {item_id}"
        
    if game not in game_data:
        logger.debug(f"Game '{game}' not found in game data. Available games: {list(game_data.keys())}")
        return f"Item {item_id}"
    
    game_info = game_data[game]
    if "item_name_to_id" not in game_info:
        logger.debug(f"No item_name_to_id in game data for '{game}'. Available keys: {list(game_info.keys())}")
        return f"Item {item_id}"
    
    # Optimize lookup using a reversed mapping for direct access
    item_mapping = game_info["item_name_to_id"]
    
    # Create a reversed mapping (id -> name) for faster lookup
    id_to_name = {}
    for name, id_value in item_mapping.items():
        id_to_name[str(id_value)] = name
    
    # Do direct lookup instead of iterating
    item_id_str = str(item_id)
    if item_id_str in id_to_name:
        result = id_to_name[item_id_str]
        logger.debug(f"Found item match: {result}" + (" (from local datapackage)" if local_data_used else ""))
        return result
    
    # Fallback to the old method if reversed mapping didn't work
    result = lookup_in_mapping(item_mapping, item_id, "item")
    if result:
        logger.debug(f"Found item match (fallback): {result}")
        return result
    
    logger.debug(f"No match found for item ID {item_id}")
    return f"Item {item_id}"

def lookup_location_name(game: str, location_id: int, game_data: Dict[str, Any] = None,
                        file_path: str = DEFAULT_DATAPACKAGE_PATH) -> str:
    """
    Look up location name from ID using game data.
    
    This function first tries to use local datapackage if available,
    then falls back to provided game_data.
    
    Args:
        game: Game name
        location_id: Location ID to look up
        game_data: Optional game data from server (fallback)
        file_path: Path to local datapackage file
        
    Returns:
        str: Location name or "Location {location_id}" if not found
    """
    logger.debug(f"Looking up location: game='{game}', location_id={location_id}")
    
    # Try to use the local datapackage first if game_data wasn't provided
    local_data_used = False
    if not game_data and os.path.exists(file_path):
        try:
            local_game_data = get_from_datapackage("game_data", file_path)
            if local_game_data:
                logger.debug("Using local datapackage for location lookup")
                game_data = local_game_data
                local_data_used = True
        except Exception as e:
            logger.debug(f"Error using local datapackage: {e}")
    
    # Proceed with lookup using available game_data
    if not game_data:
        logger.debug("No game data available")
        return f"Location {location_id}"
        
    if game not in game_data:
        logger.debug(f"Game '{game}' not found in game data. Available games: {list(game_data.keys())}")
        return f"Location {location_id}"
    
    game_info = game_data[game]
    if "location_name_to_id" not in game_info:
        logger.debug(f"No location_name_to_id in game data for '{game}'. Available keys: {list(game_info.keys())}")
        return f"Location {location_id}"
    
    # Optimize lookup using a reversed mapping for direct access
    location_mapping = game_info["location_name_to_id"]
    
    # Create a reversed mapping (id -> name) for faster lookup
    id_to_name = {}
    for name, id_value in location_mapping.items():
        id_to_name[str(id_value)] = name
    
    # Do direct lookup instead of iterating
    location_id_str = str(location_id)
    if location_id_str in id_to_name:
        result = id_to_name[location_id_str]
        logger.debug(f"Found location match: {result}" + (" (from local datapackage)" if local_data_used else ""))
        return result
    
    # Fallback to the old method if reversed mapping didn't work
    result = lookup_in_mapping(location_mapping, location_id, "location")
    if result:
        logger.debug(f"Found location match (fallback): {result}")
        return result
    
    logger.debug(f"No match found for location ID {location_id}")
    return f"Location {location_id}"

def lookup_player_info(player_id: int, info_key: str, default_value: str, 
                      connection_data: Dict[str, Any] = None,
                      file_path: str = DEFAULT_DATAPACKAGE_PATH) -> str:
    """
    Generic function to look up player information from connection data.
    
    This function first tries to use local datapackage if available,
    then falls back to provided connection_data.
    
    Args:
        player_id: Player ID to look up
        info_key: Key to look up in player info (e.g., "name", "game")
        default_value: Default value to return if not found
        connection_data: Optional connection data from server (fallback)
        file_path: Path to local datapackage file
        
    Returns:
        str: Player info value or default_value if not found
    """
    # Try to use the local datapackage first if connection_data wasn't provided
    local_data_used = False
    if not connection_data and os.path.exists(file_path):
        try:
            local_connection_data = get_from_datapackage("connection_data", file_path)
            if local_connection_data:
                logger.debug(f"Using local datapackage for player lookup (key: {info_key})")
                connection_data = local_connection_data
                local_data_used = True
        except Exception as e:
            logger.debug(f"Error using local datapackage: {e}")
    
    # Proceed with lookup using available connection_data
    if not connection_data:
        logger.debug("No connection data available")
        return default_value
    
    # Optimize by creating a direct player ID to info mapping
    player_id_str = str(player_id)
    
    # Try to find the player in any server's slot_info
    for server_url, conn_data in connection_data.items():
        slot_info = conn_data.get("slot_info", {})
        if player_id_str in slot_info:
            result = slot_info[player_id_str].get(info_key, default_value)
            logger.debug(f"Found player {info_key} match: {result}" + 
                         (" (from local datapackage)" if local_data_used else ""))
            return result
    
    # Fallback to the old method (for slot_id that may be different format)
    for server_url, conn_data in connection_data.items():
        slot_info = conn_data.get("slot_info", {})
        for slot_id, player_info in slot_info.items():
            if str(slot_id) == player_id_str:
                result = player_info.get(info_key, default_value)
                logger.debug(f"Found player {info_key} match (fallback): {result}")
                return result
    
    logger.debug(f"No match found for player ID {player_id}, key {info_key}")
    return default_value

def lookup_player_name(player_id: int, connection_data: Dict[str, Any] = None, 
                       file_path: str = DEFAULT_DATAPACKAGE_PATH) -> str:
    """
    Look up player name from ID using connection data.
    
    This function first tries to use local datapackage if available,
    then falls back to provided connection_data.
    
    Args:
        player_id: Player ID to look up
        connection_data: Optional connection data from server (fallback)
        file_path: Path to local datapackage file
        
    Returns:
        str: Player name or "Player {player_id}" if not found
    """
    return lookup_player_info(player_id, "name", f"Player {player_id}", connection_data, file_path)

def lookup_player_game(player_id: int, connection_data: Dict[str, Any] = None,
                       file_path: str = DEFAULT_DATAPACKAGE_PATH) -> str:
    """
    Look up player's game from ID using connection data.
    
    This function first tries to use local datapackage if available,
    then falls back to provided connection_data.
    
    Args:
        player_id: Player ID to look up
        connection_data: Optional connection data from server (fallback)
        file_path: Path to local datapackage file
        
    Returns:
        str: Player's game or "Unknown" if not found
    """
    return lookup_player_info(player_id, "game", "Unknown", connection_data, file_path)
