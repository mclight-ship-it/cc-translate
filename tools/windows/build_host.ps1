param(
    [ValidateSet('x86', 'x64')]
    [string[]]$Architecture = @('x86', 'x64')
)

$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or !$visualStudio) {
    throw 'Visual Studio C++ build tools are required to rebuild the Windows host.'
}
$vcvars = Join-Path $visualStudio 'VC\Auxiliary\Build\vcvarsall.bat'
$source = Join-Path $PSScriptRoot 'python_host.c'
$output = Join-Path $root 'data\windows'
[void](New-Item -ItemType Directory -Force -Path $output)
$originalPath = $env:PATH
try {
    $env:PATH = "$(Split-Path $vswhere -Parent);$originalPath"
    foreach ($arch in $Architecture) {
        $build = Join-Path $PSScriptRoot ".build\$arch"
        [void](New-Item -ItemType Directory -Force -Path $build)
        $executable = Join-Path $output "cc-python-host-$arch.exe"
        $command = 'call "{0}" {1} >nul && cd /d "{2}" && cl /nologo /O1 /MT /GS /W4 /WX /DUNICODE /D_UNICODE /Brepro "{3}" /Fe:"{4}" /link /SUBSYSTEM:WINDOWS /DYNAMICBASE /NXCOMPAT /OPT:REF /OPT:ICF /Brepro shell32.lib user32.lib' -f $vcvars, $arch, $build, $source, $executable
        & $env:ComSpec /d /c $command
        if ($LASTEXITCODE -ne 0) {
            throw "Windows host build failed: $arch"
        }
    }
} finally {
    $env:PATH = $originalPath
}
