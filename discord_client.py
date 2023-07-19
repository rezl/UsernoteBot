import asyncio
import traceback
import typing

import discord
from discord import ui
from discord.ext import commands
from prawcore import NotFound

from settings import Settings
from usernote_utils import find_rules, find_ban

usernote_brief = "Used to create toolbox usernotes and removal comments"
usernote_description = f"{usernote_brief}\n" \
                       f"Workflow: \n" \
                       f"  1. Prompt bot, eg \".a\"\n" \
                       f"  2. The bot will respond with your recent comment or post removal action(s)\n" \
                       f"  3. Click the respective button to usernote or remove the embed\n" \
                       f"  4. Fill out the usernote prompt and submit\n" \
                       f"  5. Congrats! You've mobile usernoted!\n" \
                       f"\n" \
                       f"Mod Action Summary:\n" \
                       f"  * Mod Action Summary: url to the actioned content\n" \
                       f"  * Acting Mod: your name from the mod action\n" \
                       f"  * Action Type: mod action, removelink or removecomment\n" \
                       f"  * Target User: the user actioned by this mod action\n" \
                       f"  * URL: url to the content (to help mod identify content)\n" \
                       f"  * Post Title: if post\n" \
                       f"  * Comment Body: if comment\n" \
                       f"\n" \
                       f"Usernote Creation:\n" \
                       f"  * Target User: user to usernote\n" \
                       f"  * Type: the usernote type, included in the usernote (see below)\n" \
                       f"  * Rule# (integer): rule broken (must match the rule in the subreddit, " \
                       f"i.e. \"1\" would indicate R1 has been broken." \
                       f" This is included in both the usernote and removal reason\n" \
                       f"  * Detail (optional): more detail on the usernote. NOT included in the removal comment\n" \
                       f"  * Add Removal Comment (optional, y/n): " \
                       f"if yes, bot will also add a removal reason indicating the rule broken\n" \
                       f"\n" \
                       f"Note:\n" \
                       f"  * All reddit actions are by the bot (usernotes, comments)\n" \
                       f"  * Your discord server or display name must match your Reddit username\n" \
                       f"  * The supported usernote types are (using any of the [] will map to the left-most word)"
# f": {mapdata}"


def get_username(guild, user):
    member = guild.get_member(user.id)
    if member is None:
        return "User not in server"
    if hasattr(member, "nick") and member.nick:
        return member.nick
    elif hasattr(member, "display_name") and member.display_name:
        return member.display_name
    return "Unknown Name"


class DiscordClient(commands.Bot):
    def __init__(self, error_guild_name, error_guild_channel):
        super().__init__('.', intents=discord.Intents.all())
        self.error_guild_name = error_guild_name
        self.error_channel_name = error_guild_channel
        self.error_guild = None
        self.error_channel = None
        self.is_ready = False
        self.guild_reddit_map = dict()

    async def on_ready(self):
        print(f'{self.user} has connected to Discord!')
        self.error_guild = discord.utils.get(self.guilds, name=self.error_guild_name)
        self.error_channel = discord.utils.get(self.error_guild.channels, name=self.error_channel_name)
        self.is_ready = True
        guilds_msg = "\n".join([f"\t{guild.name}" for guild in self.guilds])
        startup_message = f"{self.user} is in the following guilds:\n" \
                          f"{guilds_msg}"
        print(startup_message)
        await self.error_channel.send(f"I am online for Usernotes script, is_dry_run={Settings.is_dry_run}")

    def send_error_msg(self, message):
        full_message = f"Usernotes script has had an exception. This can normally be ignored, " \
                       f"but if it's occurring frequently, may indicate a script error.\n{message}"
        if self.error_channel:
            asyncio.run_coroutine_threadsafe(self.error_channel.send(full_message), self.loop)

    def add_commands(self):
        @self.command(name="ping", description="lol")
        async def ping(ctx):
            prefix = "DRY RUN" if Settings.is_dry_run else "DO REAL SHIT"
            dry_run = f"I'm currently running in {prefix} mode"
            await ctx.channel.send(dry_run)

        @self.command(name="set_dry_run", brief="Set whether bot can make permanent reddit actions (0/1)",
                      description="Change whether this bot can make reddit actions (usernotes, comments). "
                                  "When in dry_run, the bot will not make usernotes or reddit comments, "
                                  "however the full workflow otherwise is available on discord\n"
                                  "Include: \n"
                                  "  * 0 (not in dry run, makes actions)\n"
                                  "  * 1 (dry run, no reddit actions)",
                      usage=".set_dry_run 1")
        async def set_dry_run(ctx, dry_run: typing.Literal[0, 1] = 1):
            Settings.is_dry_run = dry_run
            if Settings.is_dry_run:
                await ctx.channel.send(f"I am now running in dry run mode")
            else:
                await ctx.channel.send(f"I am now NOT running in dry run mode")

        @self.command(aliases=["q", "qn", "query"],
                      description="Queries usernotes", brief="Queries usernotes", usage=".q")
        async def query_usernotes(ctx, username: typing.Optional[str] = ""):
            try:
                if username == "":
                    await ctx.send("Include a username with this command, such as '.q fuckspez'")
                    return

                # should be called from within a supported server, by someone with necessary role
                guild = ctx.guild
                if not guild:
                    await ctx.send("Cannot use in DM - please request in a supported discord server")
                    return
                if guild not in self.guild_reddit_map:
                    await ctx.send("Cannot use - I don't know this discord server - contact developers")
                    return
                member = guild.get_member(ctx.author.id)
                if member is None:
                    await ctx.send("Cannot identify you")
                    return
                if not any(role.name in ["Moderator", "Comment Moderator"] for role in member.roles):
                    await ctx.send("Cannot action you without necessary role")
                    return

                reddit_actions_handler = self.guild_reddit_map[guild]
                subreddit = reddit_actions_handler.subreddit
                mod = get_username(guild, ctx.author)
                print(f"Received query request: {mod} {str(username)} "
                      f"from guild [{str(guild)}], channel [{str(ctx.channel)}]")
                subreddit_mod = subreddit.moderator(mod)
                if not subreddit_mod:
                    user_response = f"I cannot find a moderator with name: {mod}. " \
                                    f"Please change your discord or server name, or contact developers"
                    print(user_response)
                    await ctx.send(user_response)
                    return
                try:
                    reddit_actions_handler.reddit.redditor(username).id
                except NotFound:
                    user_response = f"I cannot find a user with name: {username}. Please match their username exactly."
                    print(user_response)
                    await ctx.send(user_response)
                    return

                try:
                    notes = reddit_actions_handler.toolbox.usernotes.list_notes(username, reverse=True)
                except KeyError:
                    # raised if user isn't in toolbox usernotes
                    embed = discord.Embed(title=f"Usernote History for {username}",
                                          url=f"https://reddit.com/u/{username}", color=0xFF5733)
                    embed.add_field(name="Number of Usernotes", value="No Usernotes")
                    return

                embed = discord.Embed(title=f"Usernote History for {username}",
                                      url=f"https://reddit.com/u/{username}", color=0xFF5733)
                embed.add_field(name="Number of Usernotes", value=len(notes), inline=True)
                for note in notes:
                    embed.add_field(name=note.human_time, value=note.note, inline=True)
                await ctx.send(embed=embed)
            except Exception as ex:
                error_msg = f"Exception in main processing: {ex}\n```{traceback.format_exc()}```"
                self.send_error_msg(error_msg)
                print(error_msg)


        @self.command(aliases=["u", "un", "a", "act", "action", "r"],
                      description=usernote_description, brief=usernote_brief, usage=".a")
        async def usernote(ctx, num_retrieved_mod_removals: typing.Optional[int] = 1):
            try:
                # usernote should be called from within a supported server, by someone with necessary role
                guild = ctx.guild
                if not guild:
                    await ctx.send("Cannot use in DM - please request in a supported discord server")
                    return
                if guild not in self.guild_reddit_map:
                    await ctx.send("Cannot use - I don't know this discord server - contact developers")
                    return
                member = guild.get_member(ctx.author.id)
                if member is None:
                    await ctx.send("Cannot identify you")
                    return
                if not any(role.name in ["Moderator", "Comment Moderator"] for role in member.roles):
                    await ctx.send("Cannot action you without necessary role")
                    return

                reddit_actions_handler = self.guild_reddit_map[guild]
                subreddit = reddit_actions_handler.subreddit
                mod = get_username(guild, ctx.author)
                print(f"Received action request: {mod} {str(num_retrieved_mod_removals)} "
                      f"from guild [{str(guild)}], channel [{str(ctx.channel)}]")
                subreddit_mod = subreddit.moderator(mod)
                if not subreddit_mod:
                    user_response = f"I cannot find a moderator with name: {mod}. " \
                                    f"Please change your discord or server name, or contact developers"
                    print(user_response)
                    await ctx.send(user_response)
                    return
                # all and access permissions allows to ban
                can_ban = any(x in ["all", "access"] for x in subreddit_mod[0].mod_permissions)
                actions_count = 0
                for mod_action in subreddit.mod.log(mod=mod):
                    if mod_action.action in ["removecomment", "removelink"]:
                        actions_count += 1
                        is_comment = True if mod_action.action == "removecomment" else False
                        embed = discord.Embed(title="Mod Action Summary",
                                              url=f"https://reddit.com{mod_action.target_permalink}", color=0xFF5733)
                        embed.add_field(name="Acting Mod", value=mod_action.mod, inline=True)
                        embed.add_field(name="Action Type",
                                        value="Removed Comment" if is_comment else "Removed Post", inline=True)
                        embed.add_field(name="Target User", value=mod_action.target_author, inline=True)
                        embed.add_field(name="URL", value=mod_action.target_permalink, inline=False)
                        if is_comment:
                            comment_truc = (mod_action.target_body[:300] + '...') \
                                if len(mod_action.target_body) > 300 else mod_action.target_body
                            embed.add_field(name="Comment Body", value=comment_truc, inline=False)
                        else:
                            embed.add_field(name="Post Title", value=mod_action.target_title, inline=False)
                        embed.set_footer(text=f"I will monitor this message for 5 minutes. Requested by {mod}")
                        content_id = mod_action.target_fullname
                        if is_comment:
                            content = reddit_actions_handler.reddit.comment(content_id)
                        else:
                            content = reddit_actions_handler.reddit.submission(content_id)
                        await ctx.send(embed=embed, view=MyView(guild, reddit_actions_handler,
                                                                is_comment, can_ban, content))
                    if actions_count >= num_retrieved_mod_removals:
                        break
                # no actions found for a mod, so that probably means provided mod name doesn't exist
                if actions_count < num_retrieved_mod_removals:
                    user_response = f"I found no actions for {mod}. " \
                                    f"Please change your discord or server name, or contact developers"
                    print(user_response)
                    await ctx.send(user_response)
            except Exception as ex:
                error_msg = f"Exception in main processing: {ex}\n```{traceback.format_exc()}```"
                self.send_error_msg(error_msg)
                print(error_msg)

    def add_usernote_guild(self, guild_name, reddit_handler):
        print(f'Adding discord usernote guild {guild_name} for {reddit_handler.subreddit.display_name}')
        guild = discord.utils.get(self.guilds, name=guild_name)
        if not guild:
            print(f'ERROR: cannot find guild {guild_name} for {reddit_handler.subreddit.display_name}')
        self.guild_reddit_map[guild] = reddit_handler


class MyView(discord.ui.View):

    def __init__(self, guild, reddit_actions_handler, is_comment, can_ban, content):
        super().__init__(timeout=300)
        self.reddit_actions_handler = reddit_actions_handler
        self.guild = guild
        self.is_comment = is_comment
        self.can_ban = can_ban
        self.content = content

    @discord.ui.button(label="Usernote the above action", style=discord.ButtonStyle.green)
    async def usernote(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = interaction.message.embeds[0].fields
        action_mod = self.get_field(fields, "Acting Mod")
        user = get_username(self.guild, interaction.user)
        if user.lower() != action_mod.lower():
            message = f"Only {action_mod} should click Usernote button"
            print(message)
            await interaction.response.send_message(message, ephemeral=True)
            return

        target_user = self.get_field(fields, "Target User")
        url = self.get_field(fields, "URL")

        await interaction.response.send_modal(
            UsernoteModal(self.reddit_actions_handler, action_mod, url, target_user,
                          self.is_comment, self.can_ban, self.content))
        await interaction.message.delete()

    @discord.ui.button(label="Remove this message", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[typing.Any],
                       /):
        error_formatted = traceback.format_exc()
        print(error_formatted)
        await interaction.response.send_message(
            f"There has been an error. Please raise to devs.\n{error_formatted}")

    @staticmethod
    def get_field(fields, target_field):
        for field in fields:
            if field.name == target_field:
                return field.value
        return None


class UsernoteModal(ui.Modal, title="Usernote Creation"):
    default_detail = "none (change to include anything in note beyond rule#)"

    def __init__(self, reddit_actions_handler, mod, url, target_user, is_comment, can_ban, content):
        super().__init__(timeout=300)  # seconds
        self.reddit_actions_handler = reddit_actions_handler
        self.url = "https://www.reddit.com" + url
        self.mod = mod
        self.target_user = target_user
        self.is_comment = is_comment
        self.can_ban = can_ban
        self.content = content

        # self.target_user = ui.TextInput(label="Target User", default=target_user_default)
        self.note_type = ui.TextInput(label="Type (warning, low quality, ban, spam)", default="Warning")
        self.rule = ui.TextInput(label="Rule# (1=1st rule, 2=2nd, multiple comma-sep)", required=False, default="1")
        self.detail = ui.TextInput(label="Usernote Detail", style=discord.TextStyle.paragraph, required=False,
                                   default=UsernoteModal.default_detail)
        self.should_comment = ui.TextInput(label="Add Removal Comment? (yes, y, no, n)", required=False, default="No")
        self.ban_type = ui.TextInput(label="Add ban? (no, number, p=perm, i=incremental)", required=False, default="No")

        # self.add_item(item=self.target_user)
        # self.add_item(item=self.note_type)
        self.add_item(item=self.rule)
        self.add_item(item=self.detail)
        self.add_item(item=self.should_comment)
        if can_ban:
            self.add_item(item=self.ban_type)

    async def on_submit(self, interaction: discord.Interaction):
        # note_type_input = self.note_type.value.lower()
        # note_type = None
        # for key, value in note_type_translation.items():
        #     if note_type_input in value:
        #         note_type = key
        # if note_type is None:
        #     message = f"\"{self.note_type}\" is not an allowed usernote type.\n" \
        #               f"Redo the commands and use one of these (any listed entry maps to respective type): {mapdata}"
        #     print(message)
        #     await interaction.response.send_message(message, ephemeral=True)
        #     return
        # if not target_user:
        #     message = f"Target user must be included. \"{target_user}\" is not a valid user. Redo commands"
        #     print(message)
        #     await interaction.response.send_message(message, ephemeral=True)
        #     return

        rule_input = self.rule.value
        detail = self.detail.value
        affirmative_responses = ["yes", "y", "ok", "sure", "yeah", "yea", "true", "t", "1"]
        should_comment = self.should_comment.value.lower() in affirmative_responses

        cited_rules = find_rules(rule_input)

        target_user_redditor = self.reddit_actions_handler.reddit.redditor(self.target_user)
        ban_type = find_ban(self.reddit_actions_handler.discord_client, self.reddit_actions_handler.subreddit,
                            target_user_redditor, self.ban_type.value.lower())

        rules_str = ("R" + ",".join(str(x) for x in cited_rules)) if len(cited_rules) > 0 else "No cited rules"
        full_note = f'[{self.mod}] {rules_str}' + ("" if UsernoteModal.default_detail == detail else ": " + detail)

        removal_message = f" and removal comment" if should_comment else ""
        ban_message = f" and banned [{ban_type}]" if ban_type else ""
        message = f"Creating usernote{removal_message}{ban_message}! {self.target_user}: {full_note}"

        if Settings.is_dry_run:
            message = f"No action taken - bot is in dry run mode. But I would have done this:\n{message}"
            print(message)
            await interaction.response.send_message(message, ephemeral=True)
            return

        await interaction.response.send_message(message, ephemeral=True)
        print(message)

        try:
            self.reddit_actions_handler.write_usernote(self.url, self.target_user, None, full_note)
            if should_comment:
                self.reddit_actions_handler.write_removal_reason(self.content, cited_rules)
            if ban_type:
                internal_detail = f"Usernotes command by {self.mod} for {full_note}"
                self.reddit_actions_handler.ban_user(self.target_user, rules_str, internal_detail, ban_type)
        except Exception as e:
            error_formatted = traceback.format_exc()
            print(error_formatted)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        error_formatted = traceback.format_exc()
        print(error_formatted)
        await interaction.response.send_message(
            f"There has been an error. Please raise to devs.\n{error_formatted}")
