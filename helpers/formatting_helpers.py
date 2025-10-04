"""
Formatting and message processing helper functions for Archipelago Discord bot.
"""

import re
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def create_progress_bar(percentage: float, length: int = 20) -> str:
    """Create a visual progress bar."""
    filled_length = int(length * percentage / 100)
    bar = "█" * filled_length + "░" * (length - filled_length)
    return f"[{bar}] {percentage:.1f}%"

def format_hint_message(hint_data: Dict[str, Any], player_name: str) -> str:
    """Format a hint message for display."""
    try:
        item_name = hint_data.get('item', 'Unknown Item')
        location_name = hint_data.get('location', 'Unknown Location')
        player_owner = hint_data.get('player', 'Unknown Player')
        
        if player_owner == player_name:
            message = f"🔍 **Hint for {player_name}**: Your item **{item_name}** is at **{location_name}**"
        else:
            message = f"🔍 **Hint for {player_name}**: **{item_name}** (belongs to {player_owner}) is at **{location_name}**"
        
        logger.debug(f"Formatted hint message for player {player_name}")
        return message
    except Exception as e:
        logger.error(f"Error formatting hint message for player {player_name}: {e}")
        return f"🔍 **Hint for {player_name}**: Error formatting hint message"

def resolve_hint_pattern(match, player_game: str, pattern_type: str, game_data: Dict[str, Any], lookup_item_func, lookup_location_func) -> str:
    """Helper method to resolve hint patterns like 'ItemID is at LocationID'."""
    try:
        item_id = int(match.group(1))
        location_id = int(match.group(2))
        
        # Look up names
        item_name = lookup_item_func(player_game, item_id)
        location_name = lookup_location_func(player_game, location_id)
        
        # Clean up names (remove "Item XXXX" or "Location XXXX" formats)
        if item_name and item_name != f"Item {item_id}" and "Item" not in item_name:
            resolved_item = item_name
        else:
            resolved_item = f"Item {item_id}"
        
        if location_name and location_name != f"Location {location_id}" and "Location" not in location_name:
            resolved_location = location_name
        else:
            resolved_location = f"Location {location_id}"
        
        return f"{resolved_item} {pattern_type} {resolved_location}"
        
    except Exception as e:
        logger.error(f"Error resolving hint pattern: {e}")
        return match.group(0)  # Return original if error

async def process_hint_response(hint_text: str, player_game: str, game_data: Dict[str, Any], lookup_item_func, lookup_location_func, fetch_server_data_func) -> str:
    """Process hint response to resolve item and location names from IDs."""
    try:
        # First, try to get game data if we don't have it
        if not game_data:
            logger.debug("No game data available for hint processing, fetching...")
            server_data = await fetch_server_data_func()
            if server_data and server_data.get("game_data"):
                game_data = server_data["game_data"]
                logger.debug(f"Fetched game data for hint processing: {list(game_data.keys())}")
            else:
                logger.debug("Failed to fetch game data for hint processing")
                return hint_text  # Return original if can't get data
        
        processed_text = hint_text
        
        # Look for patterns that might contain IDs to resolve
        if player_game in game_data:
            # Look for item and location IDs in various patterns
            id_pattern = r'\b(\d+)\b'
            ids_found = re.findall(id_pattern, processed_text)
            
            # Create a map of resolved IDs to avoid duplicates
            id_replacements = {}
            
            for id_str in ids_found:
                try:
                    id_num = int(id_str)
                    
                    # Skip very large numbers (likely not game IDs)
                    if id_num > 100000:
                        continue
                    
                    # Try as item ID first
                    item_name = lookup_item_func(player_game, id_num)
                    if item_name and item_name != f"Item {id_num}" and "Item" not in item_name:
                        id_replacements[id_str] = item_name
                        continue

                    # Try as location ID
                    location_name = lookup_location_func(player_game, id_num)
                    if location_name and location_name != f"Location {id_num}" and "Location" not in location_name:
                        id_replacements[id_str] = location_name
                        continue
                        
                except ValueError:
                    continue
            
            # Apply all replacements, but only replace raw IDs with names (no ID numbers shown)
            for id_str, name in id_replacements.items():
                # Replace only if the ID appears as a standalone number
                processed_text = re.sub(rf'\b{id_str}\b', name, processed_text)
        
        # Advanced pattern matching for common Archipelago hint formats
        # Pattern: "ItemName is at LocationName"
        processed_text = re.sub(r'\b(\d+)\s+is\s+at\s+(\d+)\b', 
                              lambda m: resolve_hint_pattern(m, player_game, "is at", game_data, lookup_item_func, lookup_location_func), 
                              processed_text)
        
        # Pattern: "ItemName found at LocationName"
        processed_text = re.sub(r'\b(\d+)\s+found\s+at\s+(\d+)\b', 
                              lambda m: resolve_hint_pattern(m, player_game, "found at", game_data, lookup_item_func, lookup_location_func), 
                              processed_text)
        
        # Clean up any remaining unreplaced patterns that look like "Item XXXX" or "Location XXXX"
        processed_text = re.sub(r'\bItem\s+(\d+)\b', r'Item \1', processed_text)
        processed_text = re.sub(r'\bLocation\s+(\d+)\b', r'Location \1', processed_text)
        
        logger.debug(f"Hint processing: '{hint_text}' -> '{processed_text}'")
        return processed_text
        
    except Exception as e:
        logger.error(f"Error processing hint response: {e}")
        return hint_text  # Return original text if processing fails
