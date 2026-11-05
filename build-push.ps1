param(
    [Parameter(Mandatory = $true)]
    [string]$Image
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
docker buildx build --platform linux/amd64 --file "$root\docker\Dockerfile" --tag $Image --push $root
