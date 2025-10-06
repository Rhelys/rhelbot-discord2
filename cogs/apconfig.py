import discord
from discord import app_commands
from discord.ext import commands
import os
import json
from datetime import datetime

# Import helper functions
from helpers.s3_helpers import (
    upload_to_s3,
    download_from_s3,
    delete_from_s3,
    load_cache,
    save_cache,
    refresh_user_cache
)
from helpers.data_helpers import parse_yaml_metadata

donkeyServer = discord.Object(id=591625815528177690)

@app_commands.guilds(donkeyServer)
class ApConfigCog(commands.GroupCog, group_name="apconfig"):
    """Archipelago Player Configuration Management with S3 Storage"""

    # Class constants
    S3_BUCKET = "rhelbot-archipelago"
    CACHE_FILE = "./player_files_cache.json"
    TEMP_DIR = "./temp_uploads/"

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

        # Ensure temp directory exists
        os.makedirs(self.TEMP_DIR, exist_ok=True)

        # Load cache
        self.cache = load_cache(self.CACHE_FILE)

    @app_commands.command(name="upload", description="Upload a player YAML configuration file")
    @app_commands.describe(
        playerfile="The YAML player configuration file",
        description="Optional description for this configuration (e.g., 'Hard mode run', 'Randomizer settings v2')"
    )
    async def upload(
        self,
        interaction: discord.Interaction,
        playerfile: discord.Attachment,
        description: str = ""
    ) -> None:
        """Upload a player YAML file to S3 storage"""
        await interaction.response.defer()

        # Validate file extension
        if not playerfile.filename.endswith(".yaml"):
            await interaction.followup.send(
                "File must be a .yaml file. Please upload a valid Archipelago player configuration."
            )
            return

        await interaction.followup.send("Processing and uploading file...")

        # Save file temporarily
        temp_filepath = os.path.join(self.TEMP_DIR, playerfile.filename)
        await playerfile.save(temp_filepath)

        try:
            # Extract metadata from YAML
            player_name, game_name = parse_yaml_metadata(temp_filepath)

            if not player_name:
                await interaction.followup.send(
                    "Could not extract player name from YAML. Ensure the file has a 'name' field."
                )
                os.remove(temp_filepath)
                return

            # Generate S3 key with human-readable format
            discord_user_id = str(interaction.user.id)
            now = datetime.now()
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            upload_date = now.strftime("%Y-%m-%d")

            # Sanitize game name for filename (remove special characters)
            safe_game_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in (game_name or "Unknown"))
            safe_game_name = safe_game_name.replace(' ', '_')

            # Filename: playername_gamename_timestamp.yaml (timestamp ensures uniqueness)
            s3_key = f"{discord_user_id}/{player_name}_{safe_game_name}_{timestamp}.yaml"

            # Prepare metadata
            metadata = {
                "player_name": player_name or "Unknown",
                "game": game_name or "Unknown",
                "discord_user": str(interaction.user.id),
                "uploaded_by": interaction.user.name,
                "upload_date": upload_date,
                "description": description or ""
            }

            # Upload to S3
            success = upload_to_s3(temp_filepath, self.S3_BUCKET, s3_key, metadata)

            if success:
                # Refresh cache for this user
                refresh_user_cache(self.cache, self.CACHE_FILE, self.S3_BUCKET, discord_user_id)

                await interaction.followup.send(
                    f"✅ Successfully uploaded configuration for **{player_name}** ({game_name or 'Unknown game'})"
                )
            else:
                await interaction.followup.send(
                    "❌ Failed to upload file to S3. Please try again or contact an admin."
                )

            # Clean up temp file
            os.remove(temp_filepath)

        except Exception as e:
            await interaction.followup.send(f"Error processing file: {str(e)}")
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)

    @app_commands.command(name="list", description="List your uploaded player configurations")
    async def list_configs(self, interaction: discord.Interaction) -> None:
        """List all player configurations for the current user"""
        await interaction.response.defer()

        discord_user_id = str(interaction.user.id)

        # Refresh cache from S3
        user_files = refresh_user_cache(self.cache, self.CACHE_FILE, self.S3_BUCKET, discord_user_id)

        if not user_files:
            await interaction.followup.send("You have no uploaded player configurations.")
            return

        # Build list message
        embed = discord.Embed(
            title=f"Player Configurations for {interaction.user.name}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )

        for idx, file_info in enumerate(user_files, start=1):
            player_name = file_info.get("player_name", "Unknown")
            game = file_info.get("game", "Unknown")
            upload_date = file_info.get("upload_date", "Unknown")
            description = file_info.get("description", "")

            # Build value with optional description
            value_parts = [f"**Game:** {game}", f"**Uploaded:** {upload_date}"]
            if description:
                value_parts.append(f"**Description:** {description}")

            embed.add_field(
                name=f"#{idx}: {player_name}",
                value="\n".join(value_parts),
                inline=False
            )

        embed.set_footer(text=f"Total: {len(user_files)} configuration(s)")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="get", description="Download one of your player configurations")
    @app_commands.describe(number="The configuration number from /apconfig list")
    async def get_config(self, interaction: discord.Interaction, number: int) -> None:
        """Download a specific player configuration file"""
        await interaction.response.defer()

        discord_user_id = str(interaction.user.id)

        # Get user's files from cache
        user_files = self.cache.get(discord_user_id, [])

        if not user_files:
            await interaction.followup.send(
                "You have no uploaded configurations. Use `/apconfig list` to refresh."
            )
            return

        if number < 1 or number > len(user_files):
            await interaction.followup.send(
                f"Invalid number. Please choose between 1 and {len(user_files)}."
            )
            return

        # Get the selected file
        selected_file = user_files[number - 1]
        s3_key = selected_file["s3_key"]
        player_name = selected_file.get("player_name", "config")

        # Download from S3
        temp_download_path = os.path.join(self.TEMP_DIR, f"{player_name}_{number}.yaml")
        success = download_from_s3(self.S3_BUCKET, s3_key, temp_download_path)

        if success and os.path.exists(temp_download_path):
            # Send file to Discord
            await interaction.followup.send(
                f"📥 Here's your configuration for **{player_name}**:",
                file=discord.File(temp_download_path, filename=f"{player_name}.yaml")
            )

            # Clean up
            os.remove(temp_download_path)
        else:
            await interaction.followup.send(
                "❌ Failed to download file from S3. It may have been deleted."
            )

    @app_commands.command(name="delete", description="Delete one of your player configurations")
    @app_commands.describe(number="The configuration number from /apconfig list")
    async def delete_config(self, interaction: discord.Interaction, number: int) -> None:
        """Delete a specific player configuration file"""
        await interaction.response.defer()

        discord_user_id = str(interaction.user.id)

        # Get user's files from cache
        user_files = self.cache.get(discord_user_id, [])

        if not user_files:
            await interaction.followup.send(
                "You have no uploaded configurations. Use `/apconfig list` to refresh."
            )
            return

        if number < 1 or number > len(user_files):
            await interaction.followup.send(
                f"Invalid number. Please choose between 1 and {len(user_files)}."
            )
            return

        # Get the selected file
        selected_file = user_files[number - 1]
        s3_key = selected_file["s3_key"]
        player_name = selected_file.get("player_name", "Unknown")

        # Delete from S3
        success = delete_from_s3(self.S3_BUCKET, s3_key)

        if success:
            # Refresh cache
            refresh_user_cache(self.cache, self.CACHE_FILE, self.S3_BUCKET, discord_user_id)

            await interaction.followup.send(
                f"🗑️ Successfully deleted configuration for **{player_name}**"
            )
        else:
            await interaction.followup.send(
                "❌ Failed to delete file from S3. Please try again or contact an admin."
            )

    @app_commands.command(name="joinwith", description="Join a game using one of your stored configurations")
    @app_commands.describe(number="The configuration number from /apconfig list")
    async def joinwith(self, interaction: discord.Interaction, number: int) -> None:
        """Join an Archipelago game using a stored configuration file"""
        await interaction.response.defer()

        discord_user_id = str(interaction.user.id)

        # Get user's files from cache
        user_files = self.cache.get(discord_user_id, [])

        if not user_files:
            await interaction.followup.send(
                "You have no uploaded configurations. Use `/apconfig list` to refresh, or `/apconfig upload` to add one."
            )
            return

        if number < 1 or number > len(user_files):
            await interaction.followup.send(
                f"Invalid number. Please choose between 1 and {len(user_files)}."
            )
            return

        # Get the selected file
        selected_file = user_files[number - 1]
        s3_key = selected_file["s3_key"]
        player_name = selected_file.get("player_name", "Unknown")
        game_name = selected_file.get("game", "Unknown")

        # Download from S3 to the Archipelago players directory
        players_dir = "./Archipelago/players/"
        os.makedirs(players_dir, exist_ok=True)

        # Use a clean filename for the local copy
        local_filename = f"{player_name}_{game_name}.yaml"
        filepath = os.path.join(players_dir, local_filename)

        success = download_from_s3(self.S3_BUCKET, s3_key, filepath)

        if not success or not os.path.exists(filepath):
            await interaction.followup.send(
                "❌ Failed to download file from S3. It may have been deleted."
            )
            return

        try:
            # Load or create game status
            status_file = "game_status.json"
            if os.path.exists(status_file):
                try:
                    with open(status_file, 'r') as f:
                        game_status = json.load(f)
                except (json.JSONDecodeError, IOError):
                    game_status = {"players": {}, "discord_users": {}}
            else:
                game_status = {"players": {}, "discord_users": {}}

            # Ensure required keys exist
            if "players" not in game_status:
                game_status["players"] = {}
            if "discord_users" not in game_status:
                game_status["discord_users"] = {}

            # Check if player already exists
            if player_name in game_status["players"]:
                existing_discord_user = None
                # Find which Discord user owns this player
                for discord_id, players in game_status["discord_users"].items():
                    if isinstance(players, list):
                        if player_name in players:
                            existing_discord_user = discord_id
                            break
                    elif players == player_name:
                        existing_discord_user = discord_id
                        break

                # If the same Discord user is updating their file, allow it
                if existing_discord_user == str(interaction.user.id):
                    game_status["players"][player_name] = {
                        "filepath": filepath,
                        "game": game_name,
                        "joined_at": game_status["players"][player_name].get("joined_at", datetime.now().isoformat()),
                        "updated_at": datetime.now().isoformat()
                    }

                    # Save updated game status
                    with open(status_file, 'w') as f:
                        json.dump(game_status, f, indent=2)

                    await interaction.followup.send(
                        f"✅ Updated configuration for **{player_name}** ({game_name})"
                    )
                else:
                    await interaction.followup.send(
                        f"❌ {player_name} already exists and belongs to another user. "
                        "Choose a different player name in your YAML file."
                    )
                    os.remove(filepath)
                    return
            else:
                # New player - add to game status
                game_status["players"][player_name] = {
                    "filepath": filepath,
                    "game": game_name,
                    "joined_at": datetime.now().isoformat()
                }

                # Record Discord user to player mapping
                user_id_str = str(interaction.user.id)
                if user_id_str not in game_status["discord_users"]:
                    game_status["discord_users"][user_id_str] = []

                # Add the new player to the user's list of players if not already there
                if isinstance(game_status["discord_users"][user_id_str], list):
                    if player_name not in game_status["discord_users"][user_id_str]:
                        game_status["discord_users"][user_id_str].append(player_name)
                else:
                    # Handle old format where it was a single string
                    game_status["discord_users"][user_id_str] = [player_name]

                # Save updated game status
                with open(status_file, 'w') as f:
                    json.dump(game_status, f, indent=2)

                # Send success message with file attachment
                with open(filepath, "rb") as submitted_file:
                    await interaction.followup.send(
                        content=f"✅ Player joined successfully\n**Player:** {player_name}\n**Game:** {game_name}",
                        file=discord.File(submitted_file, filename=f"{player_name}_{game_name}.yaml")
                    )

        except Exception as e:
            await interaction.followup.send(f"❌ Error processing file: {str(e)}")
            if os.path.exists(filepath):
                os.remove(filepath)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApConfigCog(bot))
