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
