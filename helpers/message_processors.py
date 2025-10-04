"""
Message processors for handling different Archipelago message types.
This module contains functions to process and format AP messages for Discord.
"""

async def process_connected_message(msg: dict, channel, connection_data: dict):
    """Process Connected message type"""
    # Store connection data for player lookups - use a simpler approach
    # Since we might have multiple servers, store all connection data
    server_key = f"connection_{len(connection_data)}"  # Simple key generation
    connection_data[server_key] = msg
    print(f"Stored connection data: {msg.get('slot_info', {})}")

    players = msg.get("slot_info", {})
    if players:
        player_list = ", ".join([f"{info['name']} ({info['game']})" for info in players.values()])
        await channel.send(f"🎮 **Game Connected**\nPlayers: {player_list}")
    else:
        await channel.send(f"🎮 **Connected to Archipelago server**")


async def process_connection_refused_message(msg: dict, channel):
    """Process ConnectionRefused message type"""
    errors = msg.get("errors", ["Unknown error"])
    await channel.send(f"❌ **Connection Refused**: {', '.join(errors)}")


async def process_received_items_message(msg: dict, channel):
    """Process ReceivedItems message type"""
    items = msg.get("items", [])
    for item in items:
        item_name = item.get("item", "Unknown Item")
        player_name = item.get("player", "Unknown Player")
        await channel.send(f"📦 **{player_name}** received: {item_name}")


async def process_location_info_message(msg: dict, channel):
    """Process LocationInfo message type"""
    locations = msg.get("locations", [])
    for location in locations:
        location_name = location.get("location", "Unknown Location")
        player_name = location.get("player", "Unknown Player")
        await channel.send(f"📍 **{player_name}** checked: {location_name}")


async def process_item_send_message(data: list, channel, player_progress: dict, output_directory: str,
                                  ap_dir: str, lookup_player_name_func, lookup_player_game_func,
                                  lookup_item_name_func, lookup_location_name_func, is_player_completed_func):
    """Process ItemSend message type within PrintJSON"""
    from helpers.data_helpers import load_apsave_data

    try:
        # Extract components from the data array
        sender_id = None
        recipient_id = None
        item_id = None
        item_flags = None
        location_id = None

        for item in data:
            if item.get("type") == "player_id":
                if sender_id is None:
                    sender_id = item.get("text")
                else:
                    recipient_id = item.get("text")
            elif item.get("type") == "item_id":
                item_id = item.get("text")
                item_flags = item.get("flags", 0)
            elif item.get("type") == "location_id":
                location_id = item.get("text")

        # Track location check for progress tracking
        if sender_id and location_id:
            sender_id_int = int(sender_id)
            location_id_int = int(location_id)

            # Initialize player progress if not exists
            if sender_id_int not in player_progress:
                player_progress[sender_id_int] = set()

            # Add this location to the player's checked locations
            player_progress[sender_id_int].add(location_id_int)
            print(f"Tracked location check: Player {sender_id_int} checked location {location_id_int}")

        # Only send messages for progression items (key items)
        # Check both item_flags == 1 and item_flags & 1 (bitwise check for progression flag)
        is_progression = (item_flags == 1) or (item_flags is not None and (item_flags & 1) != 0)

        if is_progression and sender_id and recipient_id and item_id and location_id:
            # Debug logging
            print(f"Processing key ItemSend: sender_id={sender_id}, recipient_id={recipient_id}, item_id={item_id}, item_flags={item_flags}, location_id={location_id}")

            # Look up actual names using the stored data
            sender_name = lookup_player_name_func(int(sender_id))
            recipient_name = lookup_player_name_func(int(recipient_id))

            # Skip if either player is the Rhelbot tracker
            if sender_name.lower() == "rhelbot" or recipient_name.lower() == "rhelbot":
                print(f"Skipping ItemSend involving Rhelbot tracker")
                return

            # Check if the recipient player has completed 100% of their locations
            recipient_id_int = int(recipient_id)

            # Only perform the completion check if we have save data loaded
            # Try to load save data if needed
            save_data = load_apsave_data(output_directory, ap_dir)
            if save_data and is_player_completed_func(recipient_id_int, save_data):
                print(f"Skipping ItemSend to player {recipient_name} who has completed 100% of locations")
                return

            # Get the recipient's game to look up item and location names
            recipient_game = lookup_player_game_func(int(recipient_id))
            sender_game = lookup_player_game_func(int(sender_id))

            # Use recipient's game for item lookup, sender's game for location lookup
            item_name = lookup_item_name_func(recipient_game, int(item_id))
            location_name = lookup_location_name_func(sender_game, int(location_id))

            # Key item emoji
            item_emoji = "🔑"

            message = f"{item_emoji} **{sender_name}** sent **{item_name}** to **{recipient_name}**\n📍 From: {location_name}"
            await channel.send(message)
        else:
            # Skip non-key items
            if item_flags is not None:
                print(f"Skipping non-key ItemSend (flags={item_flags}) from player {sender_id} to player {recipient_id}")

    except Exception as e:
        print(f"Error parsing ItemSend message: {e}")


async def process_game_event_message(msg_type: str, data: list, channel):
    """Process Goal, Release, Collect, Countdown message types"""
    text = "".join([item.get("text", "") for item in data])
    if text:
        await channel.send(f"🎯 {text}")


async def process_server_message(msg_type: str, data: list, channel):
    """Process Tutorial, ServerChat message types"""
    text = "".join([item.get("text", "") for item in data])
    if text:
        # Skip join/leave info messages with comprehensive filtering
        text_lower = text.lower()
        join_leave_keywords = [
            "has joined", "has left", "joined the game", "left the game",
            "tracking", "client(", "tags:", "connected", "disconnected",
            "now tracking", "no longer tracking", "syncing", "sync complete",
            "slot data", "connecting", "connection established", "room join",
            "player slot", "team #"
        ]

        if any(keyword in text_lower for keyword in join_leave_keywords):
            print(f"Skipping join/leave message: {text}")
            return

        await channel.send(f"ℹ️ {text}")


async def process_filtered_message(data: list, channel):
    """Process other message types with filtering"""
    text = "".join([item.get("text", "") for item in data])
    if text:
        # Skip messages that appear to be player-related or join/leave messages
        text_lower = text.lower()
        filter_keywords = [
            "player", "sent", "received", "found", "checked",
            "has joined", "has left", "joined the game", "left the game",
            "tracking", "client(", "connected", "disconnected",
            "now tracking", "no longer tracking", "syncing", "sync complete",
            "slot data", "connecting", "connection established", "room join",
            "player slot", "team #", "tags:", "collecting", "collected"
        ]

        if any(keyword in text_lower for keyword in filter_keywords):
            print(f"Skipping filtered message: {text}")
            return

        await channel.send(f"ℹ️ {text}")


async def process_room_update_message(msg: dict, channel):
    """Process RoomUpdate message type"""
    if "players" in msg:
        players = msg["players"]
        online_players = [p["alias"] for p in players if p.get("status", 0) > 0]
        if online_players:
            await channel.send(f"👥 **Online players**: {', '.join(online_players)}")


async def process_room_info_message(msg: dict, channel):
    """Process RoomInfo message type"""
    room_info = []
    if "seed_name" in msg:
        room_info.append(f"**Seed**: {msg['seed_name']}")
    if "players" in msg:
        player_count = len(msg["players"])
        room_info.append(f"**Players**: {player_count}")
    if room_info:
        await channel.send(f"🏠 **Room Info**\n" + "\n".join(room_info))


async def process_data_package_message(msg: dict, channel, game_data: dict):
    """Process DataPackage message type"""
    print(f"Received DataPackage: {msg}")
    games = msg.get("data", {}).get("games", {})
    if games:
        # Store the game data for lookups
        game_data.clear()
        game_data.update(games)
        print(f"Stored game data for {len(games)} games: {list(games.keys())}")

        # Debug: Show what data we have for each game
        for game_name, game_info in games.items():
            item_count = len(game_info.get("item_name_to_id", {}))
            location_count = len(game_info.get("location_name_to_id", {}))
            print(f"Game '{game_name}': {item_count} items, {location_count} locations")

        game_list = list(games.keys())
        await channel.send(f"🎲 **Available games**: {', '.join(game_list[:10])}" +
                        ("..." if len(game_list) > 10 else ""))
    else:
        print("DataPackage received but no games data found")


async def process_unknown_message(cmd: str, msg: dict, channel):
    """Process unknown message types"""
    if cmd and cmd not in ["Bounced"]:  # Bounced messages are just echoes, ignore them
        await channel.send(f"📨 **{cmd}**: {str(msg)[:200]}{'...' if len(str(msg)) > 200 else ''}")