"""
Progress tracking and calculation helper functions for Archipelago Discord bot.
"""

import logging
import os
import pickle
import zlib
import zipfile
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Set

logger = logging.getLogger(__name__)

def get_player_total_locations(player_id: int, save_data: dict, output_directory: str = "./Archipelago/output/") -> int:
    """Get the actual total number of locations for a specific player from the multiworld data"""
    try:
        logger.debug(f"Attempting to get total locations for player {player_id}")
        logger.debug(f"Available save_data keys: {list(save_data.keys())}")
        
        # Method 1: Check the multiworld object for location data
        if "multiworld" in save_data:
            multiworld = save_data["multiworld"]
            logger.debug(f"Found multiworld object, type: {type(multiworld)}")
            
            try:
                # Try to access worlds array
                if hasattr(multiworld, 'worlds'):
                    logger.debug(f"Found worlds attribute, length: {len(multiworld.worlds) if hasattr(multiworld.worlds, '__len__') else 'unknown'}")
                    if len(multiworld.worlds) > player_id:
                        world = multiworld.worlds[player_id]
                        logger.debug(f"Found world for player {player_id}, type: {type(world)}")
                        
                        # Try different location attributes
                        if hasattr(world, 'location_table'):
                            locations = world.location_table
                            logger.debug(f"Found location_table with {len(locations)} locations")
                            return len(locations)
                        elif hasattr(world, 'locations'):
                            locations = world.locations
                            logger.debug(f"Found locations with {len(locations)} locations")
                            return len(locations)
                        elif hasattr(world, 'location_count'):
                            count = world.location_count
                            logger.debug(f"Found world.location_count: {count}")
                            return count
                
                # Try to access location counts directly from multiworld
                if hasattr(multiworld, 'location_count'):
                    logger.debug(f"Found multiworld.location_count, type: {type(multiworld.location_count)}")
                    if hasattr(multiworld.location_count, '__getitem__'):
                        try:
                            count = multiworld.location_count[player_id]
                            logger.debug(f"Found location_count[{player_id}]: {count}")
                            return count
                        except (KeyError, IndexError) as e:
                            logger.debug(f"Could not access location_count[{player_id}]: {e}")
                
            except Exception as e:
                logger.debug(f"Error accessing multiworld data: {e}")
        else:
            logger.debug("No 'multiworld' key found in save_data")
        
        # Method 2: Look for location-related data structures in save_data
        location_related_keys = [key for key in save_data.keys() if 'location' in key.lower()]
        logger.debug(f"Location-related keys in save_data: {location_related_keys}")
        
        for key in location_related_keys:
            value = save_data[key]
            logger.debug(f"Examining {key}, type: {type(value)}")
            
            # Skip location_checks as it contains checked locations, not total locations
            if key == 'location_checks':
                if isinstance(value, dict):
                    player_keys = [k for k in value.keys() if isinstance(k, tuple) and len(k) == 2 and k[1] == player_id]
                    if player_keys:
                        player_data = value[player_keys[0]]
                        if isinstance(player_data, (list, set)):
                            logger.debug(f"Found {len(player_data)} CHECKED locations in {key} for player {player_id} (not total)")
                continue
        
        # Method 3: Try to extract from archipelago file if it exists
        archipelago_file = find_archipelago_file(output_directory)
        if archipelago_file:
            logger.debug(f"Found .archipelago file: {archipelago_file}")
            location_count = get_locations_from_archipelago_file(archipelago_file, player_id)
            if location_count > 0:
                logger.debug(f"Got {location_count} locations from .archipelago file")
                return location_count
        else:
            logger.debug("No .archipelago file found")
        
        logger.debug(f"Could not determine total locations for player {player_id}")
        return 0
        
    except Exception as e:
        logger.error(f"Error getting total locations for player {player_id}: {e}")
        return 0

def find_archipelago_file(output_directory: str = "./Archipelago/output/") -> Optional[Path]:
    """Find the .archipelago file in the output directory or extract it from donkey.zip"""
    output_path = Path(output_directory)
    
    # First check if .archipelago file already exists in output directory
    archipelago_files = list(output_path.glob("*.archipelago"))
    if archipelago_files:
        # Return the most recent .archipelago file
        return max(archipelago_files, key=lambda f: f.stat().st_mtime)
    
    # If not found, try to extract it from donkey.zip
    donkey_zip_path = output_path / "donkey.zip"
    if donkey_zip_path.exists():
        try:
            logger.debug(f"Looking for .archipelago file in {donkey_zip_path}")
            with zipfile.ZipFile(donkey_zip_path, 'r') as zip_file:
                # Look for .archipelago files in the zip
                archipelago_files_in_zip = [f for f in zip_file.namelist() if f.endswith('.archipelago')]
                
                if archipelago_files_in_zip:
                    archipelago_file_in_zip = archipelago_files_in_zip[0]
                    logger.debug(f"Found {archipelago_file_in_zip} in donkey.zip")
                    
                    # Extract it to the output directory
                    extracted_path = output_path / Path(archipelago_file_in_zip).name
                    with zip_file.open(archipelago_file_in_zip) as source:
                        with open(extracted_path, 'wb') as target:
                            target.write(source.read())
                    
                    logger.debug(f"Extracted .archipelago file to {extracted_path}")
                    return extracted_path
                else:
                    logger.debug("No .archipelago file found in donkey.zip")
                    
        except Exception as e:
            logger.debug(f"Error extracting .archipelago file from donkey.zip: {e}")
    else:
        logger.debug("donkey.zip not found")
    
    return None

def get_locations_from_archipelago_file(archipelago_file: Path, player_id: int) -> int:
    """Extract location count for a specific player from the .archipelago file"""
    try:
        with open(archipelago_file, 'rb') as f:
            raw_data = f.read()
        
        # .archipelago files are zlib compressed pickle files with a 1-byte header
        skipped_data = raw_data[1:]  # Skip first byte
        decompressed_data = zlib.decompress(skipped_data)
        
        # Use a custom unpickler that can handle missing modules
        class ArchipelagoUnpickler(pickle.Unpickler):
            def find_class(self, module, name):
                # Handle missing modules by creating generic placeholders
                if module in ['NetUtils', 'worlds', 'BaseClasses']:
                    class GenericClass:
                        def __init__(self, *args, **kwargs):
                            for i, arg in enumerate(args):
                                setattr(self, f'arg_{i}', arg)
                            for key, value in kwargs.items():
                                setattr(self, key, value)
                        
                        def __getitem__(self, key):
                            return getattr(self, key, None)
                        
                        def __setitem__(self, key, value):
                            setattr(self, key, value)
                        
                        def get(self, key, default=None):
                            return getattr(self, key, default)
                        
                        def keys(self):
                            return [attr for attr in dir(self) if not attr.startswith('_')]
                        
                        def __len__(self):
                            return len(self.keys())
                        
                        def __contains__(self, key):
                            return hasattr(self, key)
                    
                    return GenericClass
                
                # For other modules, try to import normally
                try:
                    return super().find_class(module, name)
                except (ImportError, AttributeError):
                    class GenericClass:
                        def __init__(self, *args, **kwargs):
                            pass
                    return GenericClass
        
        import io
        unpickler = ArchipelagoUnpickler(io.BytesIO(decompressed_data))
        multidata = unpickler.load()
        
        logger.debug(f"Successfully parsed .archipelago file, type: {type(multidata)}")
        
        # Look for location data in the multidata
        if hasattr(multidata, '__getitem__') or isinstance(multidata, dict):
            locations_data = None
            
            # Try different ways to access location data
            for key in ['locations', 'location_table', 'location_tables', 'world_locations']:
                try:
                    if key in multidata:
                        locations_data = multidata[key]
                        logger.debug(f"Found '{key}' key, type: {type(locations_data)}")
                        break
                except:
                    continue
            
            # Now try to extract player-specific location count
            if locations_data and hasattr(locations_data, '__getitem__'):
                # Try different player ID formats
                for pid in [player_id, str(player_id), (0, player_id)]:
                    try:
                        if pid in locations_data:
                            player_locations = locations_data[pid]
                            if hasattr(player_locations, '__len__'):
                                count = len(player_locations)
                                logger.debug(f"Found {count} locations for player {player_id} using key {pid}")
                                return count
                    except Exception as e:
                        continue
        
        return 0
            
    except Exception as e:
        logger.error(f"Error reading .archipelago file: {e}")
        return 0

def get_player_hint_points(player_id: int, save_data: dict, get_total_locations_func) -> int:
    """Get the current hint points for a specific player"""
    try:
        # Check if there's a direct hint_points field in save_data
        if "hint_points" in save_data:
            hint_points = save_data["hint_points"]
            if isinstance(hint_points, dict):
                # Check for player-specific hint points
                for key in [player_id, str(player_id), (0, player_id)]:
                    if key in hint_points:
                        return hint_points[key]
        
        # Calculate based on hints_used and checked locations
        hints_used_data = save_data.get("hints_used", {})
        hints_used = 0
        
        # Check for hints used by this player
        for key in [(0, player_id), player_id, str(player_id)]:
            if key in hints_used_data:
                hints_used = hints_used_data[key]
                break
        
        logger.debug(f"Player {player_id} has used {hints_used} hints")
        
        # Get checked locations for this player
        location_checks = save_data.get("location_checks", {})
        checked_locations = 0
        
        # Check for checked locations by this player
        for key in [(0, player_id), player_id, str(player_id)]:
            if key in location_checks:
                checked_locations_set = location_checks[key]
                checked_locations = len(checked_locations_set) if hasattr(checked_locations_set, '__len__') else 0
                break
        
        logger.debug(f"Player {player_id} has checked {checked_locations} locations")
        
        # Get total locations for this player to calculate hint cost
        total_locations = get_total_locations_func(player_id, save_data)
        
        if total_locations > 0:
            # Calculate hint cost (5% of total locations per hint)
            hint_cost = max(10, int(total_locations * 0.05))
            
            # Calculate hint points: checked_locations - (hints_used * hint_cost)
            remaining_points = checked_locations - (hints_used * hint_cost)
            
            logger.debug(f"Player {player_id} calculation: {checked_locations} checked - ({hints_used} * {hint_cost}) = {remaining_points}")
            
            return max(0, remaining_points)  # Don't return negative points
        
        logger.debug(f"Could not calculate hint points for player {player_id}, returning 0")
        return 0
        
    except Exception as e:
        logger.error(f"Error getting hint points for player {player_id}: {e}")
        return 0

def get_hint_cost(player_id: int, save_data: dict, get_total_locations_func) -> int:
    """Get the cost of the next hint for a specific player"""
    try:
        # Check if there's a hint_cost field in save_data
        if "hint_cost" in save_data:
            hint_cost = save_data["hint_cost"]
            if isinstance(hint_cost, dict):
                for key in [player_id, str(player_id), (0, player_id)]:
                    if key in hint_cost:
                        return hint_cost[key]
        
        # Calculate hint cost based on player's total locations
        total_locations = get_total_locations_func(player_id, save_data)
        
        if total_locations > 0:
            # Calculate cost: 5% of total locations, with a minimum of 10
            calculated_cost = max(10, int(total_locations * 0.05))
            logger.debug(f"Calculated hint cost for player {player_id}: {calculated_cost} (5% of {total_locations} total locations)")
            return calculated_cost
        else:
            # Fallback: Count existing hints and use old formula
            hints_data = save_data.get("hints", {})
            player_hint_count = 0
            
            for hint_set in hints_data.values():
                if isinstance(hint_set, set):
                    for hint in hint_set:
                        if hasattr(hint, 'receiving_player') and hint.receiving_player == player_id:
                            player_hint_count += 1
            
            # Use fallback formula: 10 + (hints_owned * 10)
            calculated_cost = 10 + (player_hint_count * 10)
            logger.debug(f"Fallback hint cost calculation for player {player_id}: {calculated_cost} (based on {player_hint_count} existing hints)")
            return calculated_cost
        
    except Exception as e:
        logger.error(f"Error getting hint cost for player {player_id}: {e}")
        return 10  # Default cost

def filter_key_item_hints(all_hints: list) -> list:
    """Filter hints for key items (item_flags = 1) and return processed hint objects"""
    key_item_hints = []
    
    for hint in all_hints:
        # Check if this is a Hint object with item_flags = 1 (progression items)
        if hasattr(hint, 'item_flags') and hint.item_flags == 1:
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
            key_item_hints.append(simple_hint)
    
    return key_item_hints

def extract_hints_from_save_data(save_data: dict) -> list:
    """Extract all hints from the save data dictionary and deduplicate"""
    hints_data = save_data.get("hints", {})
    if not hints_data:
        return []
    
    # Extract all hints from the dictionary of sets and deduplicate
    all_hints_set = set()
    for hint_set in hints_data.values():
        if isinstance(hint_set, set):
            all_hints_set.update(hint_set)
        elif isinstance(hint_set, (list, tuple)):
            all_hints_set.update(hint_set)
        elif hint_set:  # Single hint object
            all_hints_set.add(hint_set)
    
    return list(all_hints_set)
