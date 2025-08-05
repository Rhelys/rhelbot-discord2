"""
Helper modules for rhelbot Discord bot.
"""

from .data_helpers import (
    load_apsave_data,
    parse_apsave_alternative,
    load_game_status,
    save_game_status,
    extract_player_data_from_save
)

from .lookup_helpers import (
    lookup_item_name,
    lookup_location_name,
    lookup_player_name,
    lookup_player_game,
    lookup_in_mapping
)

from .server_helpers import (
    is_server_running,
    kill_server_processes,
    get_server_password,
    create_connection_message,
    connect_to_server
)

from .formatting_helpers import (
    create_progress_bar,
    process_hint_response,
    resolve_hint_pattern,
    format_hint_message
)

from .progress_helpers import (
    get_player_total_locations,
    find_archipelago_file,
    get_locations_from_archipelago_file,
    get_player_hint_points,
    get_hint_cost,
    filter_key_item_hints,
    extract_hints_from_save_data
)

__all__ = [
    # Data helpers
    'load_apsave_data',
    'parse_apsave_alternative', 
    'load_game_status',
    'save_game_status',
    'extract_player_data_from_save',
    
    # Lookup helpers
    'lookup_item_name',
    'lookup_location_name',
    'lookup_player_name',
    'lookup_player_game',
    'lookup_in_mapping',
    
    # Server helpers
    'is_server_running',
    'kill_server_processes',
    'get_server_password',
    'create_connection_message',
    'connect_to_server',
    
    # Formatting helpers
    'create_progress_bar',
    'process_hint_response',
    'resolve_hint_pattern',
    'format_hint_message',
    
    # Progress helpers
    'get_player_total_locations',
    'find_archipelago_file',
    'get_locations_from_archipelago_file',
    'get_player_hint_points',
    'get_hint_cost',
    'filter_key_item_hints',
    'extract_hints_from_save_data'
]
