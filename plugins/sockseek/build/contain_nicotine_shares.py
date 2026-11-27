"""Confine the pinned Nicotine+ scanner and uploader to the sharing mount."""

from importlib.util import find_spec
from pathlib import Path

package = Path(find_spec("pynicotine").origin).parent


def patch(filename, replacements):
    target = package / filename
    source = target.read_text(encoding="utf-8")
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError(f"The pinned Nicotine+ code changed in {filename}: {before!r}")
        source = source.replace(before, after, 1)
    target.write_text(source, encoding="utf-8")


patch("shares.py", [
    ("from pynicotine.external.tinytag import TinyTag, UnsupportedFormatError",
     "from pynicotine.external.tinytag import TinyTag, UnsupportedFormatError\n"
     "from plugin.share_files import open_share_file, scan_share_folder"),
    ("with os.scandir(encode_path(folder_path, prefix=False)) as entries:",
     "with scan_share_folder(folder_path) as entries:"),
    ('basename = entry.name.decode("utf-8", "replace")',
     'basename = os.fsencode(entry.name).decode("utf-8", "replace")'),
    ("if entry.is_dir():", "if entry.is_dir(follow_symlinks=False):"),
    ("                            file_stat = entry.stat()",
     "                            file_stat = entry.stat(follow_symlinks=False)\n"
     "                            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:\n"
     "                                continue"),
    ("""            tag = TinyTag.get(
                encode_path(file_path), tags=False, duration=True, image=False,
                check_magic_bytes=False
            )""",
     """            with open_share_file(file_path) as source:
                tag = TinyTag.get(
                    encode_path(file_path), file_obj=source, tags=False, duration=True,
                    image=False, check_magic_bytes=False
                )"""),
])

patch("uploads.py", [
    ("from pynicotine.utils import encode_path",
     "from plugin.share_files import open_share_file, share_file_size"),
    ("os.path.getsize(encode_path(file_path))", "share_file_size(file_path)"),
    ("if os.access(encode_path(real_path), os.R_OK):\n                return True",
     "with open_share_file(real_path):\n                return True"),
    ('open(encode_path(real_path), "rb")', 'open_share_file(real_path)'),
])
