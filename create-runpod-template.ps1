param(
    [string]$Image = "runpod/worker-comfyui:main-base",
    [int]$VolumeInGb = 10,
    [ValidateSet("0", "1")]
    [string]$SkipModelDownload = "0"
)

$ErrorActionPreference = "Stop"
$runpodctl = Join-Path $env:LOCALAPPDATA "Programs\runpodctl\runpodctl.exe"
if (!(Test-Path $runpodctl)) {
    throw "runpodctl is not installed"
}

$readme = @"
ComfyUI + webui_flux2 + local Gemma LLM.
Models download to ephemeral container storage; user data persists in /workspace/user-data.
"@
$envJson = '{\"WEBUI_DATA_ROOT\":\"/workspace/user-data\",\"MODEL_ROOT\":\"/models\",\"SKIP_MODEL_DOWNLOAD\":\"' + $SkipModelDownload + '\"}'
$bootstrap = 'bash,-lc,wget -qO- https://raw.githubusercontent.com/ahmetyusufprojects/comfyui-runpod-pod/main/docker/bootstrap-runpod.sh | bash'
& $runpodctl template create `
    --name "comfyui-runpod-worker-userdata-only-v4" `
    --image $Image `
    --container-disk-in-gb 120 `
    --volume-in-gb $VolumeInGb `
    --volume-mount-path "/workspace/user-data" `
    --ports "8189/http,8188/http" `
    --port-labels "8189=webui,8188=comfyui" `
    --docker-start-cmd $bootstrap `
    --env $envJson `
    --readme $readme
if ($LASTEXITCODE -ne 0) {
    throw "Runpod template creation failed"
}
