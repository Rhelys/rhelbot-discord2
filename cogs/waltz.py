import discord
import random
from discord import app_commands
from discord.ext import commands
from typing import Optional

waltzServer = discord.Object(id=266039174333726725)


@app_commands.guilds(waltzServer)
class WaltzCog(commands.GroupCog, group_name="waltz"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        description="Displays the number of unique entries in the Starlight giveaway"
    )
    @app_commands.describe(messageid="Message ID of the contest post")
    async def contestcount(self, interaction: discord.Interaction, messageid: str):
        await interaction.response.defer()
        channel = self.get_channel(615421445635440660)
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

    @app_commands.command(
        description="Selects a winner from the entries in the contest post"
    )
    @app_commands.checks.has_any_role(
        "Waltz Leadership (Flare)", "Waltz Leadership (Amplifier)"
    )
    @app_commands.describe(messageid="Message ID of the contest post")
    async def contestwinner(self, interaction: discord.Interaction, messageid: str):
        await interaction.response.defer()
        channel = self.get_channel(615421445635440660)
        message = await channel.fetch_message(int(messageid))
        contestants = set()

        for reaction in message.reactions:
            async for user in reaction.users():
                contestants.add(user.mention)

        people = list(contestants)
        winner = random.choice(people)
        await interaction.followup.send(f"{winner} has been selected as the winner!")

    @app_commands.command(description="Adds a donated item to the item list")
    @app_commands.checks.has_any_role(
        "Waltz Leadership (Flare)",
        "Waltz Leadership (Amplifier)",
        "moonmoonmoonmoonmoon",
    )
    @app_commands.describe(
        item="Item donated",
        member="Person who donated the item",
        quantity="(Optional) Number of items donated. Defaults to 1 if not provided",
    )
    async def donate(
        self,
        interaction: discord.Interaction,
        quantity: Optional[int],
        item: str,
        member: str,
    ):
        await interaction.response.defer()

        quantity = quantity or 1

        itemfile = open("G:/My Drive/waltz_xmas_2023_donations.txt", "a")
        itemfile.write(f"{item},{member},{quantity}")
        itemfile.write("\n")
        itemfile.close()

        await interaction.followup.send(
            f"Recorded {quantity} {item}(s) donated by {member} for the Christmas giveaway\n"
        )


async def setup(client) -> None:
    print(f"Entering Waltz cog setup\n")
    await client.add_cog(WaltzCog(client))
    await client.tree.sync(guild=waltzServer)
    print("Waltz cog setup complete\n")
