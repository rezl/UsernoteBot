import re


class Settings:
    # set to True to prevent any bot actions (report, remove, comments)
    is_dry_run = False
    guild_name = None


class CollapseSettings(Settings):
    guild_name = 'Collapse Moderators'


class UFOsSettings(Settings):
    guild_name = 'UFO Moderators'


class SettingsFactory:
    settings_classes = {
        'collapse': CollapseSettings,
        'ufos': UFOsSettings,
    }

    @staticmethod
    def get_settings(subreddit_name):
        # ensure only contains valid characters
        if not re.match(r'^\w+$', subreddit_name):
            raise ValueError("subreddit_name contains invalid characters")

        settings_class = SettingsFactory.settings_classes.get(subreddit_name.lower(), Settings)
        return settings_class()
