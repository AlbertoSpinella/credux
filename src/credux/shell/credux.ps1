# Add to $PROFILE:  credux shell-init --shell powershell | Out-String | Invoke-Expression
function pu {
  # Not $Profile: that is a PowerShell automatic variable (the path to the
  # current profile script), and binding a parameter over it shadows it for
  # the body of this function.
  param([string]$Name)
  if (-not $Name) {
    $statement = credux env --unset --shell powershell
  } else {
    $statement = credux env $Name --shell powershell
  }
  # Returning here leaves $LASTEXITCODE as credux set it, so a caller testing
  # it after `pu` still sees the failure. Invoking the (empty) statement
  # would overwrite it with Invoke-Expression's own success.
  if ($LASTEXITCODE -ne 0) { return }
  $statement | Invoke-Expression
}

# Tab completion for `pu`. Click ships no PowerShell backend, so this is the
# only completion credux registers here. -ParameterName is required for a
# PowerShell function: without it the completer is only consulted for native
# commands, which `pu` is not.
Register-ArgumentCompleter -CommandName pu -ParameterName Name -ScriptBlock {
  param($commandName, $parameterName, $wordToComplete, $commandAst, $fakeBoundParameters)
  # Only stdout is used: credux writes its diagnostics to stderr, and one
  # offered as a candidate would be inserted into the command line on TAB.
  credux completions profiles $wordToComplete 2>$null | ForEach-Object {
    [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
  }
}
