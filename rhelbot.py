import random
import discord
from discord import app_commands
import logging
from typing import Optional

# Setting up logs
rhelbot_logs = logging.getLogger("discord")
rhelbot_logs.setLevel(logging.INFO)
handler = logging.FileHandler(filename="rhelbot.log", encoding="utf-8", mode="w")
handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
)
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
    print(f"Rhelbot has connected to Discord!\n\n")
    print(f"{rhelbot.user} is connected to the following Discord servers:\n")
    for guild in rhelbot.guilds:
        print(f"{guild.name} (id: {guild.id})\n")


@rhelbot.tree.command(
    description="Displays the number of unique entries in the Starlight giveaway"
)
@app_commands.describe(messageid="Message ID of the contest post")
async def starlightcount(interaction: discord.Interaction, messageid: str):
    await interaction.response.defer()
    channel = rhelbot.get_channel(615421445635440660)
    message = await channel.fetch_message(int(messageid))
    contestants = set()
    reactionCount = 0

    for reaction in message.reactions:
        reactionCount += reaction.count

    for reaction in message.reactions:
        async for user in reaction.users():
            contestants.add(user)
    await interaction.followup.send(
        f"Across {reactionCount} reactions, {len(contestants)} unique users have entered the giveaway"
    )


@rhelbot.tree.command(
    description="Selects a winner from the entries in the Starlight giveaway"
)
@app_commands.checks.has_any_role("Waltz Leadership (Flare)", "Amplifier")
@app_commands.describe(messageid="Message ID of the contest post")
async def starlightwinner(interaction: discord.Interaction, messageid: str):
    await interaction.response.defer()
    channel = rhelbot.get_channel(615421445635440660)
    message = await channel.fetch_message(int(messageid))
    contestants = set()

    for reaction in message.reactions:
        async for user in reaction.users():
            contestants.add(user.mention)

    people = list(contestants)
    winner = random.choice(people)
    await interaction.followup.send(f"{winner} has been selected as the winner!")


@rhelbot.tree.command(description="Adds a donated item to the item list")
@app_commands.checks.has_any_role("Waltz Leadership (Flare)", "Amplifier")
@app_commands.describe(
    item="Item donated",
    member="Person who donated the item",
    quantity="(Optional) Number of items donated. Defaults to 1 if not provided",
)
async def donate(
    interaction: discord.Interaction, quantity: Optional[int], item: str, member: str
):
    await interaction.response.defer()

    quantity = quantity or 1

    itemfile = open("G:/My Drive/waltz_starlight_donations.txt", "a")
    itemfile.write(f"{item},{member},{quantity}")
    itemfile.write("\n")
    itemfile.close()

    await interaction.followup.send(
        f"Recorded {quantity} {item}(s) donated by {member}\n"
    )


# Starting the bot
bot_token_file = open("rhelbot_token.txt", "r")
bot_token = bot_token_file.read()
rhelbot.run(bot_token)
