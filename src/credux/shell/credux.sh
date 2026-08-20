# Add to ~/.bashrc or ~/.zshrc:  eval "$(credux shell-init)"
pu() {
  # Declared on its own line on purpose: in `local x=$(cmd)` the exit status
  # belongs to `local`, not to the command substitution, so a failing
  # `credux env` would go unnoticed and `pu badprofile` would silently do
  # nothing. credux's error message goes to stderr and is not captured here.
  local _credux_statement
  if [ "$#" -eq 0 ]; then
    _credux_statement=$(credux env --unset) || return $?
  else
    _credux_statement=$(credux env "$1") || return $?
  fi
  eval "$_credux_statement"
}
