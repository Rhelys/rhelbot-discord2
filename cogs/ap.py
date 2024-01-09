import discord
from discord import app_commands
from discord.ext import commands
import os
import subprocess
from asyncio import sleep
import zipfile
from typing import Optional

# import yaml
from ruyaml import YAML

donkeyServer = discord.Object(id=591625815528177690)

outputdirectory = "./Archipelago/output/"
apdirectory = "./Archipelago/"
system_extensions = [".archipelago", ".txt", ".zip", ".apsave"]


@app_commands.guilds(donkeyServer)
class ApCog(commands.GroupCog, group_name="ap"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()  # this is now required in this context.

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
        playerfile="Your .yaml file. Must be for a unique player slot"
    )
    async def ap_join(
        self, interaction: discord.Interaction, playerfile: discord.Attachment
    ) -> None:
        await interaction.response.defer()

        if playerfile.filename.endswith(".yaml"):
            await interaction.followup.send("Processing file")
            player = ""
            game = ""

            filepath = f"./Archipelago/players/{playerfile.filename}"
            await playerfile.save(filepath)

            with open(filepath, "r", encoding="utf-8") as playeryaml:
                yaml_object = YAML(typ="safe", pure=True)
                raw_data = yaml_object.load_all(playeryaml)
                data_list = list(raw_data)

                for element in data_list:
                    player = element.get("name")
                    game = element.get("game")

            with open(filepath, "rb") as submitted_file:
                await interaction.channel.send(
                    content="Player joined successfully\nPlayer: {}\n"
                    "Game: {}".format(player, game),
                    file=discord.File(submitted_file, filename=f"{player}_{game}.yaml"),
                )

        else:
            await interaction.followup.send(
                f"File supplied is not a yaml file. Check that you uploaded the "
                f"correct file and try again"
            )
        # Todo - add in error handling if filename already exists
        # Todo - add in error handling if the slot name already exists (ruyaml)

    """
    /ap start - Starts the 
    
    """

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
            "Attempting to start Archipelago server. This will " "take 2 minutes",
            ephemeral=True,
        )

        # Todo - add error handling for submitted file to make sure it's a zip file and includes a .archipelago file
        if apfile:
            if apfile.filename.endswith(".zip"):
                await apfile.save(f"{outputdirectory}donkey.zip")
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

        outputfile = os.listdir(outputdirectory)

        # Todo - Refactor this to not be an absolute reference to the first object, just in case
        os.rename(f"{outputdirectory}{outputfile[0]}", f"{outputdirectory}donkey.zip")
        subprocess.Popen([r"serverstart.bat"])
        await sleep(8)

        await interaction.channel.send(
            "Archipelago server started.\nServer: ap.rhelys.com\nPort: 38281"
        )

        with zipfile.ZipFile(f"{outputdirectory}donkey.zip", mode="r") as gamefile:
            for file in gamefile.namelist():
                if not file.endswith(tuple(system_extensions)):
                    gamefile.extract(file, f"{outputdirectory}")
        gamefile.close()

        finaloutputlist = os.listdir(outputdirectory)

        for dirfile in finaloutputlist:
            if not dirfile.endswith(tuple(system_extensions)) and os.path.isfile(
                f"{outputdirectory}/{dirfile}"
            ):
                with open(f"{outputdirectory}{dirfile}", "rb") as f:
                    await interaction.channel.send(
                        file=discord.File(f, filename=dirfile)
                    )

    """
    /ap cleanup  - 
    
    
     
    """

    # Todo - add in a "safe" cleanup option to remove everything but what's running
    @app_commands.command(
        name="cleanup",
        description="Cleans up the output and player files from the last game",
    )
    async def ap_cleanup(self, interaction: discord.Interaction):
        outputfiles = os.listdir(outputdirectory)

        for file in outputfiles:
            os.remove(f"{outputdirectory}{file}")

        await interaction.response.send_message("File cleanup complete", ephemeral=True)
        # Todo - Store player files somewhere with the date of the game and remove them for next generation

    @app_commands.command(
        name="spoiler", description="Pulls the spoiler log from the current game"
    )
    async def ap_spoiler(self, interaction: discord.Interaction):
        await interaction.response.defer()

        outputfiles = os.listdir(outputdirectory)

        for file in outputfiles:
            if file.endswith(".zip"):
                with zipfile.ZipFile(
                    f"{outputdirectory}donkey.zip", mode="r"
                ) as gamefile:
                    for zipped_file in gamefile.namelist():
                        if zipped_file.endswith("Spoiler.txt"):
                            gamefile.extract(zipped_file, outputdirectory)
                            await sleep(3)
                            await interaction.followup.send(
                                "Spoiler file for current game"
                            )
                            outputfiles2 = os.listdir(outputdirectory)
                            for endfile in outputfiles2:
                                if endfile.endswith("Spoiler.txt"):
                                    with open(
                                        f"{outputdirectory}{endfile}", "rb"
                                    ) as sendfile:
                                        await interaction.channel.send(
                                            file=discord.File(
                                                sendfile, filename="Spoiler.txt"
                                            )
                                        )

                gamefile.close()

    # Todo - game/server/file status command
    @app_commands.command(
        name="status",
        description="Gets the status and players of the current or pending game",
    )
    async def ap_status(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Todo - add in player count
        # Todo - add in player list
        # Todo - add in server status

        await interaction.followup.send("Status function")

    # Todo - Remove single player from game (/ap leave)
    @app_commands.command(
        name="leave",
        description="Deletes player's file from the staged files. "
        " Returns list of current players without a selection.",
    )
    @app_commands.describe(player="Player/slot name to clear the file of")
    async def ap_leave(self, interaction: discord.Interaction, player: Optional[str]):
        if player:
            return
        else:
            return


async def setup(client) -> None:
    print(f"Entering AP cog setup\n")
    await client.add_cog(ApCog(client))
    await client.tree.sync(guild=donkeyServer)
