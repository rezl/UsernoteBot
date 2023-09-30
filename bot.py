import traceback
from threading import Thread
import os

from praw.reddit import Submission

import config
import time
import praw

from discord_client import DiscordClient
from reddit_actions_handler import RedditActionsHandler
from settings import SettingsFactory
from subreddit_tracker import SubredditTracker
from resilient_thread import ResilientThread
from usernote_utils import find_rules, find_ban, find_message

max_retries = 5
retry_wait_time_secs = 30


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
            reddit_handler = create_usernotes_thread(bot_password, bot_username, client_id, client_secret,
                                                     discord_client, subreddit_name)
            settings = SettingsFactory.get_settings(subreddit_name)
            if settings.guild_name:
                discord_client.add_usernote_guild(settings.guild_name, reddit_handler)
    except Exception as e:
        message = f"Exception in main processing: {e}\n```{traceback.format_exc()}```"
        discord_client.send_error_msg(message)
        print(message)

    # this is required as otherwise discord fails when main thread is done
    while True:
        time.sleep(5)


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
    subreddit_tracker = SubredditTracker(subreddit)
    reddit_handler = RedditActionsHandler(reddit, subreddit, discord_client)
    thread = ResilientThread(discord_client, f"{subreddit_name}-Usernotes",
                             target=handle_comment_stream, args=(discord_client, subreddit_tracker, reddit_handler))
    thread.start()
    print(f"Created {subreddit_name} subreddit thread")
    return reddit_handler


def handle_comment_stream(discord_client, subreddit_tracker, reddit_handler):
    subreddit = subreddit_tracker.subreddit

    for comment in subreddit.stream.comments():
        if comment.author not in subreddit_tracker.get_cached_mods():
            continue
        try:
            handle_mod_response(discord_client, subreddit_tracker, reddit_handler, comment)
        except Exception as e:
            message = f"Exception in comment processing: {e}\n```{traceback.format_exc()}```"
            discord_client.send_error_msg(message)
            print(message)
            reddit_handler.send_message(comment.author, "Error during removal request processing",
                                        f"I've encountered an error whilst actioning your request:"
                                        f"  \n\n"
                                        f"URL: https://www.reddit.com{comment.permalink}  \n\n"
                                        f"Error: {e}\n\n"
                                        f"Please review to ensure all is as expected. "
                                        f"If your command is in the correct format, "
                                        f"e.g. \".r 1,2,3\", please raise this issue to the developers")


def handle_mod_response(discord_client, subreddit_tracker, reddit_handler, mod_comment):
    subreddit = subreddit_tracker.subreddit

    # input must be space separated
    remaining_commands = mod_comment.body.split(" ")
    command_type = remaining_commands[0]
    if command_type not in [".r", ".n", ".u"]:
        return
    # remaining_command always exists, so remove it
    remaining_commands.remove(command_type)
    action_request = f"Action request {subreddit.display_name} {mod_comment.author.name}: {mod_comment.permalink}"
    print(action_request)

    if not mod_comment.author or mod_comment.removed:
        print("Ignoring - mod comment is removed, should already be actioned")
        return

    actionable_content = mod_comment.parent()
    if not actionable_content.author:
        reddit_handler.remove_content("Mod removal request: mod", mod_comment)
        reddit_handler.send_message(mod_comment.author, "Unable to action content: content deleted",
                                    f"I could not action this content, as it was deleted:\n\n"
                                    f"URL: https://www.reddit.com{actionable_content.permalink}  \n\n"
                                    f"I have removed your mod comment."
                                    f" I cannot ban, remove, or usernote the unknown user.")
        return

    # non-full mods cannot remove posts
    if command_type == ".r" and isinstance(actionable_content, Submission) and \
            (mod_comment.author.name not in subreddit_tracker.full_mods):
        reddit_handler.remove_content("Mod removal request: mod", mod_comment)
        reddit_handler.send_message(mod_comment.author, "Error during removal request",
                                    f"I could not remove this post, as you are a comment mod:\n\n"
                                    f"URL: https://www.reddit.com{actionable_content.permalink}  \n\n"
                                    f"I applaud your enthusiasm, keep it up! But with comments, for now :)")
        return

    url = f"https://www.reddit.com{actionable_content.permalink}"

    # supported format: ".r/.n/.u <rules> <ban> <usernote>"

    # if rules exist, remove it from the remaining commands
    cited_rules = find_rules(remaining_commands[0] if remaining_commands else "")
    if cited_rules:
        remaining_commands.remove(remaining_commands[0])

    # normalize ban command without the B
    ban_command = remaining_commands[0][1:] \
        if (remaining_commands and remaining_commands[0].startswith("b")) else ""
    ban_type = find_ban(discord_client, subreddit, actionable_content.author, ban_command)
    if ban_type:
        remaining_commands.remove(remaining_commands[0])
        # non-FMs can't ban, overwrite to empty
        if mod_comment.author.name not in subreddit_tracker.full_mods:
            discord_client.send_error_msg(f"Detected ban attempt from a non-FM:\n\n{action_request}")
            ban_type = None

    message = find_message(remaining_commands)

    rules_str = ("R" + ",".join(str(x) for x in cited_rules)) if len(cited_rules) > 0 else "No cited rules"
    full_note = f"[{mod_comment.author.name}] {rules_str}: {message if message else ''}"
    if command_type == ".r":
        print(f"Removing+Usernoting: {actionable_content.author.name} for {rules_str}: {actionable_content.permalink}")
        reddit_handler.remove_content("Mod removal request: mod", mod_comment)
        reddit_handler.write_removal_reason(actionable_content, cited_rules)
        reddit_handler.remove_content("Mod removal request: user", actionable_content)
        reddit_handler.write_usernote(url, actionable_content.author.name, None, full_note)
        internal_detail = f"Usernotes command by {mod_comment.author.name} for {full_note}"
        if ban_type:
            reddit_handler.ban_user(actionable_content.author.name, rules_str, internal_detail, ban_type)
        ban_message = ("Ban:" + (ban_type if ban_type.isnumeric() else "Perm" + " " + internal_detail)
                       if ban_type else "")
        reddit_handler.send_message(mod_comment.author, "Bot Action Summary",
                                    f"I have performed the following:\n\n"
                                    f"URL: https://www.reddit.com{actionable_content.permalink}  \n\n"
                                    f"Usernote detail: {full_note}\n\n"
                                    f"{ban_message}")
    elif command_type in [".n", ".u"]:
        print(f"Usernoting: {actionable_content.author.name} for {rules_str}: {actionable_content.permalink}")
        reddit_handler.remove_content("Mod removal request: mod", mod_comment)
        reddit_handler.write_usernote(url, actionable_content.author.name, None, full_note)
        reddit_handler.send_message(mod_comment.author, "Bot Action Summary",
                                    f"I have performed the following:\n\n"
                                    f"URL: https://www.reddit.com{actionable_content.permalink}  \n\n"
                                    f"Usernote detail: {full_note}\n\n")


if __name__ == "__main__":
    run_forever()
