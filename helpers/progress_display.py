"""
Progress display logic for Archipelago game tracking.
Handles calculation and formatting of player progress data.
"""

from pathlib import Path
import time
from typing import Optional, List, Tuple, Dict, Any


def validate_save_file_timestamp(output_directory: str, connection_data: dict, game_data: dict,
                                player_progress: dict) -> bool:
    """
    Validate if save file is recent relative to active connection.
    Returns True if we have active connection or save file is recent.
    """
    has_active_connection = bool(connection_data and game_data and player_progress)

    if has_active_connection:
        print("Using live tracking data from active WebSocket connection, supplemented by save file structure")
        return True
    else:
        print("Using save file data - no active connection detected")
        # When there's no active tracking, we can't be sure the save file is from the current game
        # Try to validate by checking if the save file is recent relative to server start
        output_path = Path(output_directory)
        apsave_files = list(output_path.glob("*.apsave"))

        if apsave_files:
            # Check the most recent save file
            most_recent_save = max(apsave_files, key=lambda f: f.stat().st_mtime)
            save_age_hours = (time.time() - most_recent_save.stat().st_mtime) / 3600

            if save_age_hours > 24:  # If save is older than 24 hours, warn user
                print(f"Warning: Save file is {save_age_hours:.1f} hours old - data may be from a previous game")
                return False
        return True


def get_player_progress_data(all_players: dict, location_checks: dict, activity_timer_dict: dict,
                           target_players: Optional[List[str]], show_specific_players: bool,
                           get_player_total_locations_func, create_progress_bar_func) -> List[str]:
    """
    Generate progress data for all or specific players.
    Returns list of formatted progress lines.
    """
    player_progress_data = []

    for player_id, player_info in all_players.items():
        player_name = player_info["name"]
        player_game = player_info["game"]

        # Skip the Rhelbot tracker
        if player_name.lower() == "rhelbot":
            continue

        # Filter for specific players if requested
        if show_specific_players and player_name not in target_players:
            continue

        # Get checked locations for this player from save data
        # location_checks format: {(team, slot): set of location_ids}
        checked_locations = location_checks.get((0, player_id), set())  # Assuming team 0
        checked_count = len(checked_locations)

        # Get total locations for this player from the actual multiworld data
        total_locations = get_player_total_locations_func(player_id)

        # Calculate percentage
        if total_locations > 0:
            percentage = (checked_count / total_locations) * 100
            is_complete = percentage >= 100.0

            # Add checkmark for 100% completion after the game name
            completion_indicator = " ✅" if is_complete else ""

            # Get last activity timestamp for this player
            last_activity_timestamp = activity_timer_dict.get((0, player_id))  # Assuming team 0
            timestamp_line = ""
            if last_activity_timestamp:
                # Convert to Unix timestamp and format for Discord
                unix_timestamp = int(last_activity_timestamp)
                timestamp_line = f"\n└ Last check time: <t:{unix_timestamp}:R>"

            progress_bar = create_progress_bar_func(percentage)
            player_line = (
                f"**{player_name}** ({player_game}){completion_indicator}\n"
                f"└ {checked_count}/{total_locations} locations ({percentage:.1f}%)\n"
                f"└ {progress_bar}{timestamp_line}\n"
            )
        else:
            # Get last activity timestamp for this player
            last_activity_timestamp = activity_timer_dict.get((0, player_id))  # Assuming team 0
            timestamp_line = ""
            if last_activity_timestamp:
                # Convert to Unix timestamp and format for Discord
                unix_timestamp = int(last_activity_timestamp)
                timestamp_line = f"\n└ Last check time: <t:{unix_timestamp}:R>"

            player_line = (
                f"**{player_name}** ({player_game})\n"
                f"└ {checked_count}/? locations (No location data available){timestamp_line}\n"
            )

        # Store for sorting
        player_progress_data.append((player_name.lower(), player_line))

    return player_progress_data


def format_progress_error_message(original_player: Optional[str], target_players: Optional[List[str]],
                                all_players: dict) -> str:
    """Format error message when no players found."""
    # List available players for reference
    available_players = [info["name"] for info in all_players.values()
                        if info["name"].lower() != "rhelbot"]

    # Customize error message based on the original input
    if original_player and original_player.lower() == "me":
        return (f"❌ You don't have any players in this game.\n"
                f"Available players: {', '.join(available_players)}")
    elif original_player and (original_player.startswith('@') or original_player.startswith('<@')):
        return (f"❌ The mentioned Discord user doesn't have any players in this game.\n"
                f"Available players: {', '.join(available_players)}")
    else:
        player_name = target_players[0] if target_players else original_player
        return (f"❌ Player '{player_name}' not found.\n"
                f"Available players: {', '.join(available_players)}")


def calculate_total_game_progress(all_players: dict, location_checks: dict,
                                get_player_total_locations_func) -> Tuple[int, int, float]:
    """
    Calculate total game progress across all players.
    Returns (total_checked, total_locations, overall_percentage).
    """
    total_checked = 0
    total_locations = 0

    for player_id, player_info in all_players.items():
        player_name = player_info["name"]

        # Skip the Rhelbot tracker
        if player_name.lower() == "rhelbot":
            continue

        # Get checked locations for this player
        checked_locations = location_checks.get((0, player_id), set())
        checked_count = len(checked_locations)
        total_checked += checked_count

        # Get total locations for this player
        player_total_locations = get_player_total_locations_func(player_id)
        if player_total_locations > 0:
            total_locations += player_total_locations

    # Calculate overall percentage
    overall_percentage = (total_checked / total_locations * 100) if total_locations > 0 else 0

    return total_checked, total_locations, overall_percentage


def format_progress_header(target_players: Optional[List[str]], total_checked: int,
                         total_locations: int, overall_percentage: float,
                         create_progress_bar_func) -> str:
    """Format the header section of progress display."""
    if target_players:
        if len(target_players) == 1:
            return f"📊 **Progress for {target_players[0]}**\n"
        else:
            return f"📊 **Progress for: {', '.join(target_players)}**\n"
    else:
        overall_progress_bar = create_progress_bar_func(overall_percentage)
        return (f"📊 **Overall Progress**: {total_checked}/{total_locations} "
                f"({overall_percentage:.1f}%)\n{overall_progress_bar}\n\n")


def create_progress_sections(progress_lines: List[str], max_length: int = 1800) -> List[str]:
    """
    Split progress data into sections that fit Discord message limits.
    Returns list of message sections.
    """
    sections = []
    current_section = ""

    for line in progress_lines:
        # Check if adding this line would exceed the limit
        if len(current_section) + len(line) > max_length:
            if current_section:
                sections.append(current_section.strip())
                current_section = line
            else:
                # Single line is too long, add it anyway
                sections.append(line.strip())
        else:
            current_section += line

    # Add the last section if it has content
    if current_section.strip():
        sections.append(current_section.strip())

    return sections


async def load_and_validate_game_data(interaction, connection_data: Dict, game_data: Dict,
                                    save_data: Dict, fetch_server_data_func,
                                    extract_player_data_func) -> Tuple[Dict, Dict]:
    """
    Load and validate player and game data from connection or server.
    Returns (all_players, game_data).
    """
    all_players = {}
    validated_game_data = {}

    # First try to get data from active websocket connection
    if connection_data and game_data:
        for server_key, conn_data in connection_data.items():
            slot_info = conn_data.get("slot_info", {})
            for slot_id, player_info in slot_info.items():
                player_id = int(slot_id)
                all_players[player_id] = {
                    "name": player_info.get("name", f"Player {player_id}"),
                    "game": player_info.get("game", "Unknown")
                }
        validated_game_data = game_data
    else:
        # If no websocket connection, connect to server to get current game data and validate against save file
        await interaction.edit_original_response(content="📡 Connecting to server to get current game data...")

        server_data = await fetch_server_data_func()
        if server_data:
            all_players = server_data["players"]
            validated_game_data = server_data["game_data"]

            # Validate that current server players match save file players
            save_players = set()
            for (team, slot), locations in save_data.get("location_checks", {}).items():
                if team == 0:  # Assuming team 0
                    save_players.add(slot)

            current_players = set(all_players.keys())

            # If players don't match, warn about potential mismatch
            if save_players and current_players and not save_players.intersection(current_players):
                await interaction.edit_original_response(
                    content="⚠️ **Warning**: Save file players don't match current server players. "
                    f"This save file appears to be from a different game session.\n\n"
                )
        else:
            # Fallback: try to extract basic data from save file
            all_players, validated_game_data = extract_player_data_func(save_data)

    return all_players, validated_game_data


def merge_real_time_tracking_data(location_checks: Dict, player_progress: Dict) -> Dict:
    """
    Merge real-time tracking data with save data for most up-to-date information.
    Returns updated location_checks.
    """
    updated_location_checks = location_checks.copy()

    # This ensures we show the latest location checks even if the save file hasn't been updated yet
    for player_id, real_time_locations in player_progress.items():
        # Get the current save data for this player
        save_locations = location_checks.get((0, player_id), set())

        # Merge real-time data with save data (union of both sets)
        merged_locations = save_locations.union(real_time_locations)
        updated_location_checks[(0, player_id)] = merged_locations

    return updated_location_checks


def parse_activity_timers(client_activity_timers: Any) -> Dict:
    """
    Parse client activity timers from save data into a usable dictionary.
    Returns activity_timer_dict.
    """
    activity_timer_dict = {}
    if isinstance(client_activity_timers, (list, tuple)):
        for entry in client_activity_timers:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                player_key, timestamp = entry[0], entry[1]
                if isinstance(player_key, (list, tuple)) and len(player_key) >= 2:
                    team, slot = player_key[0], player_key[1]
                    activity_timer_dict[(team, slot)] = timestamp

    return activity_timer_dict


async def check_save_file_mismatch(interaction, has_active_connection: bool, all_players: Dict,
                                 location_checks: Dict):
    """Check for potential save file mismatch and warn user if needed."""
    if has_active_connection and all_players:
        # Check if the players in the connection match those in the save file
        save_players = set()
        for (team, slot), locations in location_checks.items():
            if team == 0:  # Assuming team 0
                save_players.add(slot)

        connection_players = set(all_players.keys())

        # If there's a significant mismatch, warn that data might be from different games
        if save_players and not save_players.intersection(connection_players):
            await interaction.edit_original_response(
                content="⚠️ **Warning**: Save file appears to be from a different game session than currently running. Showing live tracking data where available.\n\n"
            )