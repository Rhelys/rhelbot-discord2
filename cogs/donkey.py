import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

donkeyServer = discord.Object(id=591625815528177690)


@app_commands.guilds(donkeyServer)
class DonkeyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(description="Kitty pls")
    @app_commands.guilds(donkeyServer)
    async def kitty(self, interaction: discord.Interaction):
        await interaction.response.send_message("<@&902282275360763965>")

    @app_commands.command(description="Right to jail")
    @commands.has_any_role("Rhelbot")
    @app_commands.guilds(donkeyServer)
    async def theon(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(381620359230652421)
        gulag = interaction.guild.get_channel(922023425042681896)
        try:
            await member.move_to(gulag)
            await interaction.response.send_message("Bye Theon")
        except:
            await interaction.response.send_message("Theon isn't here right now")
            return

    @app_commands.command(description="Let the malding cease")
    @commands.has_any_role("i want a pretty color")
    @app_commands.guilds(donkeyServer)
    async def cal(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(187413059315302401)
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

    @app_commands.command(description="No")
    @app_commands.guilds(donkeyServer)
    async def rhelys(self, interaction: discord.Interaction):
        member = interaction.user
        gulag = interaction.guild.get_channel(922023425042681896)
        try:
            await member.move_to(gulag)
            await interaction.response.send_message("No, fuck you. You go to gulag")
        except:
            await interaction.response.send_message("Nice try, but no")
            return


async def setup(bot) -> None:
    print(f"Entering Donkey cog setup\n")
    await bot.add_cog(DonkeyCog(bot=bot))
    print("Donkey cog setup complete\n")
