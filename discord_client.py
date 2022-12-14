import asyncio
import discord
from discord.ext import commands

import settings


class DiscordClient(commands.Bot):
    def __init__(self, guild_name, bot_channel):
        super().__init__(".", intents=discord.Intents.all())
        self.guild_name = guild_name
        self.bot_channel = bot_channel
        self.guild = None
        self.channel = None
        self.is_ready = False

    async def on_ready(self):
        print(f"{self.user} has connected to Discord!")
        self.guild = discord.utils.get(self.guilds, name=self.guild_name)
        self.channel = discord.utils.get(self.guild.channels, name=self.bot_channel)
        self.is_ready = True
        await self.channel.send(f"I am online, is_dry_run={settings.Settings.is_dry_run}")

    def send_error_msg(self, message):
        full_message = f"UsernotesBot has had an exception. This can normally be ignored, " \
                       f"but if it's occurring frequently, may indicate a script error.\n{message}"
        if self.channel:
            asyncio.run_coroutine_threadsafe(self.channel.send(full_message), self.loop)
