import discord
from discord.ext import commands
import logging
from typing import Optional
import os

import cogs.ap

# Setting up logs
rhelbot_logs = logging.getLogger("discord")
rhelbot_logs.setLevel(logging.INFO)
handler = logging.FileHandler(filename="log-rhelbot.log", encoding="utf-8", mode="w")
handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
)
rhelbot_logs.addHandler(handler)

waltzServer = discord.Object(id=266039174333726725)
donkeyServer = discord.Object(id=591625815528177690)

intents = discord.Intents.default()
intents.message_content = True
rhelbot = commands.Bot(command_prefix='!rhel', intents=intents)


@rhelbot.tree.command()
async def update(interaction: discord.Interaction):
    await interaction.response.defer()
    print(f"Entering update function\n")
    for f in os.listdir("./cogs"):
        if f.endswith(".py"):
            await rhelbot.unload_extension("cogs." + f[:-3])
    for f in os.listdir("./cogs"):
        if f.endswith(".py"):
            await rhelbot.load_extension("cogs." + f[:-3])
    await rhelbot.tree.sync(interaction.guild)
    await rhelbot.tree.sync()
    await interaction.followup.send('Update completed')


@rhelbot.tree.command(description='Checks to see the status of a cog and loads it if not yet loaded')
async def check_cogs(interaction: discord.Interaction, cog_name: str):
    await interaction.response.defer()
    try:
        await rhelbot.load_extension(f"cogs.{cog_name}")
    except commands.ExtensionAlreadyLoaded:
        await interaction.followup.send("Cog is loaded", ephemeral=True)
    except commands.ExtensionNotFound:
        await interaction.followup.send("Cog not found", ephemeral=True)
    else:
        await interaction.followup.send("Cog is unloaded", ephemeral=True)
        await rhelbot.unload_extension(f"cogs.{cog_name}")


@rhelbot.event
async def setup_hook():
    print(f"Entering setup_hook\n")
    for f in os.listdir("./cogs"):
        if f.endswith(".py"):
            await rhelbot.load_extension("cogs." + f[:-3])
    await rhelbot.tree.sync()
    for guild in rhelbot.guilds:
        await rhelbot.tree.sync(guild=guild)


@rhelbot.event
async def on_ready():
    print(f"Rhelbot has connected to Discord!\n\n")
    print(f"{rhelbot.user} is connected to the following Discord servers:\n")
    for guild in rhelbot.guilds:
        print(f"{guild.name} (id: {guild.id})\n")


# Starting the bot
print(f"Entering main function")
bot_token_file = open("rhelbot_token.txt", "r")
bot_token = bot_token_file.read()
rhelbot.run(bot_token)
