import random

import discord
from discord import app_commands
import logging

# Setting up logs
rhelbot_logs = logging.getLogger('discord')
rhelbot_logs.setLevel(logging.INFO)
handler = logging.FileHandler(filename='rhelbot.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
rhelbot_logs.addHandler(handler)

intents = discord.Intents.default()
intents.message_content = True

waltzId = discord.Object(id=266039174333726725)


class RhelbotClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=waltzId)
        await self.tree.sync(guild=waltzId)


intents = discord.Intents.default()
intents.message_content = True
rhelbot = RhelbotClient(intents=intents)


@rhelbot.event
async def on_ready():
    print(f'Rhelbot has connected to Discord!\n\n')
    print(f'{rhelbot.user} is connected to the following Discord servers:\n')
    for guild in rhelbot.guilds:
        print(f'{guild.name} (id: {guild.id})\n')


@rhelbot.tree.command(description='Displays the number of unique entries in the Starlight giveaway')
async def starlightcount(interaction: discord.Interaction):
    await interaction.response.defer()
    channel = rhelbot.get_channel(952012406341648454)
    message = await channel.fetch_message(1045426377991659621)
    contestants = set()

    for reaction in message.reactions:
        async for user in reaction.users():
            contestants.add(user)
    await interaction.followup.send(f'{len(contestants)} have entered the giveaway')


@rhelbot.tree.command(description='Selects a winner from the entries in the Starlight giveaway')
@app_commands.checks.has_any_role('Waltz Leadership (Flare)', 'Amplifier')
async def starlightwinner(interaction: discord.Interaction):
    await interaction.response.defer()
    channel = rhelbot.get_channel(952012406341648454)
    message = await channel.fetch_message(1045426377991659621)
    contestants = set()

    for reaction in message.reactions:
        async for user in reaction.users():
            contestants.add(user)

    people = list(contestants)
    winner = random.choice(people)
    await interaction.followup.send(f'@{winner} has been selected as the winner!')


# Starting the bot
bot_token_file = open("rhelbot_token.txt", "r")
bot_token = bot_token_file.read()
rhelbot.run(bot_token)
