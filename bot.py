import traceback
from threading import Thread
import os

from praw.exceptions import RedditAPIException
from praw.reddit import Submission

import config
import time
import praw

from discord_client import DiscordClient
from reddit_actions_handler import RedditActionsHandler
from subreddit_tracker import SubredditTracker

max_retries = 5
retry_wait_time = 30


def run_forever():
    # get config from env vars if set, otherwise from config file
    client_id = os.environ.get("CLIENT_ID", config.CLIENT_ID)
    client_secret = os.environ.get("CLIENT_SECRET", config.CLIENT_SECRET)
    bot_username = os.environ.get("BOT_USERNAME", config.BOT_USERNAME)
    bot_password = os.environ.get("BOT_PASSWORD", config.BOT_PASSWORD)
    discord_token = os.environ.get("DISCORD_TOKEN", config.DISCORD_TOKEN)
    discord_error_guild_name = os.environ.get("DISCORD_ERROR_GUILD", config.DISCORD_ERROR_GUILD)
    discord_error_channel_name = os.environ.get("DISCORD_ERROR_CHANNEL", config.DISCORD_ERROR_CHANNEL)
    subreddits_config = os.environ.get("SUBREDDITS", config.SUBREDDITS)
    subreddit_names = [subreddit.strip() for subreddit in subreddits_config.split(",")]
    print("CONFIG: subreddit_names=" + str(subreddit_names))

    # discord stuff
    discord_client = DiscordClient(discord_error_guild_name, discord_error_channel_name)
    discord_client.add_commands()
    Thread(target=discord_client.run, args=(discord_token,)).start()
    while not discord_client.is_ready:
        time.sleep(1)

    try:
        for subreddit_name in subreddit_names:
            create_usernotes_thread(bot_password, bot_username, client_id, client_secret,
                                    discord_client, subreddit_name)
    except Exception as e:
        message = f"Exception in main processing: {e}\n```{traceback.format_exc()}```"
        discord_client.send_error_msg(message)
        print(message)


def create_usernotes_thread(bot_password, bot_username, client_id, client_secret,
                            discord_client, subreddit_name):
    print(f"Creating {subreddit_name} subreddit thread")

    # each thread needs its own read for thread safety
    reddit = praw.Reddit(
        client_id=client_id, client_secret=client_secret,
        user_agent=f"flyio:com.usernotebot.{subreddit_name}",
        redirect_uri="http://localhost:8080",  # unused for script applications
        username=bot_username, password=bot_password,
        check_for_async=False
    )
    subreddit = reddit.subreddit(subreddit_name)
    subreddit_tracker = SubredditTracker(subreddit, RedditActionsHandler(reddit, subreddit))
    Thread(target=handle_comment_stream, args=(discord_client, subreddit_tracker)).start()
    print(f"Created {subreddit_name} subreddit thread")


def handle_comment_stream(discord_client, subreddit_tracker):
    subreddit = subreddit_tracker.subreddit
    reddit_actions_handler = subreddit_tracker.reddit_actions_handler

    for comment in subreddit.stream.comments():
        if comment.author not in subreddit_tracker.get_cached_mods():
            continue
        for i in range(max_retries):
            try:
                handle_mod_response(discord_client, subreddit_tracker, comment)
            except RedditAPIException as e:
                message = f"API Exception in comment processing: {e}\n```{traceback.format_exc()}```\n\n" \
                          f"Retrying in {retry_wait_time} seconds..."
                discord_client.send_error_msg(message)
                print(message)
                if i == max_retries:
                    reddit_actions_handler.send_message(comment.author, "Error during removal request processing",
                                                        f"I've encountered an error whilst actioning your request:"
                                                        f"  \n\n"
                                                        f"URL: https://www.reddit.com{comment.permalink}  \n\n"
                                                        f"Error: {e}\n\n"
                                                        f"Please review to ensure all"
                                                        f" is as expected. If your command is in the correct format, "
                                                        f"e.g. \".r 1,2,3\", please raise this issue to the developers")
                else:
                    time.sleep(retry_wait_time)
            except Exception as e:
                message = f"Exception in comment processing: {e}\n```{traceback.format_exc()}```"
                discord_client.send_error_msg(message)
                print(message)
                reddit_actions_handler.send_message(comment.author, "Error during removal request processing",
                                                    f"I've encountered an error whilst actioning your request:"
                                                    f"  \n\n"
                                                    f"URL: https://www.reddit.com{comment.permalink}  \n\n"
                                                    f"Error: {e}\n\n"
                                                    f"Please review to ensure all"
                                                    f" is as expected. If your command is in the correct format, "
                                                    f"e.g. \".r 1,2,3\", please raise this issue to the developers")


def handle_mod_response(discord_client, subreddit_tracker, mod_comment):
    reddit_actions_handler = subreddit_tracker.reddit_actions_handler
    subreddit = subreddit_tracker.subreddit

    # input must be space separated
    remaining_commands = mod_comment.body.split(" ")
    command_type = remaining_commands[0]
    # early check to prevent querying for parent etc if not even a command
    if command_type not in [".r", ".n"]:
        return
    # remaining_command always exists, so remove it
    remaining_commands.remove(command_type)
    print(f"Action request {subreddit.display_name} {mod_comment.author.name}: {mod_comment.permalink}")

    actionable_content = mod_comment.parent()
    is_submission = type(actionable_content) is Submission
    url = f"https://www.reddit.com{actionable_content.permalink}"
    notes = reddit_actions_handler.toolbox.usernotes.list_notes(actionable_content.author.name, reverse=True)

    for note in notes:
        if note.url is None:
            continue
        # already usernoted: a usernote already contains the link to this content
        if actionable_content.id in note.url:
            print(f"Ignoring as already actioned {actionable_content.id}:"
                  f" {actionable_content.author.name}: {actionable_content.permalink}")
            return

    rules_int = find_rules(remaining_commands)
    # if rules exist, remove it from the remaining commands
    if rules_int and remaining_commands:
        remaining_commands.remove(remaining_commands[0])

    ban_type = find_ban(discord_client, subreddit, actionable_content.author, remaining_commands)
    # if ban command exists, remove it from the remaining commands
    if ban_type and remaining_commands:
        remaining_commands.remove(remaining_commands[0])
    # if mod can't ban, overwrite to empty
    if mod_comment.author.name not in subreddit_tracker.ban_mods:
        ban_type = ""

    message = find_message(remaining_commands)

    rules_str = ("R" + ",".join(str(x) for x in rules_int)) if len(rules_int) > 0 else "No cited rules"
    full_note = rules_str + (": " + message if message else "")
    if command_type == ".r":
        print(f"Removing+Usernoting: {actionable_content.author.name} for {rules_str}: {actionable_content.permalink}")
        reddit_actions_handler.write_removal_reason(url, rules_int, not is_submission)
        reddit_actions_handler.remove_content("Mod removal request: mod", mod_comment)
        reddit_actions_handler.remove_content("Mod removal request: user", actionable_content)
        reddit_actions_handler.write_usernote(url, actionable_content.author.name, None, full_note)
        internal_detail = f"Usernotes command by {mod_comment.author.name} for {full_note}"
        if ban_type:
            reddit_actions_handler.ban_user(actionable_content.author.name, rules_str, internal_detail, ban_type)
        ban_message = ("Ban:" + (ban_type if ban_type.isnumeric() else "Perm" + " " + internal_detail)
                       if ban_type else "")
        reddit_actions_handler.send_message(mod_comment.author, "Bot Action Summary",
                                            f"I have performed the following:\n\n"
                                            f"URL: https://www.reddit.com{actionable_content.permalink}  \n\n"
                                            f"Usernote detail: {full_note}\n\n"
                                            f"{ban_message}")
    elif command_type == ".n":
        print(f"Usernoting: {actionable_content.author.name} for {rules_str}: {actionable_content.permalink}")
        reddit_actions_handler.write_usernote(url, actionable_content.author.name, None, full_note)
        reddit_actions_handler.remove_content("Mod removal request: mod", mod_comment)
        reddit_actions_handler.send_message(mod_comment.author, "Bot Action Summary",
                                            f"I have performed the following:\n\n"
                                            f"URL: https://www.reddit.com{actionable_content.permalink}  \n\n"
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
