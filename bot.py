import traceback
import typing
from datetime import datetime, timedelta
from threading import Thread
import os

import config
import time
import praw

import settings
from discord_client import DiscordClient
from reddit_actions_handler import RedditActionsHandler


def run_forever():
    # get config from env vars if set, otherwise from config file
    client_id = os.environ.get("CLIENT_ID", config.CLIENT_ID)
    client_secret = os.environ.get("CLIENT_SECRET", config.CLIENT_SECRET)
    bot_username = os.environ.get("BOT_USERNAME", config.BOT_USERNAME)
    bot_password = os.environ.get("BOT_PASSWORD", config.BOT_PASSWORD)
    discord_token = os.environ.get("DISCORD_TOKEN", config.DISCORD_TOKEN)
    guild_error_name = os.environ.get("DISCORD_ERROR_GUILD", config.DISCORD_ERROR_GUILD)
    guild_error_channel = os.environ.get("DISCORD_ERROR_CHANNEL", config.DISCORD_ERROR_CHANNEL)
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
    reddit_actions_handler = RedditActionsHandler(reddit, subreddit)

    while not client.is_ready:
        time.sleep(1)

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

    @client.command(name="ping", description="lol")
    async def ping(ctx):
        dry_run = "I'm currently running in Dry Run mode" if settings.Settings.is_dry_run else ""
        await ctx.channel.send(dry_run)

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

    while True:
        for comment in subreddit.stream.comments():
            # don't use is_mod (which is true if mod of ANY subs)
            if comment.author not in get_cached_mods():
                continue
            try:
                handle_mod_response(client, subreddit, comment, reddit_actions_handler)
            except Exception as e:
                message = f"Exception in comment processing: {e}\n```{traceback.format_exc()}```"
                client.send_error_msg(message)
                print(message)
                reddit_actions_handler.send_message(comment.author, "Error during removal request processing",
                                                    f"I've encountered an error whilst actioning your removal request:"
                                                    f"  \n\n"
                                                    f"URL: https://www.reddit.com{comment.permalink}  \n\n"
                                                    f"Error: {e}\n\n"
                                                    f"Please review your comment and the offending user to ensure all"
                                                    f" is as expected. If your command is in the correct format, "
                                                    f"e.g. \".r 1,2,3\", please raise this issue to the developers.")


def handle_mod_response(discord_client, subreddit, mod_comment, reddit_actions_handler):
    # input must be space separated
    remaining_commands = mod_comment.body.split(" ")
    command_type = remaining_commands[0]
    # early check to prevent querying for parent etc if not even a command
    if command_type not in [".r", ".n"]:
        return
    # remaining_command always exists, so remove it
    remaining_commands.remove(command_type)
    print(f"Action request: {mod_comment.author.name}: {mod_comment.permalink}")

    actionable_comment = mod_comment.parent()
    url = f"https://www.reddit.com{actionable_comment.permalink}"
    notes = reddit_actions_handler.toolbox.usernotes.list_notes(actionable_comment.author.name, reverse=True)

    for note in notes:
        if note.url is None:
            continue
        # already usernoted: a usernote already contains the link to this content
        if actionable_comment.id in note.url:
            print(f"Ignoring as already actioned {actionable_comment.id}:"
                  f" {actionable_comment.author.name}: {actionable_comment.permalink}")
            return

    rules_int = find_rules(remaining_commands)
    # if rules exist, remove it from the remaining commands
    if rules_int and remaining_commands:
        remaining_commands.remove(remaining_commands[0])

    ban_type = find_ban(discord_client, subreddit, actionable_comment.author, remaining_commands)
    # if ban command exists, remove it from the remaining commands
    if ban_type and remaining_commands:
        remaining_commands.remove(remaining_commands[0])

    message = find_message(remaining_commands)

    rules_str = ("R" + ",".join(str(x) for x in rules_int)) if len(rules_int) > 0 else "No cited rules"
    full_note = rules_str + (": " + message if message else "")
    if command_type == ".r":
        print(f"Removing+Usernoting: {actionable_comment.author.name} for {rules_str}: {actionable_comment.permalink}")
        reddit_actions_handler.write_removal_reason(url, rules_int, True)
        reddit_actions_handler.remove_comment("Mod removal request: user", actionable_comment)
        reddit_actions_handler.write_usernote(url, actionable_comment.author.name, None, full_note)
        reddit_actions_handler.remove_comment("Mod removal request: mod", mod_comment)
        internal_detail = f"Usernotes command by {mod_comment.author.name} for {full_note}"
        if ban_type:
            reddit_actions_handler.ban_user(actionable_comment.author.name, rules_str, internal_detail, ban_type)
        ban_message = ("Ban:" + (ban_type if ban_type.isnumeric() else "Perm" + " " + internal_detail)
                       if ban_type else "")
        reddit_actions_handler.send_message(mod_comment.author, "Bot Action Summary",
                                            f"I have performed the following:\n\n"
                                            f"URL: https://www.reddit.com{actionable_comment.permalink}  \n\n"
                                            f"Usernote detail: {full_note}\n\n"
                                            f"{ban_message}")
    elif command_type == ".n":
        print(f"Usernoting: {actionable_comment.author.name} for {rules_str}: {actionable_comment.permalink}")
        reddit_actions_handler.write_usernote(url, actionable_comment.author.name, None, full_note)
        reddit_actions_handler.remove_comment("Mod removal request: mod", mod_comment)
        reddit_actions_handler.send_message(mod_comment.author, "Bot Action Summary",
                                            f"I have performed the following:\n\n"
                                            f"URL: https://www.reddit.com{actionable_comment.permalink}  \n\n"
                                            f"Usernote detail: {full_note}\n\n")


def get_id(fullname):
    split = fullname.split("_")
    return split[1] if len(split) > 0 else split[0]


# attempts to find rule set from input
# if all input is a number, optionally delim sep, returns a list of these numbers
# if it cannot, returns empty list (ie no rules included)
def find_rules(command):
    if not command:
        return list()
    # if rules included, it is always the 1st of rule_input
    rules = command[0]
    # case if the input is only 1 rule
    if rules.isnumeric():
        rules_int = list()
        rules_int.append(int(rules))
        return rules_int
    for delim in [",", ".", ";"]:
        if delim in rules:
            rules_str = rules.split(delim)
            rules_int = list()
            for rule in rules_str:
                if rule.isnumeric():
                    rules_int.append(int(rule))
                else:
                    return list()
            return rules_int
    return list()


# attempts to find ban type from input: num, i, p
# if input is a ban request and matches the supported ban types, returns the type (number or perm)
# otherwise returns empty string (not ban)
def find_ban(discord_client, subreddit, user, command):
    # ban command always starts with "b"
    if not command or not command[0].startswith("b"):
        return ""
    ban_type = command[0][1:]
    if ban_type.isnumeric():
        return ban_type
    # incremental ban
    elif ban_type == "i":
        for log in subreddit.mod.notes.redditors(user):
            if log.action == "banuser" and len(log.details) > 0:
                try:
                    # hopefully ban detail is always in (# days) ...
                    banned_days = int(log.details.split(" ")[0])
                    return str(banned_days * 2)
                except Exception as e:
                    error_formatted = traceback.format_exc()
                    print(error_formatted)
                    discord_client.send_error_msg(f"Caught exception in finding user ban:\n{error_formatted} "
                                                  f"when processing {user}: {command} with {log} {log.details}")
                    return "3"
        # if no notes, default to 3 days
        return "3"
    elif ban_type == "p":
        return "perm"
    return ""


def find_message(command):
    if not command:
        return ""
    return " ".join(command)


if __name__ == "__main__":
    run_forever()
