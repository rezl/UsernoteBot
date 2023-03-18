import re


class Settings:
    # set to True to prevent any bot actions (report, remove, comments)
    is_dry_run = False


class SettingsFactory:
    settings_classes = {
        'collapse': Settings,
        'ufos': Settings,
    }

    @staticmethod
    def get_settings(subreddit_name):
        # ensure only contains valid characters
        if not re.match(r'^\w+$', subreddit_name):
            raise ValueError("subreddit_name contains invalid characters")

        settings_class = SettingsFactory.settings_classes.get(subreddit_name.lower(), Settings)
        return settings_class()

