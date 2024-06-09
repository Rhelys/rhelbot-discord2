import discord
from discord.ext import commands
import logging
from typing import Optional
import os

# Setting up logs
rhelbot_logs = logging.getLogger("discord")
rhelbot_logs.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename="log-rhelbot.log", encoding="utf-8", mode="w")
handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
)
rhelbot_logs.addHandler(handler)

waltzServer = discord.Object(id=266039174333726725)
donkeyServer = discord.Object(id=591625815528177690)

intents = discord.Intents.default()
intents.message_content = True
rhelbot = commands.Bot(command_prefix="!rhel", intents=intents)


@rhelbot.tree.command(
    description="Reloads/updates bot commands without having to restart the entire bot process"
)
async def update(interaction: discord.Interaction):
    await interaction.response.send_message("Starting update process", ephemeral=True)
    print(f"Entering update function\n")
    for f in os.listdir("./cogs"):
        if f.endswith(".py"):
            await rhelbot.reload_extension("cogs." + f[:-3])
            await interaction.channel.send(f"Reloaded {f[:-3]} Cog")
    for guild in rhelbot.guilds:
        await rhelbot.tree.sync(guild=guild)
    await rhelbot.tree.sync()
    await interaction.edit_original_response(content="Update completed")


@rhelbot.tree.command(
    description="Checks to see the status of a cog and loads it if not yet loaded"
)
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


@rhelbot.tree.command(description="Kitty pls", guild=donkeyServer)
async def kitty(interaction: discord.Interaction):
    await interaction.response.send_message("<@&902282275360763965>")


@rhelbot.tree.command(description="Right to jail", guild=donkeyServer)
@commands.has_any_role("Rhelbot")
async def theon(interaction: discord.Interaction):
    guild = interaction.guild
    member = guild.get_member(381620359230652421)
    gulag = rhelbot.get_channel(922023425042681896)
    try:
        await member.move_to(gulag)
        await interaction.response.send_message("Bye Theon")
    except:
        await interaction.response.send_message("Theon isn't here right now")
        return


@rhelbot.tree.command(description="Let the malding cease", guild=donkeyServer)
@commands.has_any_role("i want a pretty color")
async def cal(interaction: discord.Interaction):
    guild = interaction.guild
    member = guild.get_member(187413059315302401)
    try:
        await member.edit(mute=True)
        await interaction.response.send_message(
            "Shhhh... Its quiet now :)", ephemeral=True
        )
    except:
        await interaction.response.send_message(
            "Cal isn't here right now, it should already be quiet", ephemeral=True
        )
        return


@rhelbot.tree.command(description="No", guild=donkeyServer)
async def rhelys(interaction: discord.Interaction):
    guild = interaction.guild
    member = interaction.user
    gulag = rhelbot.get_channel(922023425042681896)
    try:
        await member.move_to(gulag)
        await interaction.response.send_message("No, fuck you. You go to gulag")
    except:
        await interaction.response.send_message("Nice try, but no")
        return


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
