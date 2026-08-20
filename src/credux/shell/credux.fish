# Add to ~/.config/fish/config.fish:  credux shell-init --shell fish | source
function pu
  set -l credux_statement
  if test (count $argv) -eq 0
    set credux_statement (credux env --unset --shell fish)
  else
    set credux_statement (credux env $argv[1] --shell fish)
  end
  set -l credux_status $status
  # Failure is detected two ways because fish versions disagree about whether
  # `set x (cmd)` reports cmd's exit status: the status where fish provides
  # one, and an empty capture otherwise (credux writes its error to stderr,
  # which is not captured, and prints nothing on stdout when it fails).
  # Either way `pu badprofile` fails instead of silently doing nothing.
  if test $credux_status -ne 0
    return $credux_status
  end
  if test -z "$credux_statement"
    return 1
  end
  printf '%s\n' $credux_statement | source
end
