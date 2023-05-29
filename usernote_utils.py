import traceback


# attempts to find rule set from input
# if all input is a number, optionally delim sep, returns a list of these numbers
# if it cannot, returns empty list (ie no rules included)
def find_rules(rules):
    if not rules:
        return list()
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
def find_ban(discord_client, subreddit, user, ban_type):
    if not ban_type:
        return None
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
                                                  f"when processing {user}: {ban_type} with {log} {log.details}")
                    return "3"
        # if no notes, default to 3 days
        return "3"
    elif ban_type == "p":
        return "perm"
    return None


def find_message(command):
    if not command:
        return ""
    return " ".join(command)
