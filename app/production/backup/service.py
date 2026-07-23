"""BackupService: respaldos automáticos y manuales con verificación (Fase 9).

Respalda el **estado crítico** del motor (auditoría, journal, snapshots,
registro de modelos, aprobaciones) en un archivo comprimido con un manifiesto
que incluye un SHA-256 del contenido. La verificación recomputa ese hash, así un
respaldo corrupto se detecta antes de necesitarlo —un backup que no se puede
restaurar no es un backup—.

No respalda la base de datos PostgreSQL directamente (eso es responsabilidad de
``pg_dump`` en el runbook de infraestructura); respalda el estado en disco que
el motor genera y necesita para no arrancar de cero. Las operaciones pesadas se
ejecutan en un hilo para no bloquear el bucle asíncrono.
"""

import asyncio
import hashlib
import json
import logging
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.settings import BackupSettings
from app.production.audit import AuditAction, AuditLog
from app.utils.time import utc_now

_log = logging.getLogger("app.production.backup")
_MANIFEST_SUFFIX = ".manifest.json"


@dataclass(frozen=True, slots=True)
class BackupRecord:
    """Metadata about one backup archive.

    Attributes:
        backup_id: Stable identifier (also the archive stem).
        created_at: ISO-8601 UTC creation time.
        kind: ``manual`` or ``scheduled``.
        path: Archive path.
        size_bytes: Archive size.
        sha256: Content hash used for verification.
        files: Source paths included.
    """

    backup_id: str
    created_at: str
    kind: str
    path: str
    size_bytes: int
    sha256: str
    files: list[str]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "kind": self.kind,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "files": list(self.files),
        }


class BackupService:
    """Create, verify, rotate and restore state backups.

    Args:
        settings: Backup configuration.
        audit: Audit log (creation/restore/rotation is always recorded).
    """

    def __init__(self, settings: BackupSettings, audit: AuditLog | None = None) -> None:
        self._settings = settings
        self._audit = audit

    # ------------------------------------------------------------------
    # Creación
    # ------------------------------------------------------------------

    def create(self, *, kind: str = "manual") -> BackupRecord:
        """Create a backup archive of every configured source (blocking).

        Args:
            kind: ``manual`` or ``scheduled``.

        Returns:
            The created backup's record.
        """
        backup_dir = self._settings.backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        backup_id = f"backup_{stamp}"
        compress = self._settings.compress
        suffix = ".tar.gz" if compress else ".tar"
        archive = backup_dir / f"{backup_id}{suffix}"

        # Modo literal por rama: tarfile.open exige un Literal, no un str variable,
        # y el open va directo en el ``with`` (context manager).
        if compress:
            with tarfile.open(archive, "w:gz") as tar:
                included = self._add_sources(tar)
        else:
            with tarfile.open(archive, "w") as tar:
                included = self._add_sources(tar)

        digest = _sha256(archive)
        record = BackupRecord(
            backup_id=backup_id,
            created_at=utc_now().isoformat(),
            kind=kind,
            path=str(archive),
            size_bytes=archive.stat().st_size,
            sha256=digest,
            files=included,
        )
        (backup_dir / f"{backup_id}{_MANIFEST_SUFFIX}").write_text(
            json.dumps(record.to_dict(), indent=2), encoding="utf-8"
        )
        _log.info("Backup created: %s (%d bytes)", backup_id, record.size_bytes)

        if self._settings.verify_on_create and not self._verify_record(record):
            _log.error("Backup %s failed verification right after creation", backup_id)

        if self._audit is not None:
            self._audit.record(
                action=AuditAction.BACKUP_CREATED,
                actor="system" if kind == "scheduled" else "operator",
                target=backup_id,
                after=record.to_dict(),
            )
        self.rotate()
        return record

    def _add_sources(self, tar: tarfile.TarFile) -> list[str]:
        """Add every existing configured source to the archive."""
        included: list[str] = []
        for source in self._settings.sources:
            if not source.exists():
                continue
            tar.add(source, arcname=source.name)
            included.append(str(source))
        return included

    async def acreate(self, *, kind: str = "manual") -> BackupRecord:
        """Async wrapper around :meth:`create` (runs in a worker thread)."""
        return await asyncio.to_thread(self.create, kind=kind)

    # ------------------------------------------------------------------
    # Listado / verificación
    # ------------------------------------------------------------------

    def list_backups(self) -> list[BackupRecord]:
        """Return every backup with a manifest, newest first."""
        backup_dir = self._settings.backup_dir
        if not backup_dir.exists():
            return []
        records: list[BackupRecord] = []
        for manifest in backup_dir.glob(f"*{_MANIFEST_SUFFIX}"):
            record = _load_manifest(manifest)
            if record is not None:
                records.append(record)
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    def get(self, backup_id: str) -> BackupRecord | None:
        """Return one backup's record, or ``None`` if unknown."""
        manifest = self._settings.backup_dir / f"{backup_id}{_MANIFEST_SUFFIX}"
        return _load_manifest(manifest)

    def verify(self, backup_id: str) -> bool:
        """Recompute the archive hash and compare it to the manifest.

        Args:
            backup_id: Backup identifier.

        Returns:
            Whether the archive is present and matches its recorded hash.
        """
        record = self.get(backup_id)
        return record is not None and self._verify_record(record)

    def _verify_record(self, record: BackupRecord) -> bool:
        """Verify a loaded record's archive against its stored hash."""
        archive = Path(record.path)
        if not archive.exists():
            return False
        return _sha256(archive) == record.sha256

    # ------------------------------------------------------------------
    # Restauración
    # ------------------------------------------------------------------

    def restore(self, backup_id: str, *, destination: Path | None = None) -> dict[str, Any]:
        """Restore a backup after verifying its integrity (blocking).

        Args:
            backup_id: Backup identifier.
            destination: Directory to extract into (default: the sources' common
                parent — i.e. the project ``data``/``logs`` roots).

        Returns:
            ``{"restored": bool, "backup_id": ..., "reason": ...}``.

        Raises:
            FileNotFoundError: If the backup does not exist.
            ValueError: If the archive fails verification (never restore a
                corrupt backup over live state).
        """
        record = self.get(backup_id)
        if record is None:
            raise FileNotFoundError(f"Unknown backup: {backup_id}")
        if not self._verify_record(record):
            raise ValueError(f"Backup {backup_id} failed verification; refusing to restore")

        target = destination or self._settings.backup_dir.parent
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(record.path, "r:*") as tar:
            _safe_extract(tar, target)

        if self._audit is not None:
            self._audit.record(
                action=AuditAction.BACKUP_RESTORED,
                actor="operator",
                target=backup_id,
                after={"destination": str(target)},
            )
        _log.warning("Backup %s restored into %s", backup_id, target)
        return {"restored": True, "backup_id": backup_id, "destination": str(target)}

    async def arestore(self, backup_id: str, *, destination: Path | None = None) -> dict[str, Any]:
        """Async wrapper around :meth:`restore` (runs in a worker thread)."""
        return await asyncio.to_thread(self.restore, backup_id, destination=destination)

    # ------------------------------------------------------------------
    # Rotación
    # ------------------------------------------------------------------

    def rotate(self) -> list[str]:
        """Delete backups beyond the retention count, oldest first.

        Returns:
            The ids removed.
        """
        retention = max(1, self._settings.retention)
        backups = self.list_backups()
        removed: list[str] = []
        for record in backups[retention:]:
            try:
                Path(record.path).unlink(missing_ok=True)
                (self._settings.backup_dir / f"{record.backup_id}{_MANIFEST_SUFFIX}").unlink(
                    missing_ok=True
                )
                removed.append(record.backup_id)
            except OSError as exc:
                _log.warning("Could not rotate backup %s: %r", record.backup_id, exc)
        if removed and self._audit is not None:
            self._audit.record(
                action=AuditAction.BACKUP_ROTATED,
                actor="system",
                target="backups",
                after={"removed": removed, "retention": retention},
            )
        return removed

    def status(self) -> dict[str, Any]:
        """Compact backup status for the dashboard."""
        backups = self.list_backups()
        latest = backups[0] if backups else None
        return {
            "enabled": self._settings.enabled,
            "count": len(backups),
            "retention": self._settings.retention,
            "backup_dir": str(self._settings.backup_dir),
            "latest": latest.to_dict() if latest is not None else None,
        }


def _sha256(path: Path) -> str:
    """Compute the SHA-256 of a file, streaming to bound memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(manifest: Path) -> BackupRecord | None:
    """Load a backup record from its manifest (best effort)."""
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return BackupRecord(
            backup_id=str(data["backup_id"]),
            created_at=str(data["created_at"]),
            kind=str(data.get("kind", "manual")),
            path=str(data["path"]),
            size_bytes=int(data.get("size_bytes", 0)),
            sha256=str(data.get("sha256", "")),
            files=[str(f) for f in data.get("files", [])],
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _log.warning("Unreadable backup manifest %s: %r", manifest, exc)
        return None


def _safe_extract(tar: tarfile.TarFile, target: Path) -> None:
    """Extract a tar into ``target``, rejecting path traversal entries.

    Un archivo con ``../`` o rutas absolutas podría escribir fuera del destino;
    aquí se descarta cualquier miembro que no quede dentro de ``target``.
    """
    target = target.resolve()
    for member in tar.getmembers():
        member_path = (target / member.name).resolve()
        if not str(member_path).startswith(str(target)):
            _log.error("Refusing unsafe tar member: %s", member.name)
            continue
        tar.extract(member, target)
