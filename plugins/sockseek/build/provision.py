"""Install just the checksum-pinned Linux Sockseek executable at build time."""

import hashlib
import json
import shutil
import tarfile
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

PIN = Path(__file__).with_name("sockseek-pin.json")
DESTINATION = Path("/opt/sockseek/sockseek")
ARCHIVE_LIMIT = 64 * 1024 * 1024
EXECUTABLE_LIMIT = 256 * 1024 * 1024

if __name__ == "__main__":
    pin = json.loads(PIN.read_text())
    with TemporaryDirectory() as staging:
        archive = Path(staging) / "archive.tar.gz"
        digest = hashlib.sha256()
        length = 0
        with urllib.request.urlopen(pin["url"], timeout=60) as response, archive.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                length += len(chunk)
                if length > ARCHIVE_LIMIT:
                    raise ValueError("The pinned archive is too large.")
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != pin["sha256"]:
            raise ValueError("The Sockseek archive checksum does not match.")
        with tarfile.open(archive, "r:gz") as bundle:
            member = next(item for item in bundle if item.isfile() and Path(item.name).name == "sockseek")
            if not 0 < member.size <= EXECUTABLE_LIMIT:
                raise ValueError("The pinned executable has an unexpected size.")
            DESTINATION.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, DESTINATION.open("xb") as binary:
                shutil.copyfileobj(source, binary)
            DESTINATION.chmod(0o755)
