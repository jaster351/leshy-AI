param([switch]$Rotate)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$brain = Join-Path $repo 'dist\Leshy\LeshyBrain'
if (-not (Test-Path -LiteralPath $brain -PathType Container)) {
  throw 'LeshyBrain not found in dist\Leshy. First compile/start Leshy and retry.'
}
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python 3 is required to issue the limited Android voice access key.' }
$old = $env:PYTHONPATH
try {
  $env:PYTHONPATH = Join-Path $repo 'src'
  $argsList = @('-m','leshy.android_voice_access','--brain-dir',$brain)
  if ($Rotate) { $argsList += '--rotate' }
  & $python.Source @argsList
  if ($LASTEXITCODE -ne 0) { throw 'Android voice token provisioning failed.' }
} finally {
  if ($null -eq $old) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
  else { $env:PYTHONPATH = $old }
}
