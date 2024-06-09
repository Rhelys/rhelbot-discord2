import enum

import discord
import random
from discord import app_commands
from discord.ext import commands
from typing import Optional
from typing import Literal
from datetime import datetime, date
from time import strptime

waltzServer = discord.Object(id=266039174333726725)


@app_commands.guilds(waltzServer)
class WaltzCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def list_birthdays(self, filepath):
        current_birthdays = {}

        with open(filepath, "a") as current_month:
            for line in current_month:
                (
                    name,
                    day,
                ) = line.rstrip(
                    "\n"
                ).split(":")
                current_birthdays[name.capitalize()] = day

        return current_birthdays

    @app_commands.command(
        description="Displays the number of unique entries in the Starlight giveaway"
    )
    @app_commands.describe(messageid="Message ID of the contest post")
    async def contestcount(self, interaction: discord.Interaction, messageid: str):
        await interaction.response.defer()
        channel = self.bot.get_channel(615421445635440660)
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
        channel = self.bot.get_channel(615421445635440660)
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

    @app_commands.command(
        name="add_birthday", description="Adds your birthday to the Waltz calendar"
    )
    @app_commands.describe(
        character_first="Your character's first name",
        character_last="Your character's last name",
        month="Month name as a word",
        day="Day as a number",
    )
    async def add_birthday(
        self,
        interaction: discord.Interaction,
        character_first: str,
        character_last: str,
        month: Literal[
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        day: int,
    ):
        await interaction.response.defer()
        await interaction.followup.send("Checking the birthday list...", ephemeral=True)

        birthday_file = f"G:/My Drive/birthdays/{month.lower()}.txt"

        character = f"{character_first.capitalize()} {character_last.capitalize()}"

        # Validate the given date ahead of any computation
        year = datetime.now().year
        month_number = strptime(month, "%B").tm_mon
        try:
            date(year, month_number, day)
        except ValueError:
            interaction.followup.send(
                "Date submitted is invalid, try resubmitting", ephemeral=True
            )

        submitted_birthdays = self.list_birthdays(birthday_file)

        if character in submitted_birthdays:
            await interaction.followup.send(
                "This character is already in the list!", ephemeral=True
            )
        else:
            with open(birthday_file, "a+") as open_file:
                capital_player = character.capitalize()
                open_file.write(f"{capital_player}:{day}\n")
            await interaction.followup.send(
                "Birthday added to the list successfully!", ephemeral=True
            )


async def setup(client) -> None:
    print(f"Entering Waltz cog setup\n")
    await client.add_cog(WaltzCog(client))
    await client.tree.sync(guild=waltzServer)
    print("Waltz cog setup complete\n")
