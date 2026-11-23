"""Resolve document family references against the current render's validated faces."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import re
import shutil

from app import publication


_SLOT = r"([0-9a-f]{64}|none)"
_FAMILY = re.compile(rf"font-{_SLOT}-{_SLOT}")
_assets = ContextVar("document_font_assets", default=None)


def is_font_family(value):
    return isinstance(value, str) and (value in publication.FONT_LABELS or
                                       value != "font-none-none" and _FAMILY.fullmatch(value) is not None)


@contextmanager
def use_document_fonts(assets: dict[str, bytes]):
    """Bind digest-keyed, PDF-permitted bytes for measurement and all replay passes."""
    token = _assets.set(assets.copy())
    try:
        yield
    finally:
        _assets.reset(token)


def _face_filename(digest, installed_dir, assets, files):
    from app.font_assets import FONT_ASSETS, LOCKED_ASSET_PATHS

    if digest in FONT_ASSETS["faces"]:
        name = LOCKED_ASSET_PATHS[digest].name
        with (installed_dir / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == digest:
                return name
    if digest in assets:
        data = assets[digest]
        extension = "otf" if data.startswith(b"OTTO") else "ttf"
        name = f"doc-{digest}.{extension}"
        files[name] = data
        return name
    return None


def prepare_fonts(style, policy, destination, installed_dir, *, copy_installed):
    """Add selected families to this job's policy and materialize only the referenced assets."""
    families = {style.body_font, style.heading_font, publication.RUNNING_DEFAULTS["font"]}
    families.update(row.font for row in style.resolved_list_levels())
    families.update(dict(row.values).get("font") for row in style.section_rules or ())
    families.update(dict(fields).get("font") for _, fields in style.text_styles or ())

    defaults = policy["fonts"][publication.DEFAULTS["body_font"]]
    assets = _assets.get() or {}
    files = {}
    resolved = {"none": None}
    for family in families - {None} - policy["fonts"].keys():
        regular, bold = _FAMILY.fullmatch(family).groups()
        pair = {}
        for weight, digest in (("regular", regular), ("bold", bold)):
            if digest not in resolved:
                resolved[digest] = _face_filename(digest, installed_dir, assets, files)
            pair[weight] = resolved[digest] or defaults[weight]
        policy["fonts"][family] = pair

    if not files and not copy_installed:
        destination.symlink_to(installed_dir.resolve(strict=True), target_is_directory=True)
    else:
        destination.mkdir()
        required = {name for pair in policy["fonts"].values() for name in pair.values()}
        for name in required - files.keys():
            shutil.copyfile(installed_dir / name, destination / name)
        for name, data in files.items():
            (destination / name).write_bytes(data)
    return bool(files)
