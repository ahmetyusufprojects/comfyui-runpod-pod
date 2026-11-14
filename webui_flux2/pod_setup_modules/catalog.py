"""RunPod setup catalog operations.

Shared runtime state and public compatibility API live in pod_setup.
"""

import pod_setup as _ctx

def _validate_catalog():
    """Fail early when a model link, target path, or workflow mapping is invalid."""
    if not isinstance(_ctx.CATALOG, dict) or not _ctx.CATALOG:
        raise RuntimeError("Model listesi boş veya bozuk.")
    targets = {}
    for group, item in _ctx.CATALOG.items():
        if not _ctx.re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", group):
            raise RuntimeError("Model grubu adı geçersiz.")
        if item.get("engine") not in ("comfy", "llama", "translation"):
            raise RuntimeError(f"{group}: desteklenmeyen çalışma ortamı.")
        if not isinstance(item.get("label"), str) or not item.get("label"):
            raise RuntimeError(f"{group}: görünen ad eksik.")
        custom_nodes = item.get("custom_nodes") or []
        if not isinstance(custom_nodes, list):
            raise RuntimeError(f"{group}: eklenti listesi geçersiz.")
        for node in custom_nodes:
            if (not isinstance(node, dict)
                    or not _ctx.re.fullmatch(r"[a-z0-9_-]{1,64}", str(node.get("name") or ""))
                    or not _ctx.re.fullmatch(r"[0-9a-f]{40}", str(node.get("revision") or ""))
                    or not _ctx.re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
                                        str(node.get("repo") or ""))):
                raise RuntimeError(f"{group}: eklenti kaynağı geçersiz.")
        files = item.get("files")
        if not isinstance(files, list) or not files:
            raise RuntimeError(f"{group}: dosya listesi boş.")
        for file in files:
            directory, name = file.get("dir"), file.get("name")
            if (not isinstance(directory, str) or not isinstance(name, str)
                    or not directory or not name
                    or _ctx.Path(directory).is_absolute() or _ctx.Path(name).name != name
                    or ".." in _ctx.Path(directory).parts):
                raise RuntimeError(f"{group}: hedef dosya yolu geçersiz.")
            target = (directory, name)
            source_type = file.get("source") or "hf"
            source = (source_type, file.get("repo"), file.get("url"),
                      file.get("revision"), file.get("path"), file.get("version"),
                      file.get("host"), file.get("sha256"))
            if target in targets and targets[target] != source:
                raise RuntimeError(f"{group}: ortak dosyanın kaynağı gruplar arasında farklı.")
            targets[target] = source
            if source_type not in ("hf", "civitai", "url"):
                raise RuntimeError(f"{group}: desteklenmeyen indirme kaynağı.")
            if source_type == "civitai":
                if not isinstance(file.get("version"), int):
                    raise RuntimeError(f"{group}: Civitai sürüm numarası eksik.")
                if file.get("host", "civitai.com") not in _ctx._CIVITAI_HOSTS:
                    raise RuntimeError(f"{group}: Civitai alan adı geçersiz.")
            elif source_type == "url":
                parsed = _ctx.urllib.parse.urlsplit(file.get("url") or "")
                if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                        or parsed.password or parsed.fragment):
                    raise RuntimeError(f"{group}: doğrudan indirme bağlantısı güvenli HTTPS olmalı.")
            elif not all(isinstance(file.get(key), str) and file.get(key)
                         for key in ("repo", "revision", "path")):
                raise RuntimeError(f"{group}: Hugging Face deposu, sürümü veya yolu eksik.")
            digest = file.get("sha256")
            if digest is not None and not _ctx.re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise RuntimeError(f"{group}: SHA-256 biçimi geçersiz.")
        dependencies = item.get("depends_on", [])
        if not isinstance(dependencies, list) or any(dep not in _ctx.CATALOG for dep in dependencies):
            raise RuntimeError(f"{group}: bağımlılık listesi geçersiz.")

    def visit(group, visited, active):
        if group in active:
            raise RuntimeError("Model listesinde döngüsel bağımlılık var.")
        if group in visited:
            return
        active.add(group)
        for dependency in _ctx.CATALOG[group].get("depends_on", []):
            visit(dependency, visited, active)
        active.remove(group)
        visited.add(group)

    visited = set()
    for group in _ctx.CATALOG:
        visit(group, visited, set())
    for name, workflow in _ctx.WORKFLOWS.items():
        if workflow.get("group") not in _ctx.CATALOG:
            raise RuntimeError(f"{name}: iş akışının model grubu bulunamadı.")
        for profile in (workflow.get("profiles") or {}).values():
            if profile.get("group") not in _ctx.CATALOG:
                raise RuntimeError(f"{name}: profil grubu bulunamadı.")


def _group_files_complete(group):
    item = _ctx.CATALOG[group]
    return all(file.get("optional") or
               _ctx._model_present(_ctx.MODELS / file["dir"] / file["name"])
               for file in item["files"])


def _group_files_verified(group):
    item = _ctx.CATALOG[group]
    return all(file.get("optional") and
               not _ctx._model_present(_ctx.MODELS / file["dir"] / file["name"])
               or _ctx._model_verified(_ctx.MODELS / file["dir"] / file["name"], file)
               for file in item["files"])


def _engine_ready(group):
    item = _ctx.CATALOG[group]
    if not _ctx._group_files_verified(group):
        return False
    if item["engine"] == "comfy":
        return (_ctx._comfy_process is not None and _ctx._comfy_process.poll() is None
                and (_ctx.ROOT / "comfy-venv/bin/python").is_file())
    if item["engine"] == "llama":
        if not _ctx._runtime_available(group):
            return False
        return bool(_ctx.re.search(r"(?m)^\s*CUDA0:", _ctx._llama_cuda_devices()))
    return (_ctx.COMFY / ".pod-ready").is_file()


def _installation_ready(group):
    """Check installed files and runtime without requiring a live model server."""
    if not _ctx._group_files_verified(group):
        return False
    if _ctx.CATALOG[group]["engine"] in ("comfy", "translation"):
        try:
            return (_ctx.COMFY / ".pod-ready").read_text(encoding="utf-8").strip() == _ctx.COMFY_REF
        except OSError:
            return False
    return _ctx._runtime_available(group)


def _runtime_available(group):
    item = _ctx.CATALOG[group]
    if item["engine"] == "comfy":
        return (_ctx._comfy_process is not None and _ctx._comfy_process.poll() is None
                and (_ctx.ROOT / "comfy-venv/bin/python").is_file())
    if item["engine"] == "llama":
        marker = _ctx.ROOT / "llama/.pod-ready"
        try:
            return marker.read_text(encoding="ascii").strip() == _ctx.LLAMA_TAG and _ctx.Path(
                _ctx.os.environ["LLAMA_SERVER_PATH"]).is_file()
        except (OSError, KeyError):
            return False
    return (_ctx.COMFY / ".pod-ready").is_file()


def _transitive_dependents(group):
    result = {group}
    changed = True
    while changed:
        changed = False
        for candidate, item in _ctx.CATALOG.items():
            if candidate not in result and any(dep in result for dep in item.get("depends_on", [])):
                result.add(candidate)
                changed = True
    return result


def is_ready():
    return _ctx._ready


def workflow(name):
    return dict(_ctx.WORKFLOWS.get(name) or {})


def workflow_enabled(name):
    group = _ctx.WORKFLOWS.get(name, {}).get("group")
    return bool(group in _ctx.CATALOG and _ctx.CATALOG[group].get("enabled", True))


def workflow_ready(name):
    group = _ctx.WORKFLOWS.get(name, {}).get("group")
    return _ctx.workflow_enabled(name) and _ctx._engine_ready(group)


def _bagimlilik_sirasi(group, gorulen=None):
    gorulen = gorulen or set()
    if group in gorulen:
        return []
    gorulen.add(group)
    result = []
    for dependency in _ctx.CATALOG[group].get("depends_on", []):
        result.extend(_ctx._bagimlilik_sirasi(dependency, gorulen))
    result.append(group)
    return result
