"""Launch or remove the dedicated WebUI Pod using the existing Flex volume."""

import argparse
import getpass
import json
import os
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

API = "https://api.runpod.io/v2"
REPO = "ahmetyusufprojects/comfyui-runpod-pod"
FLEX = "cmw3pwboc000008jp5i0j6vi8"
NAME = "gallery-webui"
DATA_PATH = "/opt/runtime/user-data"
TEMPLATE_ID = "0ax45qxl0n"


def request(url, method="GET", data=None, key=None):
    headers = {"User-Agent": "Mozilla/5.0"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def cli_json(args):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                encoding="utf-8", check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(f"{args[0]} çalıştırılamadı. CLI kurulumunu ve girişini kontrol edin.") from None
    return json.loads(result.stdout)


def current_image():
    runs = cli_json(["gh", "run", "list", "--repo", REPO, "--branch", "runpod/gallery-first",
                     "--status", "success", "--limit", "10", "--json", "headSha,workflowName"])
    run = next((item for item in runs if item["workflowName"] == "RunPod gallery image"), None)
    if run is None:
        raise RuntimeError("Başarılı WebUI imajı bulunamadı.")
    return f"ghcr.io/{REPO}:gallery-{run['headSha']}"


def runpod_key():
    if os.environ.get("RUNPOD_API_KEY"):
        return os.environ["RUNPOD_API_KEY"]
    config = Path(os.environ["USERPROFILE"]) / ".runpod/config.toml"
    return tomllib.loads(config.read_text(encoding="utf-8"))["apikey"]


def choose_gpu(limit):
    gpus = cli_json(["runpodctl", "gpu", "list"])
    return sorted((gpu for gpu in gpus
                   if gpu.get("available") and gpu.get("secureCloud")
                   and gpu.get("memoryInGb", 0) >= 16
                   and 0 < (gpu.get("securePricePerHr") or 0) <= limit
                   and "Blackwell" not in gpu["gpuId"]),
                  key=lambda gpu: gpu["securePricePerHr"])


def wait_webui(pod_id, open_browser):
    url = f"https://{pod_id}-8189.proxy.runpod.net"
    setup_url = f"https://{pod_id}-8192.proxy.runpod.net"
    print(f"Model kurulumu: {setup_url}", flush=True)
    print(f"WebUI: {url}", flush=True)
    if open_browser:
        webbrowser.open(setup_url)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            setup = request(setup_url + "/api/setup-state")
            if setup.get("hazir") and request(url + "/api/sunucu-hazir").get("hazir"):
                print("WebUI ve ayrı model kurulum ekranı hazır.")
                return
        except (OSError, ValueError):
            pass
        time.sleep(3)
    raise RuntimeError("WebUI zamanında yanıt vermedi. Açık Pod'u kapatabilir veya RunPod'dan kontrol edebilirsiniz.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true", help="Kasayı mühürledikten sonra bu Pod'u kapatır.")
    parser.add_argument("--restore", action="store_true", help="İlk kurulum parolası sormadan Flex kaydını açar.")
    parser.add_argument("--max-usd", type=float, default=0.32, help="Saatlik GPU fiyat sınırı.")
    parser.add_argument("--no-browser", action="store_true")
    options = parser.parse_args()
    if options.max_usd <= 0:
        parser.error("--max-usd pozitif olmalı.")
    key = runpod_key()
    pods = request(f"{API}/pods?name={urllib.parse.quote(NAME)}", key=key)
    if isinstance(pods, dict):
        pods = pods.get("data", [])
    matches = [pod for pod in pods if pod.get("name") == NAME
               and any(mount.get("volumeId") == FLEX
                       for mount in (pod.get("mounts") or {}).get("global", []))]
    if len(matches) > 1:
        raise RuntimeError("Birden fazla WebUI Pod'u bulundu. RunPod panelinden kontrol edin.")
    if options.stop:
        if matches:
            request(f"{API}/pods/{matches[0]['id']}", method="DELETE", key=key)
            print("WebUI Pod'u kapatıldı.")
        else:
            print("Açık WebUI Pod'u yok.")
        return
    if matches:
        wait_webui(matches[0]["id"], not options.no_browser)
        return
    if request(f"{API}/global-volumes/{FLEX}", key=key).get("id") != FLEX:
        raise RuntimeError("Mevcut Flex diski doğrulanamadı.")
    image = current_image()
    gpus = choose_gpu(options.max_usd)
    if not gpus:
        raise RuntimeError("Fiyat sınırı içinde boş ve uyumlu GPU bulunamadı.")
    env = {"POD_FLEX_PREFIX": NAME, "WEBUI_DATA_ROOT": DATA_PATH, "WEBUI_BOOTSTRAP_USER": "user"}
    if not options.restore:
        password = getpass.getpass("İlk kullanım için WebUI parolası (en az 8 karakter): ")
        if len(password) < 8:
            raise RuntimeError("Parola en az 8 karakter olmalı. Mevcut hesap için --restore kullanın.")
        env["WEBUI_BOOTSTRAP_PASSWORD"] = password
    for gpu in gpus:
        gpu_id, rate = gpu["gpuId"], gpu["securePricePerHr"]
        print(f"Deneniyor: {gpu_id}, {rate:.2f} USD/saat", flush=True)
        body = {
            "name": NAME, "templateId": TEMPLATE_ID, "image": image,
            "cloud": "SECURE", "disk": 120,
            "gpu": {"id": gpu_id, "count": 1, "minCudaVersion": "12.8"},
            "ports": ["8189/http", "8192/http"], "env": env,
            "mounts": {"global": [{"volumeId": FLEX, "path": "/workspace"}]},
        }
        try:
            pod = request(f"{API}/pods", "POST", body, key)
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            message = json.loads(exc.read()).get("detail", "").lower()
            if "no longer any instances" not in message and "no instances" not in message:
                raise RuntimeError("RunPod, Pod ayarlarını reddetti (HTTP 400).") from None
            continue
        if not any(mount.get("volumeId") == FLEX
                   for mount in (pod.get("mounts") or {}).get("global", [])):
            request(f"{API}/pods/{pod['id']}", method="DELETE", key=key)
            raise RuntimeError("Flex bağlı görünmediği için Pod kapatıldı.")
        wait_webui(pod["id"], not options.no_browser)
        return
    raise RuntimeError("Uygun GPU'lar deneme sırasında doldu. Biraz sonra tekrar deneyin.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"RunPod isteği tamamlanamadı (HTTP {error.code}).") from None
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        raise SystemExit(str(error)) from None
