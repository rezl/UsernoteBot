import traceback
import typing
from datetime import datetime, timedelta
from threading import Thread
import discord
import os

from discord import ui
from discord.ext import commands

import config
import time
import praw

import settings
from discord_client import DiscordClient
from usernote_handler import UsernoteHandler, note_type_translation, mapdata

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
                       f"  * The supported usernote types are (using any of the [] will map to the left-most word)" \
                       f": {mapdata}"


def get_username(guild, user):
    member = guild.get_member(user.id)
    if member is None:
        return "User not in collapse server"
    if hasattr(member, "nick") and member.nick:
        return member.nick
    elif hasattr(member, "display_name") and member.display_name:
        return member.display_name
    return "Unknown Name"


def run_forever():
    # get config from env vars if set, otherwise from config file
    client_id = os.environ.get("CLIENT_ID", config.CLIENT_ID)
    client_secret = os.environ.get("CLIENT_SECRET", config.CLIENT_SECRET)
    bot_username = os.environ.get("BOT_USERNAME", config.BOT_USERNAME)
    bot_password = os.environ.get("BOT_PASSWORD", config.BOT_PASSWORD)
    discord_token = os.environ.get("DISCORD_TOKEN", config.DISCORD_TOKEN)
    guild_error_name = os.environ.get("DISCORD_ERROR_GUILD", config.DISCORD_ERROR_GUILD)
    guild_error_channel = os.environ.get("DISCORD_ERROR_CHANNEL", config.DISCORD_ERROR_CHANNEL)
    guild_collapse_name = os.environ.get("DISCORD_COLLAPSE_GUILD", config.DISCORD_COLLAPSE_GUILD)
    subreddits = os.environ.get("SUBREDDITS", config.SUBREDDITS)
    print("CONFIG: subreddit_names=" + str(subreddits))

    # discord stuff
    client = DiscordClient(guild_error_name, guild_error_channel)

    Thread(target=client.run, args=(discord_token,)).start()

    # reddit + toolbox stuff
    reddit = praw.Reddit(
        client_id=client_id, client_secret=client_secret,
        user_agent="flyio:com.collapse.usernotebot",
        redirect_uri="http://localhost:8080",  # unused for script applications
        username=bot_username, password=bot_password,
        check_for_async=False
    )
    subreddit = reddit.subreddit(subreddits)
    usernote_handler = UsernoteHandler(reddit, subreddit)

    while not client.is_ready:
        time.sleep(1)

    collapse_guild = discord.utils.get(client.guilds, name=guild_collapse_name)

    mods_last_check = datetime.utcfromtimestamp(0)
    mods = None

    def get_cached_mods():
        nonlocal mods_last_check
        nonlocal mods
        if datetime.utcnow() - mods_last_check < timedelta(days=1):
            return mods

        mods = list()
        for moderator in subreddit.moderator():
            mods.append(moderator.name)
        mods_last_check = datetime.utcnow()
        mods = mods
        print(f"Refreshed mods: {mods}")
        return mods

    # anyone using the bot must be a mod in the collapse mod discord server
    # (even if using outside this server)
    def is_collapse_mod():
        async def predicate(ctx):
            member = collapse_guild.get_member(ctx.author.id)
            if member is None:
                return False
            for role in member.roles:
                if role.name in ["Moderator", "Comment Moderator"]:
                    return True
            return False

        return commands.check(predicate)

    @is_collapse_mod()
    @client.command(name="ping", description="lol")
    async def ping(ctx):
        mod = get_username(collapse_guild, ctx.author)
        dry_run = "Also, I'm currently running in Dry Run mode" if settings.Settings.is_dry_run else ""
        await ctx.channel.send(f"{mod} is the bestest mod! {dry_run}")

    @is_collapse_mod()
    @client.command(name="set_dry_run", brief="Set whether bot can make permanent reddit actions (0/1)",
                    description="Change whether this bot can make reddit actions (usernotes, comments). "
                                "When in dry_run, the bot will not make usernotes or reddit comments, "
                                "however the full workflow otherwise is available on discord\n"
                                "Include: \n"
                                "  * 0 (not in dry run, makes actions)\n"
                                "  * 1 (dry run, no reddit actions)",
                    usage=".set_dry_run 1")
    async def set_dry_run(ctx, dry_run: typing.Literal[0, 1] = 1):
        settings.Settings.is_dry_run = dry_run
        if settings.Settings.is_dry_run:
            await ctx.channel.send(f"I am now running in dry run mode")
        else:
            await ctx.channel.send(f"I am now NOT running in dry run mode")

    @is_collapse_mod()
    @client.command(aliases=["un", "a", "act", "action"], description=usernote_description, brief=usernote_brief,
                    usage=".a")
    async def usernote(ctx, num_retrieved_mod_removals: typing.Optional[int] = 1):
        try:
            mod = get_username(collapse_guild, ctx.author)
            print(f"Received action request: {mod} {str(num_retrieved_mod_removals)} "
                  f"from guild [{str(ctx.guild)}], channel [{str(ctx.channel)}]")
            actions = subreddit.mod.log(mod=mod)
            actions_count = 0
            for mod_action in actions:
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
                    await ctx.send(embed=embed, view=MyView(collapse_guild, usernote_handler, is_comment))
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
            client.send_error_msg(error_msg)
            print(error_msg)

    while True:
        for comment in subreddit.stream.comments():
            if comment.author not in get_cached_mods():
                continue
            try:
                handle_mod_response(comment, usernote_handler)
            except Exception as e:
                message = f"Exception in comment processing: {e}\n```{traceback.format_exc()}```"
                client.send_error_msg(message)
                print(message)
                usernote_handler.send_message(comment.author, "Error during removal request processing",
                                              f"I've encountered an error whilst actioning your removal request:  \n\n"
                                              f"URL: https://www.reddit.com{comment.permalink}  \n\n"
                                              f"Error: {e}\n\n"
                                              f"Please review your comment and the offending comment"
                                              f" to ensure they are removed. If your command is in the correct format, "
                                              f"e.g. \".r 1,2,3\", please raise this issue to the developers.")


def handle_mod_response(mod_comment, usernote_handler):
    split = mod_comment.body.split(" ")
    # early check to prevent querying for parent etc if not even a command
    if split[0] not in [".r", ".n"]:
        return
    rules = find_rules(split)
    rules_str = "R" + str(rules)
    print(f"Action request: {mod_comment.author.name} for {rules_str}: {mod_comment.permalink}")
    actionable_comment = mod_comment.parent()
    url = f"https://www.reddit.com{actionable_comment.permalink}"
    notes = usernote_handler.toolbox.usernotes.list_notes(actionable_comment.author.name, reverse=True)
    for note in notes:
        if note.url is None:
            continue
        # already usernoted: a usernote already contains the link to this content
        if actionable_comment.id in note.url:
            print(f"Ignoring as already actioned {actionable_comment.id}:"
                  f" {actionable_comment.author.name} for {rules_str}: {actionable_comment.permalink}")
            return

    if split[0] == ".r":
        print(f"Removing+Usernoting: {actionable_comment.author.name} for {rules_str}: {actionable_comment.permalink}")
        usernote_handler.write_removal_reason(url, rules, True)
        usernote_handler.write_usernote(url, actionable_comment.author.name, None, rules_str)
        usernote_handler.remove_comment("Mod removal request: mod", mod_comment)
        usernote_handler.remove_comment("Mod removal request: user", actionable_comment)
    elif split[0] == ".n":
        print(f"Usernoting: {actionable_comment.author.name} for {rules_str}: {actionable_comment.permalink}")
        usernote_handler.write_usernote(url, actionable_comment.author.name, None, rules_str)
        usernote_handler.remove_comment("Mod removal request: mod", mod_comment)
        usernote_handler.remove_comment("Mod removal request: user", actionable_comment)


def get_id(fullname):
    split = fullname.split("_")
    return split[1] if len(split) > 0 else split[0]


def find_rules(input):
    if input is None or len(input) < 2:
        return list()
    rules = input[1]
    for delim in [",", ".", ";"]:
        if delim in rules:
            return rules.split(delim)
    return rules


class MyView(discord.ui.View):

    def __init__(self, guild, usernote_handler, is_comment):
        super().__init__(timeout=300)
        self.usernote_handler = usernote_handler
        self.guild = guild
        self.is_comment = is_comment

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
            UsernoteModal(self.usernote_handler, action_mod, url, target_user, self.is_comment))
        await interaction.message.delete()

    @discord.ui.button(label="Remove this message", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[typing.Any], /):
        error_formatted = traceback.format_exc()
        print(error_formatted)
        await interaction.response.send_message(f"There has been an error. Please raise to devs.\n{error_formatted}")

    @staticmethod
    def get_field(fields, target_field):
        for field in fields:
            if field.name == target_field:
                return field.value
        return None


class UsernoteModal(ui.Modal, title="Usernote Creation"):
    default_detail = "none (change to include anything in note beyond rule#)"

    def __init__(self, usernote_handler, mod, url, target_user_default, is_comment):
        super().__init__(timeout=300)  # seconds
        self.reddit_handler = usernote_handler
        self.url = "https://www.reddit.com" + url
        self.mod = mod
        self.is_comment = is_comment

        self.target_user = ui.TextInput(label="Target User", default=target_user_default)
        self.note_type = ui.TextInput(label="Type (warning, low quality, ban, spam)", default="warning")
        self.rule = ui.TextInput(label="Rule# (1=respect, 3=relevant, 4=misinfo, etc)", default="1")
        self.detail = ui.TextInput(label="Detail", style=discord.TextStyle.paragraph, required=False,
                                   default=UsernoteModal.default_detail)
        self.should_comment = ui.TextInput(label="Add Removal Comment (yes, y, no, n)", required=False, default="no")

        self.add_item(item=self.target_user)
        self.add_item(item=self.note_type)
        self.add_item(item=self.rule)
        self.add_item(item=self.detail)
        self.add_item(item=self.should_comment)

    async def on_submit(self, interaction: discord.Interaction):
        rule = self.rule.value
        note_type_input = self.note_type.value.lower()
        detail = self.detail.value
        target_user = self.target_user.value
        should_comment = self.should_comment.value.lower() in ["yes", "y", "ok", "sure", "yeah", "yea", "true", "t"]

        note_type = None
        for key, value in note_type_translation.items():
            if note_type_input in value:
                note_type = key
        if note_type is None:
            message = f"\"{self.note_type}\" is not an allowed usernote type.\n" \
                      f"Redo the commands and use one of these (any listed entry maps to respective type): {mapdata}"
            print(message)
            await interaction.response.send_message(message, ephemeral=True)
            return

        if not rule.isnumeric():
            message = f"Rule number must be included a number. \"{rule}\" is not a number last I checked. Redo commands"
            print(message)
            await interaction.response.send_message(message, ephemeral=True)
            return

        if int(rule) < 0 or int(rule) > 12:
            message = f"Rule number must be a valid rule in r/collapse. Redo commands"
            print(message)
            await interaction.response.send_message(message, ephemeral=True)
            return

        if not target_user:
            message = f"Target user must be included. \"{target_user}\" is not a valid user. Redo commands"
            print(message)
            await interaction.response.send_message(message, ephemeral=True)
            return

        full_note = "R" + rule + ("" if UsernoteModal.default_detail == detail else ": " + detail)

        comment_addendum = " and removal comment" if should_comment else ""
        if settings.Settings.is_dry_run:
            message = f"No action taken - bot is in dry run mode. " \
                      f"But I would have created a usernote{comment_addendum}: {target_user}: {full_note}"
            print(message)
            await interaction.response.send_message(message, ephemeral=True)
            return

        message = f"Creating usernote{comment_addendum}! {target_user}: {full_note}"
        await interaction.response.send_message(message, ephemeral=True)
        print(message)

        try:
            self.reddit_handler.write_usernote(self.url, target_user, note_type, full_note)
            if should_comment:
                rules = list()
                rules.append(rule)
                self.reddit_handler.write_removal_reason(self.url, rules, self.is_comment)
        except Exception as e:
            error_formatted = traceback.format_exc()
            print(error_formatted)
            return

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        error_formatted = traceback.format_exc()
        print(error_formatted)
        await interaction.response.send_message(f"There has been an error. Please raise to devs.\n{error_formatted}")


if __name__ == "__main__":
    run_forever()
