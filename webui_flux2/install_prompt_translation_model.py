"""Install the pinned, Apache-2.0 OPUS-MT Turkish-to-English model locally."""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import snapshot_download


REPO = "Helsinki-NLP/opus-mt-tr-en"
REVISION = "19c65427cc2af5f191337d4899e0348c4af25902"
WEIGHTS_SHA256 = "e2b820f31c9348b0ae33d77ac23bd1686fa60a8afa0b9511bc7d9a35c125b534"
FILES = ["config.json", "generation_config.json", "pytorch_model.bin",
         "source.spm", "target.spm", "tokenizer_config.json", "vocab.json"]
BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models" / "opus_mt_tr_en"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    if MODEL_DIR.exists():
        weights = MODEL_DIR / "pytorch_model.bin"
        if (all((MODEL_DIR / name).is_file() for name in FILES)
                and _sha256(weights) == WEIGHTS_SHA256):
            print("Translation model is already installed and verified.")
            return
        raise RuntimeError("Model directory exists but is incomplete or unverified; refusing to overwrite it")

    MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix=".opus-mt-download-", dir=str(MODEL_DIR.parent)) as temp:
        snapshot = Path(snapshot_download(
            repo_id=REPO, revision=REVISION, local_dir=os.path.join(temp, "snapshot"),
            allow_patterns=FILES))
        weights = snapshot / "pytorch_model.bin"
        if not weights.is_file() or _sha256(weights) != WEIGHTS_SHA256:
            raise RuntimeError("Published OPUS-MT SHA-256 verification failed")
        if MODEL_DIR.exists():
            raise RuntimeError("Model directory appeared during download; refusing to overwrite it")
        shutil.move(str(snapshot), str(MODEL_DIR))
    print("Installed and verified", REPO, "at", MODEL_DIR)


if __name__ == "__main__":
    main()
