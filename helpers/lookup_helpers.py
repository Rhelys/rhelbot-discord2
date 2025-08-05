"""
Lookup and name resolution helper functions for Archipelago Discord bot.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def lookup_in_mapping(mapping: dict, lookup_id: int, mapping_name: str) -> Optional[str]:
    """Generic lookup function for ID to name mappings."""
    for name, id_value in mapping.items():
        if str(id_value) == str(lookup_id):
            logger.debug(f"Found match: {name}")
            return name
    return None

def lookup_item_name(game: str, item_id: int, game_data: Dict[str, Any]) -> str:
    """Look up item name from ID using game data."""
    logger.debug(f"Looking up item: game='{game}', item_id={item_id}")
    
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
    
    item_mapping = game_info["item_name_to_id"]
    logger.debug(f"Searching through {len(item_mapping)} items for ID {item_id}")
    
    result = lookup_in_mapping(item_mapping, item_id, "item")
    if result:
        return result
    
    logger.debug(f"No match found for item ID {item_id}")
    return f"Item {item_id}"

def lookup_location_name(game: str, location_id: int, game_data: Dict[str, Any]) -> str:
    """Look up location name from ID using game data."""
    logger.debug(f"Looking up location: game='{game}', location_id={location_id}")
    
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
    
    location_mapping = game_info["location_name_to_id"]
    logger.debug(f"Searching through {len(location_mapping)} locations for ID {location_id}")
    
    result = lookup_in_mapping(location_mapping, location_id, "location")
    if result:
        return result
    
    logger.debug(f"No match found for location ID {location_id}")
    return f"Location {location_id}"

def lookup_player_info(player_id: int, info_key: str, default_value: str, connection_data: Dict[str, Any]) -> str:
    """Generic function to look up player information from connection data."""
    for server_url, conn_data in connection_data.items():
        slot_info = conn_data.get("slot_info", {})
        for slot_id, player_info in slot_info.items():
            if str(slot_id) == str(player_id):
                return player_info.get(info_key, default_value)
    return default_value

def lookup_player_name(player_id: int, connection_data: Dict[str, Any]) -> str:
    """Look up player name from ID using connection data."""
    return lookup_player_info(player_id, "name", f"Player {player_id}", connection_data)

def lookup_player_game(player_id: int, connection_data: Dict[str, Any]) -> str:
    """Look up player's game from ID using connection data."""
    return lookup_player_info(player_id, "game", "Unknown", connection_data)
