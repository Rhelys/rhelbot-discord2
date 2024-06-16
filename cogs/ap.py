import discord
from discord import app_commands
from discord.ext import commands
from os import remove, listdir, rename, path
import subprocess
from asyncio import sleep
import zipfile
from typing import Optional
from ruyaml import YAML

donkeyServer = discord.Object(id=591625815528177690)


@app_commands.guilds(donkeyServer)
class ApCog(commands.GroupCog, group_name="ap"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()  # this is now required in this context.

    # Config section - To be updated if anything moves around, or it's going to be run by someone else
    output_directory = "./Archipelago/output/"
    ap_directory = "./Archipelago/"
    system_extensions = [".archipelago", ".txt", ".apsave"]
    status_file = "./game_status.txt"
    player = ""
    game = ""

    async def upload_success(self, filepath: str, interaction: discord.Interaction):
        with open(filepath, "rb") as submitted_file:
            await interaction.channel.send(
                content="Player joined successfully\nPlayer: {}\n"
                "Game: {}".format(self.player, self.game),
                file=discord.File(
                    submitted_file, filename=f"{self.player}_{self.game}.yaml"
                ),
            )

    def list_players(self):
        current_players = {}

        # Pulling the list of current players in the game for reference/comparison
        with open(self.status_file) as status:
            for line in status:
                name, file = line.rstrip("\n").split(":")
                current_players[name.capitalize()] = file

        return current_players

    @app_commands.command(
        name="addgame",
        description="Adds a new .apworld file to the server for future games",
    )
    @app_commands.describe(
        apworld="Valid .apworld for a new game that the server doesn't support"
    )
    async def ap_add_game(
        self, interaction: discord.Interaction, apworld: discord.Attachment
    ):
        await interaction.response.defer()

        if not apworld.filename.endswith(".apworld"):
            await interaction.followup.send(
                "Not a .apworld file. Make sure you've selected the right file for upload"
            )
            return

        await apworld.save(f"{self.ap_directory}/worlds/{apworld.filename}")
        await interaction.followup.send(
            "File added. If your game requires a patch file to be generated then work with the server owner to ensure "
            "your game will generate successfully"
        )

    """
    /ap join -  Takes in a yaml file from a discord member, checks to make sure it's a valid file and that we're not
                overwriting an existing valid file for a player, and responds back to the channel saying that the file
                has been uploaded
                
    Parameters: discord.File object as required from the command
                
    Function workflow:
                1: Validate that the file is a .yaml file, otherwise the generator won't accept it and will error out
                    1 - Exit: If the file is not a .yaml file, return a message to the user
                2: Save the submitted .yaml file 
                3: Read the players text file for currently used slot names
                4: Read the .yaml file and compare the slot name against the player list
                    4 - Exit: If the name matches, send an error back and recommend /ap leave to open the slot name
                              and delete the file initially saved
                5: Add the slot name to the player list and return a success to the user 
    """

    @app_commands.command(
        name="join",
        description="Adds your yaml config file to the pending Archipelago game",
    )
    @app_commands.describe(
        playerfile="Your .yaml file. Must be for a unique player slot or an edit on your existing file"
    )
    async def ap_join(
        self, interaction: discord.Interaction, playerfile: discord.Attachment
    ) -> None:
        await interaction.response.defer()

        try:
            current_players = self.list_players()
        except AttributeError:
            print("No players yet")

        if playerfile.filename.endswith(".yaml"):
            await interaction.followup.send("Processing file")

            filepath = f"./Archipelago/players/{playerfile.filename}"
            await playerfile.save(filepath)

            # Pulling out the player name and their game from the submitted yaml file
            # Todo - Add the game each person is playing to the status file
            with open(filepath, "r", encoding="utf-8") as playeryaml:
                yaml_object = YAML(typ="safe", pure=True)
                raw_data = yaml_object.load_all(playeryaml)
                data_list = list(raw_data)

                # Setting the metadata for the rest of the comparisons from the submitted file
                for element in data_list:
                    self.player = element.get("name")
                    self.game = element.get("game")

            if self.player in current_players:
                if filepath == current_players[self.player]:
                    await self.upload_success(filepath, interaction)
                else:
                    # Todo - setup the secondary edit https://stackoverflow.com/questions/75009840/how-i-can-edit
                    #  -followup-message-discord-py
                    await interaction.channel.send(
                        f"{self.player} already exists in another file. "
                        "Remove the second file before submitting again"
                    )
                    remove(filepath)

            else:
                await self.upload_success(filepath, interaction)
                with open("game_status.txt", "a+") as status_file:
                    capital_player = self.player.capitalize()
                    status_file.write(f"{capital_player}:{filepath}\n")

        else:
            await interaction.followup.send(
                f"File supplied is not a yaml file. Check that you uploaded the "
                f"correct file and try again"
            )

    """
    /ap start - Generates the game files from uploaded player files and then starts the server. Optionally
                takes in a pre-generated file to start up.
                
    Parameters: [Optional] apfile: Generated .zip file from Archipelago to start with the server
    
    """

    # Todo - Find out how to connect the bot to the server + channel for status messages
    # https://github.com/LegendaryLinux/ArchipelaBot
    @app_commands.command(
        name="start",
        description="Starts the game. Either generates or takes an optional "
        "pre-generated game file.",
    )
    @app_commands.describe(apfile="Pre-generated zip file for an Archipelago game")
    async def ap_start(
        self, interaction: discord.Interaction, apfile: Optional[discord.Attachment]
    ) -> None:
        await interaction.response.send_message(
            "Attempting to start Archipelago server. This will take 2 minutes"
        )

        # Clean up existing files - this is a port from the update command later in the file
        def outputfiles():
            return listdir(self.output_directory)

        for file in outputfiles():
            remove(f"{self.output_directory}/{file}")

        # Todo - add error handling for submitted file to make sure it includes a .archipelago file
        if apfile:
            if apfile.filename.endswith(".zip"):
                await apfile.save(f"{self.output_directory}/donkey.zip")
            else:
                await interaction.edit_original_response(
                    content="File submitted was not a .zip file. Submit a valid "
                    "generated Archipelago game file"
                )
                return
        else:
            # Todo - Fix the generation async issue so asyncio.sleep isn't necessary
            subprocess.Popen("python" + " ./Archipelago/Generate.py")
            await sleep(
                120
            )  # This is both duct tape and a bad idea, but it works for now

        outputfile = listdir(self.output_directory)

        # Todo - Refactor this to not be an absolute reference to the first object, just in case
        rename(
            f"{self.output_directory}{outputfile[0]}",
            f"{self.output_directory}/donkey.zip",
        )
        subprocess.Popen([r"serverstart.bat"])
        await sleep(8)

        await interaction.edit_original_response(
            content="Archipelago server started.\nServer: ap.rhelys.com\nPort: 38281\nPassword: 1440"
        )

        with zipfile.ZipFile(
            f"{self.output_directory}/donkey.zip", mode="r"
        ) as gamefile:
            for file in gamefile.namelist():
                if not file.endswith(tuple(self.system_extensions)):
                    gamefile.extract(file, f"{self.output_directory}")
        gamefile.close()

        finaloutputlist = listdir(self.output_directory)

        for dirfile in finaloutputlist:
            if dirfile == "donkey.zip":
                print("Skipping output zip")
            elif not dirfile.endswith(tuple(self.system_extensions)) and path.isfile(
                f"{self.output_directory}/{dirfile}"
            ):
                with open(f"{self.output_directory}/{dirfile}", "rb") as f:
                    await interaction.channel.send(
                        file=discord.File(f, filename=dirfile)
                    )

    """
    /ap cleanup  - 
    
    
     
    """

    # Todo - add in a "safe" cleanup option to remove everything but what's running
    # Todo - Disabling this for now and putting it into the startup function for ease of use.
    # I'll come back to this once I have a need to run multiple servers at once
    """
    @app_commands.command(
        name="cleanup",
        description="Cleans up the output and player files from the last game",
    )
    async def ap_cleanup(self, interaction: discord.Interaction):
        def outputfiles():
            return listdir(self.output_directory)

        for file in outputfiles():
            remove(f"{self.output_directory}/{file}")

        await interaction.response.send_message("File cleanup complete", ephemeral=True)
        # Todo - Store player files somewhere with the date of the game and remove them for next generation
    """

    @app_commands.command(
        name="spoiler", description="Pulls the spoiler log from the current game"
    )
    async def ap_spoiler(self, interaction: discord.Interaction):
        await interaction.response.defer()

        def outputfiles():
            return listdir(self.output_directory)

        for file in outputfiles():
            if file.endswith(".zip"):
                with zipfile.ZipFile(
                    f"{self.output_directory}/donkey.zip", mode="r"
                ) as gamefile:
                    for zipped_file in gamefile.namelist():
                        if zipped_file.endswith("Spoiler.txt"):
                            gamefile.extract(zipped_file, self.output_directory)
                            await sleep(3)
                            await interaction.followup.send(
                                "Spoiler file for current game"
                            )

        for endfile in outputfiles():
            if endfile.endswith("Spoiler.txt"):
                with open(f"{self.output_directory}/{endfile}", "rb") as sendfile:
                    await interaction.channel.send(
                        file=discord.File(sendfile, filename="Spoiler.txt")
                    )

    @app_commands.command(
        name="status",
        description="Gets the status and players of the current or pending game",
    )
    async def ap_status(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            current_players = self.list_players()
        except AttributeError:
            await interaction.followup.send("No current players in the game")
            return

        if not current_players:
            await interaction.followup.send("No current players in the game")
            return

        # Todo - Add the game each person is playing
        playerlist = list(current_players.keys())

        await interaction.followup.send(
            f"Current players: {playerlist}\n" f"Game status: Unknown"
        )
        # Todo - add in server status

    @app_commands.command(
        name="leave",
        description="Deletes player's file from the staged files. "
        " Returns list of current players without a selection.",
    )
    @app_commands.describe(player="Removes yaml file for selected player from the game")
    async def ap_leave(self, interaction: discord.Interaction, player: str):
        file_lines = []
        player_dict = self.list_players()

        if player.lower() == "all":
            for player_line in player_dict:
                remove(player_dict[player_line])

            remove(self.status_file)

            with open(self.status_file, "x") as file:
                await interaction.response.send_message(
                    f"All player files have been deleted"
                )

        else:
            try:
                remove(player_dict[player.capitalize()])
            except KeyError:
                await interaction.response.send_message(
                    f"{player.capitalize()} is not in the game. Check current players with /ap status"
                )
                return

            with open(self.status_file, "r") as player_list:
                file_lines = player_list.readlines()

            with open(self.status_file, "w") as player_list:
                for line in file_lines:
                    if not line.startswith(player.capitalize()):
                        player_list.write(line)

            await interaction.response.send_message(
                f"{player.capitalize()}'s file has been deleted"
            )

    @app_commands.command(
        name="help", description="Basic Archipelago setup information and game lists"
    )
    async def ap_help(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "# __Archipelago Setup Resources__\n"
            "* Main site: https://archipelago.gg/\n"
            "* Beta site: http://archipelago.gg:24242/\n"
            "* Setup guides: https://archipelago.gg/tutorial/\n"
            "* Alpha games: https://canary.discord.com/channels/731205301247803413/1009608126321922180\n"
            "* Archipelago Discord: https://discord.gg/8Z65BR2"
        )


async def setup(bot) -> None:
    print(f"Entering AP cog setup\n")
    await bot.add_cog(ApCog(bot=bot))
    print("AP cog setup complete\n")
