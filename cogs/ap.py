import discord
from discord import app_commands
from discord.ext import commands

donkeyServer = discord.Object(id=591625815528177690)


class ApCog(commands.GroupCog, name="ap"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()  # this is now required in this context.

    @app_commands.command(name="setup")
    async def my_sub_command_1(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello from sub command 1", ephemeral=True)

    @app_commands.command(name="status")
    async def my_sub_command_2(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello from sub command 2", ephemeral=True)


async def setup(client) -> None:
    print(f"Entering AP cog setup\n")
    await client.add_cog(ApCog(client), guild=donkeyServer)
