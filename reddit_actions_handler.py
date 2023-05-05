import time
import traceback

import pmtw
from pmtw import ToolboxNote
from praw.exceptions import RedditAPIException

from settings import Settings


class RedditActionsHandler:
    max_retries = 3
    retry_delay_secs = 10

    def __init__(self, reddit, subreddit, discord_client):
        self.reddit = reddit
        self.subreddit = subreddit
        self.toolbox = pmtw.Toolbox(
            subreddit
        )
        self.discord_client = discord_client
        self.last_call_time = 0

    def write_usernote(self, url, user, note_type, detail):
        print(f"Writing usernote for {str(user)}: {detail}")

        redditor = self.reddit.redditor(user)
        note = ToolboxNote(redditor, detail, warning=note_type, url=url)
        self.reddit_call(lambda: self.toolbox.usernotes.add(note))

    def write_removal_reason(self, content, rules):
        print(f"Writing removal comment for {str(content)}: {str(rules)}")

        reddit_rule_message = ""
        for rule in rules:
            rule_detail = self.subreddit.rules[rule - 1]
            rule_message = f"Rule {rule_detail.priority + 1}: {rule_detail.short_name}\n\n" \
                           f"{rule_detail.description}\n\n"
            reddit_rule_message = reddit_rule_message + rule_message

        response = f"Hi, thanks for contributing." \
                   f"However, your submission was removed from r/{self.subreddit.display_name}.\n\n" \
                   f"{reddit_rule_message}" \
                   f"You can message the mods if you feel this was in error," \
                   f" please include a link to the comment or post in question."
        comment = self.reddit_call(lambda: content.reply(response))
        self.reddit_call(lambda: comment.mod.distinguish(sticky=True), reddit_throttle_secs=1)
        self.reddit_call(lambda: comment.mod.lock(), reddit_throttle_secs=1)

    def remove_content(self, removal_reason, content):
        print(f"Removing content, reason: {removal_reason}")
        self.reddit_call(lambda: content.mod.remove(mod_note=removal_reason))

    def send_message(self, user, subject, detail):
        print(f"Send message to {user}, detail: {detail}")
        self.reddit_call(lambda: user.message(subject, detail))

    def ban_user(self, user, external_detail, internal_detail, duration):
        print(f"Banning {user} for {duration}, detail: {internal_detail}")
        if duration.isnumeric():
            self.reddit_call(lambda: self.subreddit.banned.add(user, ban_message=external_detail,
                                                               ban_reason=internal_detail, duration=int(duration)))
        else:
            self.reddit_call(lambda: self.subreddit.banned.add(user, ban_message=external_detail,
                                                               ban_reason=internal_detail))

    def reddit_call(self, callback, reddit_throttle_secs=5):
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return
        # throttle reddit calls to prevent reddit throttling
        elapsed_time = time.time() - self.last_call_time
        if elapsed_time < reddit_throttle_secs:
            time.sleep(reddit_throttle_secs - elapsed_time)
        # retry reddit exceptions, such as throttling or reddit issues
        for i in range(self.max_retries):
            try:
                result = callback()
                self.last_call_time = time.time()
                return result
            except RedditAPIException as e:
                message = f"Exception in RedditRetry: {e}\n```{traceback.format_exc()}```"
                self.discord_client.send_error_msg(message)
                print(message)
                if i < self.max_retries - 1:
                    print(f"Retrying in {self.retry_delay_secs} seconds...")
                    time.sleep(self.retry_delay_secs)
                else:
                    raise e
