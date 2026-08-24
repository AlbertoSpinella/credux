
# Tab completion for the `cdx` function. Click's generated script registers
# itself against the `credux` command alone, so the wrapper needs a completer
# of its own; it asks credux for the candidates so `cdx <TAB>` offers exactly
# what `credux env <TAB>` offers.
_credux_cdx_completion_bash() {
    local IFS=$'\n'
    # Only stdout is read. credux's diagnostics go to stderr, and a
    # diagnostic offered as a candidate would be inserted into the command
    # line on TAB; the exit status is ignored for the same reason.
    COMPREPLY=($(credux completions profiles "${COMP_WORDS[COMP_CWORD]}" 2>/dev/null))
}

complete -o nosort -F _credux_cdx_completion_bash cdx
