from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import shutil
import sys

from packaging.markers import default_environment
from packaging.requirements import Requirement


ROOT_DISTRIBUTIONS = ("bilibili-cli", "pyzhihu-cli", "xiaohongshu-cli", "click")
EXCLUDED_DISTRIBUTIONS = {
    # Login-only or terminal-rendering extras that stream-curator does not need in release.
    "browser-cookie3",
    "markdown-it-py",
    "mdurl",
    "pillow",
    "pygments",
    "pywin32",
    "qrcode",
    "qrcode-terminal",
    "shadowcopy",
    "wmi",
}
EDITABLE_PACKAGE_SPECS = {
    "bilibili-cli": {"repo_dir": "bilibili-cli", "packages": ("bili_cli",)},
    "pyzhihu-cli": {"repo_dir": "zhihu-cli", "packages": ("zhihu_cli",)},
    "xiaohongshu-cli": {"repo_dir": "xiaohongshu-cli", "packages": ("xhs_cli",)},
}
STDLIB_PRUNE_DIRS = {
    "__phello__",
    "__pycache__",
    "ensurepip",
    "idlelib",
    "lib2to3",
    "site-packages",
    "test",
    "tests",
    "tkinter",
    "turtledemo",
    "venv",
}
STDLIB_PRUNE_EXTRA_NAMES = {
    "distutils",
    "msilib",
    "pydoc_data",
    "unittest",
}
COPY_SKIP_DIRS = {"__pycache__", ".pytest_cache", "tests", "test"}
COPY_SKIP_SUFFIXES = {".pyc", ".pyo", ".pdb", ".pyi", ".pxd", ".pxi", ".pyx", ".h", ".c"}
LIBRARY_BIN_EXCLUDE_PATTERNS = (
    "bzip2.exe",
    "lzmadec.exe",
    "lzmainfo.exe",
    "openssl.exe",
    "sqlite3.exe",
    "tcl*.dll",
    "tclsh*.exe",
    "tk*.dll",
    "wish*.exe",
    "xz.exe",
    "xzdec.exe",
)
LIBRARY_BIN_EXACT_EXCLUDE = {
    "api-ms-win-core-console-l1-1-0.dll",
    "api-ms-win-core-file-l1-1-0.dll",
    "api-ms-win-core-heap-l1-1-0.dll",
    "api-ms-win-core-handle-l1-1-0.dll",
    "api-ms-win-core-interlocked-l1-1-0.dll",
    "api-ms-win-core-libraryloader-l1-1-0.dll",
    "api-ms-win-core-processenvironment-l1-1-0.dll",
    "api-ms-win-core-processthreads-l1-1-0.dll",
    "api-ms-win-core-processthreads-l1-1-1.dll",
    "api-ms-win-core-profile-l1-1-0.dll",
    "api-ms-win-core-rtlsupport-l1-1-0.dll",
    "api-ms-win-core-string-l1-1-0.dll",
    "api-ms-win-crt-conio-l1-1-0.dll",
    "api-ms-win-crt-convert-l1-1-0.dll",
    "api-ms-win-crt-environment-l1-1-0.dll",
    "api-ms-win-crt-filesystem-l1-1-0.dll",
    "api-ms-win-crt-heap-l1-1-0.dll",
    "api-ms-win-crt-locale-l1-1-0.dll",
    "api-ms-win-crt-math-l1-1-0.dll",
    "api-ms-win-crt-multibyte-l1-1-0.dll",
    "api-ms-win-crt-private-l1-1-0.dll",
    "api-ms-win-crt-process-l1-1-0.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll",
    "api-ms-win-crt-stdio-l1-1-0.dll",
    "api-ms-win-crt-string-l1-1-0.dll",
    "api-ms-win-crt-time-l1-1-0.dll",
    "api-ms-win-crt-utility-l1-1-0.dll",
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "ucrtbase.dll",
    "vccorlib140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
    "zlib.dll",
}
DLLS_EXCLUDE_PATTERNS = (
    "_ctypes_test.pyd",
    "_msi.pyd",
    "_test*.pyd",
    "_tkinter.pyd",
    "py.ico",
    "pyc.ico",
    "winsound.pyd",
    "xxlimited*.pyd",
)
ROOT_FILE_PATTERNS = (
    "python.exe",
    "python3.dll",
    "python311.dll",
    "vcruntime*.dll",
    "msvcp140*.dll",
    "concrt140.dll",
    "vccorlib140.dll",
    "ucrtbase.dll",
    "zlib.dll",
    "api-ms-win-*.dll",
    "LICENSE_PYTHON.txt",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a slim portable Python runtime for stream-curator.")
    parser.add_argument("--source-env-root", required=True, type=Path)
    parser.add_argument("--target-env-root", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    return parser.parse_args()


def normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def should_skip_path(path: Path) -> bool:
    if path.suffix.lower() in COPY_SKIP_SUFFIXES:
        return True
    for part in path.parts:
        if part == "..":
            return True
        if part in COPY_SKIP_DIRS:
            return True
    return False


def copy_file(src: Path, dst: Path) -> None:
    if should_skip_path(src):
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree_filtered(src: Path, dst: Path) -> None:
    if should_skip_path(src):
        return
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        if should_skip_path(child):
            continue
        target = dst / child.name
        if child.is_dir():
            copy_tree_filtered(child, target)
        else:
            copy_file(child, target)


def copy_root_files(source_env_root: Path, target_env_root: Path) -> None:
    copied = set()
    for pattern in ROOT_FILE_PATTERNS:
        for src in source_env_root.glob(pattern):
            if src.is_file() and src.name not in copied:
                copy_file(src, target_env_root / src.name)
                copied.add(src.name)


def copy_stdlib(source_env_root: Path, target_env_root: Path) -> None:
    source_lib = source_env_root / "Lib"
    target_lib = target_env_root / "Lib"
    target_lib.mkdir(parents=True, exist_ok=True)
    for child in source_lib.iterdir():
        if child.name in STDLIB_PRUNE_DIRS:
            continue
        if child.name in STDLIB_PRUNE_EXTRA_NAMES:
            continue
        target = target_lib / child.name
        if child.is_dir():
            copy_tree_filtered(child, target)
        else:
            copy_file(child, target)


def copy_dll_dirs(source_env_root: Path, target_env_root: Path) -> None:
    source_dlls = source_env_root / "DLLs"
    if source_dlls.exists():
        target_dlls = target_env_root / "DLLs"
        target_dlls.mkdir(parents=True, exist_ok=True)
        excluded_dlls: set[Path] = set()
        for pattern in DLLS_EXCLUDE_PATTERNS:
            excluded_dlls.update(path.resolve() for path in source_dlls.glob(pattern))
        for child in source_dlls.iterdir():
            if child.resolve() in excluded_dlls:
                continue
            target = target_dlls / child.name
            if child.is_dir():
                copy_tree_filtered(child, target)
            else:
                copy_file(child, target)
    source_library_bin = source_env_root / "Library" / "bin"
    if source_library_bin.exists():
        target_library_bin = target_env_root / "Library" / "bin"
        target_library_bin.mkdir(parents=True, exist_ok=True)
        excluded_paths: set[Path] = set()
        for pattern in LIBRARY_BIN_EXCLUDE_PATTERNS:
            excluded_paths.update(path.resolve() for path in source_library_bin.glob(pattern))
        for child in source_library_bin.iterdir():
            if child.resolve() in excluded_paths:
                continue
            if child.name in LIBRARY_BIN_EXACT_EXCLUDE:
                continue
            target = target_library_bin / child.name
            if child.is_dir():
                copy_tree_filtered(child, target)
            else:
                copy_file(child, target)


def build_distribution_index(site_packages: Path) -> dict[str, metadata.Distribution]:
    index: dict[str, metadata.Distribution] = {}
    for dist in metadata.distributions(path=[str(site_packages)]):
        name = dist.metadata.get("Name")
        if not name:
            continue
        index[normalize_dist_name(name)] = dist
    return index


def resolve_dependency_closure(site_packages: Path) -> dict[str, metadata.Distribution]:
    env = default_environment()
    env["extra"] = ""
    index = build_distribution_index(site_packages)
    resolved: dict[str, metadata.Distribution] = {}
    pending = [normalize_dist_name(name) for name in ROOT_DISTRIBUTIONS]

    while pending:
        name = pending.pop()
        if name in EXCLUDED_DISTRIBUTIONS:
            continue
        if name in resolved:
            continue
        dist = index.get(name)
        if dist is None:
            raise RuntimeError(f"Missing required distribution in source env: {name}")
        resolved[name] = dist
        for raw_requirement in dist.requires or []:
            requirement = Requirement(raw_requirement)
            if requirement.marker and not requirement.marker.evaluate(env):
                continue
            dependency_name = normalize_dist_name(requirement.name)
            if dependency_name in EXCLUDED_DISTRIBUTIONS:
                continue
            pending.append(dependency_name)
    return resolved


def copy_distribution_files(
    distributions: dict[str, metadata.Distribution],
    target_site_packages: Path,
) -> None:
    for normalized_name, dist in sorted(distributions.items()):
        if normalized_name in EDITABLE_PACKAGE_SPECS:
            continue
        for package_path in dist.files or ():
            relative_path = Path(package_path)
            if should_skip_path(relative_path):
                continue
            source_path = Path(dist.locate_file(package_path))
            if not source_path.exists():
                continue
            target_path = target_site_packages / relative_path
            if source_path.is_dir():
                copy_tree_filtered(source_path, target_path)
            else:
                copy_file(source_path, target_path)


def copy_editable_packages(repo_root: Path, target_site_packages: Path) -> None:
    workspace_root = repo_root.parent
    for spec in EDITABLE_PACKAGE_SPECS.values():
        source_repo = workspace_root / spec["repo_dir"]
        if not source_repo.exists():
            raise RuntimeError(f"Editable package repo not found: {source_repo}")
        for package_dir_name in spec["packages"]:
            source_package_dir = source_repo / package_dir_name
            if not source_package_dir.exists():
                raise RuntimeError(f"Package directory not found: {source_package_dir}")
            copy_tree_filtered(source_package_dir, target_site_packages / package_dir_name)


def patch_copied_packages(target_site_packages: Path) -> None:
    bilibili_init = target_site_packages / "bilibili_api" / "__init__.py"
    if bilibili_init.exists():
        text = bilibili_init.read_text(encoding="utf-8")
        original = "from .utils.picture import Picture"
        replacement = (
            "try:\n"
            "    from .utils.picture import Picture\n"
            "except ModuleNotFoundError:\n"
            "    class Picture:\n"
            "        pass"
        )
        if original in text:
            bilibili_init.write_text(text.replace(original, replacement, 1), encoding="utf-8")

    bilibili_picture = target_site_packages / "bilibili_api" / "utils" / "picture.py"
    if bilibili_picture.exists():
        text = bilibili_picture.read_text(encoding="utf-8")
        original = "from PIL import Image"
        replacement = (
            "def _image_module():\n"
            "    from PIL import Image\n"
            "    return Image"
        )
        if original in text:
            text = text.replace(original, replacement, 1)
        text = text.replace("Image.open(", "_image_module().open(")
        bilibili_picture.write_text(text, encoding="utf-8")

    bilibili_login = target_site_packages / "bilibili_api" / "login_v2.py"
    if bilibili_login.exists():
        text = bilibili_login.read_text(encoding="utf-8")
        text = text.replace(
            "import qrcode\nimport qrcode_terminal\n",
            "def _qrcode_module():\n"
            "    import qrcode\n"
            "    return qrcode\n\n"
            "def _qrcode_terminal_module():\n"
            "    import qrcode_terminal\n"
            "    return qrcode_terminal\n",
            1,
        )
        text = text.replace("qr = qrcode.QRCode()", "qr = _qrcode_module().QRCode()")
        text = text.replace(
            "self.__qr_terminal = qrcode_terminal.qr_terminal_str(self.__qr_link)",
            "self.__qr_terminal = _qrcode_terminal_module().qr_terminal_str(self.__qr_link)",
        )
        bilibili_login.write_text(text, encoding="utf-8")


def runtime_size_mb(path: Path) -> float:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return round(total / (1024 * 1024), 2)


def main() -> int:
    args = parse_args()
    source_env_root = args.source_env_root.resolve()
    target_env_root = args.target_env_root.resolve()
    repo_root = args.repo_root.resolve()
    source_site_packages = source_env_root / "Lib" / "site-packages"
    target_site_packages = target_env_root / "Lib" / "site-packages"

    if not (source_env_root / "python.exe").exists():
        raise RuntimeError(f"python.exe not found in source env: {source_env_root}")
    if not source_site_packages.exists():
        raise RuntimeError(f"site-packages not found in source env: {source_site_packages}")

    ensure_clean_dir(target_env_root)
    copy_root_files(source_env_root, target_env_root)
    copy_stdlib(source_env_root, target_env_root)
    copy_dll_dirs(source_env_root, target_env_root)
    target_site_packages.mkdir(parents=True, exist_ok=True)

    distributions = resolve_dependency_closure(source_site_packages)
    copy_distribution_files(distributions, target_site_packages)
    copy_editable_packages(repo_root, target_site_packages)
    patch_copied_packages(target_site_packages)

    print(f"Built slim runtime at: {target_env_root}")
    print(f"Bundled distributions: {len(distributions)}")
    print(f"Runtime size: {runtime_size_mb(target_env_root)} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
