# ComfyUI Runpod Pod

Single-container Runpod Pod deployment for the local ComfyUI and `webui_flux2`
application.

## What is included

- Official `runpod/comfyui:latest` CUDA 12 base image.
- ComfyUI source pinned to the tested local revision.
- `webui_flux2` application source.
- llama.cpp server built with CUDA support.
- Runtime model downloads to ephemeral container storage.
- User vaults and history under `/workspace/user-data`.

Models are intentionally not committed to this repository or stored on the
Network Volume. Set `HF_TOKEN` in the Pod environment when Hugging Face access
requires authentication.

## Runpod settings

- Pod, not Serverless.
- Container disk: 120 GB.
- Network Volume: small, mounted at `/workspace`.
- Expose HTTP port: `8189`.
- Optional internal diagnostics: `8188`.
- Do not use Docker Compose; the image runs all services through
  `docker/start.sh`.

Runpod's HTTP proxy URL is:
`https://<POD_ID>-8189.proxy.runpod.net`

The official Runpod base image uses `/workspace/runpod-slim/ComfyUI` and
already supplies the CUDA/Python runtime. The startup script preserves that
layout and only overlays this application's files.

## Sources

- [Runpod ComfyUI base image](https://github.com/runpod-workers/comfyui-base)
- [Runpod port exposure](https://docs.runpod.io/pods/configuration/expose-ports)
- [Runpod Pod management](https://docs.runpod.io/pods/manage-pods)
- [Runpod template CLI](https://github.com/runpod/runpodctl)
