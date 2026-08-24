
# Tab completion for the `cdx` function - see cdx.bash for why it needs one.
_credux_cdx_completion_zsh() {
    local -a matches
    # Only stdout is read; stderr and the exit status are discarded so that a
    # diagnostic can never be offered as a candidate.
    matches=("${(@f)$(credux completions profiles "${words[CURRENT]}" 2>/dev/null)}")
    if [ -n "$matches" ]; then
        # -U because credux matches substrings: most candidates do not start
        # with what has been typed, and zsh would otherwise discard them, so
        # `cdx mana<TAB>` would never reach `prod-management`.
        compadd -U -V unsorted -a matches
        # -U also makes zsh replace the typed word with the matches' common
        # prefix, which for substring matches is usually empty: `cdx mana<TAB>`
        # would erase "mana" and offer nothing. Cycling through the matches
        # leaves the line alone until one of them is chosen.
        compstate[insert]=menu
    fi
}

compdef _credux_cdx_completion_zsh cdx
