import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone


BACKUP_FILENAME_RE = re.compile(r'^backup_\d{4}_\d{2}_\d{2}_\d{6}\.json$')


@dataclass(frozen=True)
class BackupFile:
    filename: str
    created_at: object
    size_bytes: int
    size_display: str


def get_backup_root():
    backup_root = Path(settings.BACKUP_ROOT)
    backup_root.mkdir(mode=0o750, parents=True, exist_ok=True)
    return backup_root


def is_valid_backup_filename(filename):
    return bool(BACKUP_FILENAME_RE.match(Path(filename).name))


def get_backup_path(filename):
    safe_filename = Path(filename).name
    if filename != safe_filename or not is_valid_backup_filename(safe_filename):
        return None

    backup_root = get_backup_root().resolve()
    backup_path = (backup_root / safe_filename).resolve()
    if backup_root not in backup_path.parents:
        return None
    if not backup_path.exists() or not backup_path.is_file():
        return None
    return backup_path


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def list_backups():
    backups = []
    for path in get_backup_root().iterdir():
        if not path.is_file() or not is_valid_backup_filename(path.name):
            continue
        stat = path.stat()
        backups.append(BackupFile(
            filename=path.name,
            created_at=timezone.datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone()),
            size_bytes=stat.st_size,
            size_display=format_size(stat.st_size),
        ))
    return sorted(backups, key=lambda backup: backup.created_at, reverse=True)


def create_database_backup():
    now = timezone.localtime()
    filename = f"backup_{now:%Y_%m_%d_%H%M%S}.json"
    backup_path = get_backup_root() / filename
    with backup_path.open('w', encoding='utf-8') as output:
        call_command(
            'dumpdata',
            '--natural-foreign',
            '--natural-primary',
            '--indent',
            '2',
            stdout=output,
        )
    backup_path.chmod(0o640)
    return backup_path


def delete_backup_file(filename):
    backup_path = get_backup_path(filename)
    if backup_path is None:
        return False
    backup_path.unlink()
    return True
