import random
import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import Optional
import os

# Setting up logs
rhelbot_logs = logging.getLogger("discord")
rhelbot_logs.setLevel(logging.INFO)
handler = logging.FileHandler(filename="rhelbot.log", encoding="utf-8", mode="w")
handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
)
rhelbot_logs.addHandler(handler)


waltzServer = discord.Object(id=266039174333726725)
donkeyServer = discord.Object(id=591625815528177690)


intents = discord.Intents.default()
intents.message_content = True
rhelbot = commands.Bot(command_prefix='!rhel',intents=intents)


@rhelbot.tree.command()
async def update(interaction: discord.Interaction):
    print(f"Entering update function\n")
    await rhelbot.tree.sync()
    for f in os.listdir("./cogs"):
        if f.endswith(".py"):
           await rhelbot.load_extension("cogs." + f[:-3])


@rhelbot.tree.command()
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

