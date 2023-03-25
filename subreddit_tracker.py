from datetime import timedelta, datetime


class SubredditTracker:
    def __init__(self, subreddit):
        self.subreddit = subreddit

        self.mods_last_check = datetime.utcfromtimestamp(0)
        self.removal_mods = list()
        self.ban_mods = list()
        self.get_cached_mods()

    def get_cached_mods(self):
        if datetime.utcnow() - self.mods_last_check < timedelta(days=1):
            return self.removal_mods

        removal_mods = list()
        ban_mods = list()
        for moderator in self.subreddit.moderator():
            if any(x in ["all", "posts"] for x in moderator.mod_permissions):
                removal_mods.append(moderator.name)
            if any(x in ["all", "access"] for x in moderator.mod_permissions):
                ban_mods.append(moderator.name)
        self.mods_last_check = datetime.utcnow()
        self.removal_mods = removal_mods
        self.ban_mods = ban_mods
        print(f"Refreshed removal_mods: {removal_mods}")
        print(f"Refreshed ban_mods: {ban_mods}")
        return removal_mods
