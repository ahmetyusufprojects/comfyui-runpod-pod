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

## Build without downloading the 5 GB base image on Windows

The official Runpod image contains the CUDA, Python, PyTorch, and ComfyUI
runtime. It is not a model and must not be copied into this repository.
Building locally downloads that large base layer. The repository therefore
also includes a GitHub Actions build that performs the build remotely and
pushes only the resulting image layers to GitHub Container Registry:

1. Push to `main`, or run **Build Runpod image** from the Actions tab.
2. Use `ghcr.io/ahmetyusufprojects/comfyui-runpod-pod:latest` as the Pod
   image after making the package public in GitHub Packages.

The workflow uses GitHub Actions layer caching, so later application-only
changes do not rebuild or upload the large base layer from the Windows
machine.

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
