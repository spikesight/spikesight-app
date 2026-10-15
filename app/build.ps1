# Build the shipping SpikeSight: one self-contained file, no prerequisites.
#
#   .\build.ps1
#
# Self-contained rather than framework-dependent on purpose. It is bigger,
# but "unzip it and run it" has to be true - telling somebody to install a
# .NET runtime first is how a tool stops getting used.
#
# WPF cannot be trimmed, so most of the size is the framework itself and
# there is no honest way to get it much lower.

param([string]$Configuration = "Release")

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$out = Join-Path $root "dist"
$staging = Join-Path $out "SpikeSight"

if (Get-Process SpikeSight.App -ErrorAction SilentlyContinue) {
    throw "SpikeSight is running; close it first or the build writes a stale binary."
}

Write-Host "Running the tests..."
dotnet test (Join-Path $root "SpikeSight.sln") -c $Configuration --nologo -v q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; not packaging." }

Write-Host "Publishing..."
Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
dotnet publish (Join-Path $root "SpikeSight.App") `
    -c $Configuration -r win-x64 --self-contained true `
    -p:PublishSingleFile=true `
    -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:EnableCompressionInSingleFile=true `
    -p:DebugType=none `
    -o $staging --nologo -v q
if ($LASTEXITCODE -ne 0) { throw "Publish failed." }

$version = (Select-String -Path (Join-Path $root "SpikeSight.App\SpikeSight.App.csproj") `
    -Pattern '<Version>(.*?)</Version>').Matches.Groups[1].Value
if (-not $version) { $version = "3.0.0-preview" }

@"
SpikeSight $version

Run SpikeSight.exe. Start the Riot Client first, or it will wait for it.

Windows will warn you about an unsigned app: More info -> Run anyway. There
is no installer and nothing is written to Program Files; to uninstall,
delete this folder. The only registry entry is the optional "start with
Windows" one, and turning it off removes it.

Your notes live in %LOCALAPPDATA%\SpikeSight and are not touched by deleting
this folder.
"@ | Set-Content (Join-Path $staging "READ ME FIRST.txt") -Encoding utf8

Rename-Item (Join-Path $staging "SpikeSight.App.exe") "SpikeSight.exe"

$zip = Join-Path $out "SpikeSight-$version-windows.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "$staging\*" -DestinationPath $zip

$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
"$hash  $(Split-Path $zip -Leaf)" | Set-Content (Join-Path $out "SHA256SUMS.txt") -Encoding ascii

$exeMb = [Math]::Round((Get-Item (Join-Path $staging "SpikeSight.exe")).Length / 1MB, 1)
$zipMb = [Math]::Round((Get-Item $zip).Length / 1MB, 1)

Write-Host ""
Write-Host "Build complete."
Write-Host "  App    : $(Join-Path $staging 'SpikeSight.exe')  ($exeMb MB, one file)"
Write-Host "  Zip    : $zip  ($zipMb MB)"
Write-Host "  SHA256 : $hash"
