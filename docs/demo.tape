# A VHS script for the README demo GIF: https://github.com/charmbracelet/vhs
#
#   vhs docs/demo.tape        # writes docs/demo.gif
#
# Record it against an account you are happy to publish. Everything the
# terminal prints ends up in the GIF: organisation name, account names, account
# IDs, user names, and role names. `credux list` shows all of them at once.
# Nothing secret is printed by any command below, but names are not nothing.

Output docs/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms

# Assumes the credential already exists, so the GIF shows the daily loop rather
# than a one-time setup prompt. Add it beforehand with:
#   credux add iam-user
Type "credux list"
Enter
Sleep 3s

Type "credux start acme-billing-User-ada"
Enter
Sleep 3s

# The profile is now a real profile: no wrapper, no credential_process.
Type "aws sts get-caller-identity --profile acme-billing-User-ada"
Enter
Sleep 4s

# ...and `cdx` puts it in this shell, for every tool that reads AWS_PROFILE.
Type "cdx billing"
Enter
Sleep 2s
Type "terraform plan | head -3"
Enter
Sleep 4s

# The guard: no profile selected means a loud failure, not someone else's keys.
Type "unset AWS_PROFILE; aws sts get-caller-identity"
Enter
Sleep 4s

Type "credux stop acme-billing-User-ada"
Enter
Sleep 3s
