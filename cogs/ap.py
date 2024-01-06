import discord
from discord import app_commands, HTTPException
from discord.ext import commands
import os
import subprocess
import asyncio
import zipfile
from typing import Optional

donkeyServer = discord.Object(id=591625815528177690)

outputdirectory = "./Archipelago/output/"
apdirectory = "./Archipelago/"
system_extensions = [".archipelago", ".txt", ".zip", ".apsave"]


@app_commands.guilds(donkeyServer)
class ApCog(commands.GroupCog, group_name="ap"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()  # this is now required in this context.

    @app_commands.command(name="join", description="Adds your yaml config file to the pending Archipelago game")
    async def ap_join(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        await file.save(f'./Archipelago/players/{file.filename}')
        await interaction.response.send_message(f"{file.filename} uploaded to the game")
        # Todo - add in filetype validation
        # Todo - add in error handling if filename already exists
        # Todo - add in error handling if the slot name already exists (pyyaml)

    @app_commands.command(name="start", description="Starts the game. Either generates or takes an optional "
                                                    "pre-generated game file.")
    @app_commands.describe(file='Pre-generated zip file for an Archipelago game')
    async def ap_start(self, interaction: discord.Interaction, file: Optional[discord.Attachment]) -> None:
        await interaction.response.send_message("Attempting to start Archipelago server. This will "
                                                "take 2 minutes", ephemeral=True)

        # Todo - add error handling for submitted file to make sure it's a zip file and includes a .archipelago file

        if file:
            await file.save(f'{outputdirectory}donkey.zip')
        else:
            # Todo - Fix the generation async issue so asyncio.sleep isn't necessary
            subprocess.Popen("python" + " ./Archipelago/Generate.py")
            await asyncio.sleep(100)  # This is both duct tape and a bad idea, but it works for now

        outputfile = os.listdir(outputdirectory)

        # Todo - Refactor this to not be an absolute reference to the first object, just in case
        os.rename(f'{outputdirectory}{outputfile[0]}', f'{outputdirectory}donkey.zip')
        subprocess.Popen([r'serverstart.bat'])
        await asyncio.sleep(8)

        await interaction.channel.send("Archipelago server started.\nServer: ap.rhelys.com")

        with zipfile.ZipFile(f'{outputdirectory}donkey.zip', mode='r') as gamefile:
            for file in gamefile.namelist():
                if not file.endswith(tuple(system_extensions)):
                    gamefile.extract(file, f'{outputdirectory}')
        gamefile.close()

        finaloutputlist = os.listdir(outputdirectory)

        for dirfile in finaloutputlist:
            if not dirfile.endswith(tuple(system_extensions)) and os.path.isfile(f'{outputdirectory}/{dirfile}'):
                with open(f'{outputdirectory}{dirfile}', 'rb') as f:
                    await interaction.channel.send(file=discord.File(f, filename=dirfile))

    @app_commands.command(name="cleanup", description="Cleans up the output and player files from the last game")
    async def ap_cleanup(self, interaction: discord.Interaction):
        outputfiles = os.listdir(outputdirectory)

        for file in outputfiles:
            os.remove(f'{outputdirectory}{file}')

        await interaction.response.send_message("File cleanup complete", ephemeral=True)
        # Todo - Store player files somewhere with the date of the game and remove them for next generation

    @app_commands.command(name="spoiler", description="Pulls the spoiler log from the current game")
    async def ap_spoiler(self, interaction: discord.Interaction):
        await interaction.response.defer()

        outputfiles = os.listdir(outputdirectory)

        for file in outputfiles:
            if file.endswith(".zip"):
                with zipfile.ZipFile(f'{outputdirectory}donkey.zip', mode='r') as gamefile:
                    for zipped_file in gamefile.namelist():
                        if zipped_file.endswith("Spoiler.txt"):
                            gamefile.extract(zipped_file, outputdirectory)
                            with open(f'{outputdirectory}{zipped_file}', 'rb') as spoiler:
                                await interaction.followup.send("Spoiler file for current game")
                                await interaction.channel.send(discord.File(spoiler, filename="Spoiler.txt"))
                gamefile.close()

    # Todo - game/server/file status command
    @app_commands.command(name="status", description="Gets the status and players of the current or pending game")
    async def ap_status(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Todo - add in player count
        # Todo - add in player list
        # Todo - add in server status

        await interaction.followup.send("Status function")


async def setup(client) -> None:
    print(f"Entering AP cog setup\n")
    await client.add_cog(ApCog(client))
    await client.tree.sync(guild=donkeyServer)
