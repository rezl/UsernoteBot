import time

import pmtw
from pmtw import ToolboxNote
from praw.models import Comment, Submission

from settings import Settings

note_type_translation = {
    "ban": ["b", "ban"],
    "spamwatch": ["spam", "sw"],
    "low_quality": ["low_quality", "low quality", "lq"],
    "abusewarn": ["abusewarn", "abuse warn", "aw"],
    "permban": ["permban", "perm ban", "perm"],
    None: [None, "(empty)", "empty", "null", "no", "n/a"],
    "warning": ["warning", "warn"],
    "spamwarn": ["spamwarn", "spam warn"],
    "gooduser": ["gooduser", "good", "gu"],
    "low_quality_contributor": ["low_quality_contributor", "low quality contributor", "lqc"]
}

mapdata = ""
for key, value in note_type_translation.items():
    mapdata = mapdata + "\n" + str(key) + ": " + str(value)


class RedditActionsHandler:
    def __init__(self, reddit, subreddit):
        self.reddit = reddit
        self.subreddit = subreddit
        self.toolbox = pmtw.Toolbox(
            subreddit
        )

    def write_usernote(self, url, user, note_type, detail):
        print(f"Writing usernote for {str(user)}: {detail}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        redditor = self.reddit.redditor(user)
        note = ToolboxNote(redditor, detail, warning=note_type, url=url)
        self.toolbox.usernotes.add(note)
        time.sleep(5)

    def write_removal_reason(self, url, rules, is_comment):
        print(f"Writing removal comment for {str(url)}: {str(rules)}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        content = self.reddit.comment(Comment.id_from_url(url)) \
            if is_comment \
            else self.reddit.submission(Submission.id_from_url(url))

        reddit_rule_message = ""
        for rule in rules:
            rule_detail = self.subreddit.rules[int(rule) - 1]
            rule_message = f"Rule {rule_detail.priority + 1}: {rule_detail.short_name}\n\n" \
                           f"{rule_detail.description}\n\n"
            reddit_rule_message = reddit_rule_message + rule_message

        comment = content.reply(f"Hi, thanks for contributing. "
                                f"However, your submission was removed from r/collapse for:\n\n"
                                f"{reddit_rule_message}"
                                f"You can message the mods if you feel this was in error,"
                                f" please include a link to the comment or post in question.")
        comment.mod.distinguish(sticky=True)
        comment.mod.lock()
        time.sleep(5)

    @staticmethod
    def remove_comment(removal_reason, comment):
        print(f"Removing comment, reason: {removal_reason}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        comment.mod.remove(mod_note=removal_reason)
        time.sleep(5)

    @staticmethod
    def send_message(user, subject, detail):
        print(f"Send message to {user}, detail: {detail}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        user.message(subject, detail)
        time.sleep(5)
