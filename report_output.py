"""Confined report staging and manifests for single files and new output bundles."""
import hashlib
from pathlib import Path


def manifest(folder):
    files = []
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise ValueError('Generated report links cannot escape the staging directory')
        if path.is_file():
            files.append({'path': str(path.relative_to(folder)), 'size': path.stat().st_size,
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return files


def publish_bundle(stage, destination, main_name):
    # Publish companions before the entry point. Only new bundles are supported:
    # existing directories are preserved and never recursively replaced/deleted.
    files = manifest(stage)
    destination.mkdir()
    created_files, created_dirs = [], [destination]
    try:
        for item in sorted(files, key=lambda item: (item['path'] == main_name, item['path'])):
            relative = Path(item['path'])
            source, target = stage / relative, destination / relative
            parent = target.parent
            missing = []
            while parent != destination and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            import os
            os.link(source, target)
            stat = target.stat()
            created_files.append((target, stat.st_dev, stat.st_ino, item['sha256']))
    except Exception:
        for path, device, inode, digest in reversed(created_files):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                if (stat.st_dev, stat.st_ino) == (device, inode) and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                    path.unlink()
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass  # Preserve any intervening files rather than deleting them.
        raise
    return files
