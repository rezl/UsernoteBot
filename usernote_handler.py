import pmtw
from pmtw import ToolboxNote
from praw.models import Comment, Submission

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


class UsernoteHandler:
    def __init__(self, reddit, subreddit):
        self.reddit = reddit
        self.subreddit = subreddit
        self.toolbox = pmtw.Toolbox(
            subreddit
        )

    def write_usernote(self, url, user, note_type, detail):
        redditor = self.reddit.redditor(user)
        note = ToolboxNote(redditor, detail, warning=note_type, url=url)
        self.toolbox.usernotes.add(note)

    def write_removal_reason(self, url, rule, is_comment):
        content = self.reddit.comment(Comment.id_from_url(url)) \
            if is_comment \
            else self.reddit.submission(Submission.id_from_url(url))

        reddit_rule = self.subreddit.rules[rule - 1]
        comment = content.reply(f"Hi, thanks for contributing. "
                                f"However, your submission was removed from r/collapse for:\n\n"
                                f"Rule {reddit_rule.priority + 1}: {reddit_rule.short_name}\n\n"
                                f"{reddit_rule.description}\n\n"
                                f"You can message the mods if you feel this was in error,"
                                f" please include a link to the comment or post in question.")
        comment.mod.distinguish(sticky=True)
        comment.mod.lock()
