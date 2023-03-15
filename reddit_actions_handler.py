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
    def __init__(self, reddit, subreddit, lock):
        self.reddit = reddit
        self.subreddit = subreddit
        self.toolbox = pmtw.Toolbox(
            subreddit
        )
        self.lock = lock

    def write_usernote(self, url, user, note_type, detail):
        self.lock.acquire()
        print(f"Writing usernote for {str(user)}: {detail}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        redditor = self.reddit.redditor(user)
        note = ToolboxNote(redditor, detail, warning=note_type, url=url)
        self.toolbox.usernotes.add(note)
        time.sleep(5)
        self.lock.release()

    def write_removal_reason(self, url, rules, is_comment):
        self.lock.acquire()
        print(f"Writing removal comment for {str(url)}: {str(rules)}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        content = self.reddit.comment(Comment.id_from_url(url)) \
            if is_comment \
            else self.reddit.submission(Submission.id_from_url(url))

        reddit_rule_message = ""
        for rule in rules:
            rule_detail = self.subreddit.rules[rule - 1]
            rule_message = f"Rule {rule_detail.priority + 1}: {rule_detail.short_name}\n\n" \
                           f"{rule_detail.description}\n\n"
            reddit_rule_message = reddit_rule_message + rule_message

        comment = content.reply(f"Hi, thanks for contributing. "
                                f"However, your submission was removed from r/collapse.\n\n"
                                f"{reddit_rule_message}"
                                f"You can message the mods if you feel this was in error,"
                                f" please include a link to the comment or post in question.")
        comment.mod.distinguish(sticky=True)
        comment.mod.lock()
        time.sleep(5)
        self.lock.release()

    def remove_content(self, removal_reason, content):
        self.lock.acquire()
        print(f"Removing content, reason: {removal_reason}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        content.mod.remove(mod_note=removal_reason)
        time.sleep(5)
        self.lock.release()

    def send_message(self, user, subject, detail):
        self.lock.acquire()
        print(f"Send message to {user}, detail: {detail}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        user.message(subject, detail)
        time.sleep(5)
        self.lock.release()

    def ban_user(self, user, external_detail, internal_detail, duration):
        self.lock.acquire()
        print(f"Banning {user} for {duration}, detail: {internal_detail}")
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        if duration.isnumeric():
            self.subreddit.banned.add(user, ban_message=external_detail, ban_reason=internal_detail,
                                      duration=int(duration))
        else:
            self.subreddit.banned.add(user, ban_message=external_detail, ban_reason=internal_detail)
        time.sleep(5)
        self.lock.release()
