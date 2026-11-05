# ComfyUI Runpod Pod

Single-container Runpod Pod deployment for the local ComfyUI and `webui_flux2`
application.

## What is included

- Official `runpod/worker-comfyui:main-base` CUDA base image.
- ComfyUI source pinned to the tested local revision.
- `webui_flux2` application source.
- llama.cpp server built with CUDA support.
- Runtime model downloads to ephemeral container storage at `/models`.
- User vaults and history under the only Network Volume path,
  `/workspace/user-data`.

Models, llama.cpp runtime files, source checkout, and service logs are
intentionally kept on the container disk. They are not stored on the Network
Volume and may be downloaded again when a new Pod is created. Only
`/workspace/user-data` is persistent. Set `HF_TOKEN` in the Pod environment
when Hugging Face access requires authentication.

The model downloader is strict by default. For a service-only boot test, set
`SKIP_MODEL_DOWNLOAD=1`; for a real deployment leave it at `0`. If a model
repository returns Hugging Face `401`, add a valid `HF_TOKEN` environment
variable to the Pod. The token is used only for the download process. The
current core manifest uses the public `Comfy-Org/Flux-2-Klein-4B` and
`Comfy-Org/Z-Image-Turbo` repositories. Gemma files are downloaded only when
`DOWNLOAD_LLM_MODELS=1` is set, because that repository requires authorization.

The Pod starts ComfyUI and WebUI before model downloads begin. The WebUI system
monitor shows the queue and allows reordering during the first 60 seconds;
without a change it uses Gemma (when enabled), Flux 2 Klein, then Z-Image Turbo.
Completed groups are immediately usable while later groups continue downloading.
Once a file download starts, reordering is refused so active downloads are not
interrupted.

## Runpod settings

- Pod, not Serverless.
- Container disk: 120 GB.
- Network Volume: disabled by default. Pass a positive `-VolumeInGb` only when
  persistent user data is intentionally required.
- When creating a Pod from the template, pass `--container-disk-in-gb 120`
  explicitly; Runpod can otherwise fall back to its 20 GB default.
- For low-cost tests, use an available community GPU and
  `--cloud-type COMMUNITY`; do not use secure RTX 4090/A40 pricing unless
  explicitly required. Prefer RTX A5000, RTX 4090 community, or another
  available GPU at or below the target hourly rate.
- Expose HTTP port: `8189`.
- Optional internal diagnostics: `8188`.
- Do not use Docker Compose; the image runs all services through
  `docker/start.sh`.

## Build-free deployment

Use the official `runpod/worker-comfyui:main-base` image directly. It contains
the CUDA, Python, PyTorch, and ComfyUI runtime and is not a model. The template's
startup command downloads `docker/bootstrap-runpod.sh` from this repository,
installs the application source and a CUDA 12.8 llama-server release, and then
starts both services. No local Docker build or 5 GB base-image download is
needed on Windows.

Runpod's HTTP proxy URL is:
`https://<POD_ID>-8189.proxy.runpod.net`

The official Runpod worker image uses `/comfyui` and already supplies the
CUDA/Python runtime. The startup script overlays this application's files
without placing runtime files on Network Storage.

Example volume-free test command:

```powershell
runpodctl pod create --name comfyui-e2e --template-id fccm9qs9s6 `
  --gpu-id "NVIDIA RTX A5000" --cloud-type COMMUNITY `
  --container-disk-in-gb 120 --volume-in-gb 0
```

## Sources

- [Runpod ComfyUI base image](https://github.com/runpod-workers/comfyui-base)
- [Runpod port exposure](https://docs.runpod.io/pods/configuration/expose-ports)
- [Runpod Pod management](https://docs.runpod.io/pods/manage-pods)
- [Runpod template CLI](https://github.com/runpod/runpodctl)
