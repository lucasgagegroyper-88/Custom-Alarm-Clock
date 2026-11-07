"""Create a clean source ZIP with no media, databases, caches or development environment."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib

root = Path(__file__).resolve().parent.parent
output = root.parent / 'CustomAlarmClock_FINAL_Optimized.zip'
exclude_dirs = {'.venv', 'venv', '__pycache__', '.pytest_cache', '.ruff_cache', 'build', 'dist', '.git'}
exclude_extensions = {'.pyc', '.sqlite3', '.db', '.log', '.gif', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.ico', '.svg', '.mp3', '.wav', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wma', '.aiff', '.aif', '.exe'}
with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink() or any(part in exclude_dirs or part.startswith('pytest-of-') for part in relative.parts):
            continue
        if ((path.suffix.lower() in exclude_extensions and relative != Path('resources/CustomClock.ico'))
                or path.name == 'core' or path.name.startswith('core.') or path.name.endswith(('-wal', '-shm', '-journal'))):
            continue
        archive.write(path, Path(root.name) / relative)
print(output)
print('Bytes:', output.stat().st_size)
print('SHA256:', hashlib.sha256(output.read_bytes()).hexdigest())
