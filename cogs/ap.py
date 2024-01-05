import discord
from discord import app_commands
from discord.ext import commands
import os
import subprocess

from win32process import CREATE_NEW_CONSOLE

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

    @app_commands.command(name="start")
    async def ap_start(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Starting Archipelago server", ephemeral=True)
        initialMessage = await interaction.original_response()

        apdirectory = "C:/Users/Administrator/Documents/Archipelago"

        os.system(f'python ./Archipelago/Generate.py')
        outputfile = os.listdir('./Archipelago/output')
        os.rename(f'{apdirectory}/output/{outputfile[0]}', f'{apdirectory}/output/donkey.zip')

        subprocess.Popen([r'serverstart.bat'])

        await initialMessage.edit(content="Archipelago server started.\nServer: ap.rhelys.com")
        # Todo - Generate multiworld file as well

    @app_commands.command(name="cleanup", description="Deletes the output and player files from the last game")
    async def ap_cleanup(self, interaction: discord.Interaction):
        outputPath = './Archipelago/output'
        outputfiles = os.listdir(outputPath)

        for file in outputfiles:
            os.remove(f'{outputPath}/{file}')

        await interaction.response.send_message("File cleanup complete", ephemeral=True)
        # Todo - Store player files somewhere with the date of the game and remove them for next generation

    # Todo - Serve spoiler file back to the channel (use zipfile)


async def setup(client) -> None:
    print(f"Entering AP cog setup\n")
    await client.add_cog(ApCog(client))
    await client.tree.sync(guild=donkeyServer)
