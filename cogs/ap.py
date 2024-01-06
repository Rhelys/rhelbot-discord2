import discord
from discord import app_commands, HTTPException
from discord.ext import commands
import os
import subprocess
from time import sleep
import zipfile
from typing import Optional

donkeyServer = discord.Object(id=591625815528177690)


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
        # Todo - add in player count/validation/tracking (use pyyaml)
        # Todo - add in error handling if filename already exists

    @app_commands.command(name="start", description="Starts the game. Either generates or takes an optional "
                                                    "pre-generated game file.")
    @app_commands.describe(file='Pre-generated zip file for an Archipelago game')
    async def ap_start(self, interaction: discord.Interaction, file: Optional[discord.Attachment]) -> None:
        await interaction.response.defer()
        initialMessage = await interaction.followup.send("Starting Archipelago server. If this message doesn't change "
                                                         "within a minute, it means there was an error")

        apdirectory = "C:/Users/Administrator/Documents/Archipelago/"
        outputdirectory = "C:/Users/Administrator/Documents/Archipelago/output/"

        # Todo - Add handling for the optional game zip if no generation is needed
        if file:
            await file.save("./Archipelago/output/donkey.zip")
        else:
            os.system(f'python ./Archipelago/Generate.py')

        outputfile = os.listdir(outputdirectory)

        # Todo - Refactor this to not be an absolute reference to the first object, just in case
        os.rename(f'{outputdirectory}{outputfile[0]}', f'{outputdirectory}donkey.zip')
        subprocess.Popen([r'serverstart.bat'])
        sleep(5)

        await initialMessage.edit(content="Archipelago server started.\nServer: ap.rhelys.com")

        # Todo - Send patch files back from the zip file
        extensions = [".archipelago", ".txt", ".zip", ".apsave"]

        with zipfile.ZipFile(f'{outputdirectory}donkey.zip', mode='r') as gamefile:
            for file in gamefile.namelist():
                if not file.endswith(tuple(extensions)):
                    gamefile.extract(file, f'{outputdirectory}')

        response_files: list[discord.File] = []

        # Todo - debug file locking issue when cleaning up
        finaloutputlist = os.listdir(outputdirectory)
        for file in finaloutputlist:
            if not file.endswith(tuple(extensions)) and os.path.isfile(f'{outputdirectory}/{file}'):
                try:
                    f = open(f'{outputdirectory}{file}', 'rb')
                    response_files.append(discord.File(f, filename=file))
                finally:
                    f.close()
        try:
            await interaction.channel.send(files=response_files)
        except HTTPException:
            await interaction.channel.send("No additional patch files found")

        # Todo - Allow for a pre-generated file to be sent instead of generating

    @app_commands.command(name="cleanup", description="Deletes the output and player files from the last game")
    async def ap_cleanup(self, interaction: discord.Interaction):
        outputPath = './Archipelago/output'
        outputfiles = os.listdir(outputPath)

        for file in outputfiles:
            os.remove(f'{outputPath}/{file}')

        await interaction.response.send_message("File cleanup complete", ephemeral=True)
        # Todo - Store player files somewhere with the date of the game and remove them for next generation

    # Todo - New command to serve spoiler file back to the channel (use zipfile)


async def setup(client) -> None:
    print(f"Entering AP cog setup\n")
    await client.add_cog(ApCog(client))
    await client.tree.sync(guild=donkeyServer)
