"""Read-only discovery and preflight for the operator-managed GTA corpus."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.core.config import settings
from app.services.image_metadata import InvalidImageError, inspect_static_image
from app.services.operator_timeline_adapter import parse_operator_timeline
from app.services.probe import FFprobe, ProbeError

CorpusOnly = Literal["all", "temporal", "static"]
SmokeMode = Literal["initial", "trailer1"]

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
EXPECTED_TEMPORAL_COUNT = 20
EXPECTED_STATIC_COUNT = 133

_GTA_VI_LABELS = {
    "GTAVI_Trailer_1": "gta_vi_official_trailer_1",
    "GTAVI_Trailer_2": "gta_vi_official_trailer_2",
    "GTAVI_An_Extended_Look": "gta_vi_official_extended_look",
    "GTAVI_Official_Cover_Art_Landscape": "gta_vi_official_cover_art_animation",
    "Boobie_Ike_Video_Clip": "gta_vi_official_clip_boobie_ike",
    "Brian_Heder_Video_Clip": "gta_vi_official_clip_brian_heder",
    "Cal_Hampton_Video_Clip": "gta_vi_official_clip_cal_hampton",
    "DreQuan_Priest_Video_Clip": "gta_vi_official_clip_drequan_priest",
    "Jason_Duval_Video_Clip": "gta_vi_official_clip_jason_duval",
    "Lucia_Caminos_Video_Clip": "gta_vi_official_clip_lucia_caminos",
    "Raul_Bautista_Video_Clip": "gta_vi_official_clip_raul_bautista",
    "Real_Dimez_Video_Clip": "gta_vi_official_clip_real_dimez",
}


@dataclass(frozen=True, slots=True)
class CorpusIssue:
    severity: Literal["error", "warning"]
    code: str
    path: str
    message: str
    line_number: int | None = None


@dataclass(slots=True)
class VideoCorpusEntry:
    game: str
    label: str
    path: Path
    sidecar_path: Path
    byte_size: int = 0
    sha256: str = ""
    sidecar_sha256: str = ""
    duration_seconds: float | None = None
    format_name: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    observation_count: int = 0
    first_timestamp: float | None = None
    last_timestamp: float | None = None
    observations: list[dict[str, Any]] = field(default_factory=list, repr=False)


@dataclass(slots=True)
class ImageCorpusEntry:
    kind: str
    path: Path
    sidecar_path: Path
    provenance: dict[str, Any]
    byte_size: int = 0
    sha256: str = ""
    sidecar_sha256: str = ""
    content_type: str | None = None
    format_name: str | None = None
    width: int | None = None
    height: int | None = None
    semantic_text: str = field(default="", repr=False)


@dataclass(slots=True)
class CorpusPreflightReport:
    corpus_root: Path
    videos: list[VideoCorpusEntry]
    images: list[ImageCorpusEntry]
    issues: list[CorpusIssue]
    excluded_noncanonical_artworks: int = 0

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.issues)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> dict[str, Any]:
        def public_entry(entry: VideoCorpusEntry | ImageCorpusEntry) -> dict[str, Any]:
            payload = asdict(entry)
            payload.pop("observations", None)
            payload.pop("semantic_text", None)
            payload["path"] = str(entry.path.relative_to(self.corpus_root))
            payload["sidecar_path"] = str(entry.sidecar_path.relative_to(self.corpus_root))
            return payload

        return {
            "preflight_version": "gta_corpus_preflight_v1",
            "corpus_root": str(self.corpus_root),
            "ok": self.ok,
            "summary": {
                "videos": len(self.videos),
                "semantic_timelines": sum(bool(item.sidecar_sha256) for item in self.videos),
                "images": len(self.images),
                "semantic_descriptions": sum(bool(item.sidecar_sha256) for item in self.images),
                "excluded_noncanonical_artworks": self.excluded_noncanonical_artworks,
                "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "videos": [public_entry(item) for item in self.videos],
            "images": [public_entry(item) for item in self.images],
            "issues": [asdict(item) for item in self.issues],
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _expected_videos(root: Path) -> list[VideoCorpusEntry]:
    entries: list[VideoCorpusEntry] = []
    for episode in ("000", "001", "002"):
        path = root / "gta_iv" / f"{episode}.mkv"
        entries.append(
            VideoCorpusEntry(
                "gta_iv", f"gta_iv_gameplay_ep{episode}", path, path.with_suffix(".md")
            )
        )
    for episode in ("001", "002", "003", "004", "005"):
        path = root / "gta_v" / f"{episode}.mkv"
        entries.append(
            VideoCorpusEntry("gta_v", f"gta_v_gameplay_ep{episode}", path, path.with_suffix(".md"))
        )
    for basename, label in _GTA_VI_LABELS.items():
        path = root / "gta_vi" / f"{basename}.mp4"
        entries.append(VideoCorpusEntry("gta_vi", label, path, path.with_suffix(".md")))
    return entries


def _declared_metadata(image_root: Path, path: Path, kind: str) -> dict[str, Any]:
    if kind != "screenshot":
        return {}
    relative = path.relative_to(image_root / "GTAVI_Screenshots")
    parts = relative.parts
    if len(parts) >= 3 and parts[0] == "People":
        return {"known_subject": parts[1]}
    if len(parts) >= 3 and parts[0] == "Places":
        return {"known_location": parts[1]}
    return {}


def _image_provenance(image_root: Path, path: Path, kind: str) -> dict[str, Any]:
    values = {
        "screenshot": ("official_screenshot", "screenshots"),
        "artwork": ("official_artwork", "artwork_wallpapers"),
        "vintage": ("official_promotional", "vintage_vice_city_pack"),
    }
    asset_class, collection = values[kind]
    return {
        "source_owner": "rockstar_games",
        "source_class": "official",
        "asset_class": asset_class,
        "collection": collection,
        "game": "gta_vi",
        "declared_metadata": _declared_metadata(image_root, path, kind),
    }


def _discover_images(root: Path, issues: list[CorpusIssue]) -> tuple[list[ImageCorpusEntry], int]:
    image_root = root / "gta_vi" / "Images"
    groups = (
        ("screenshot", image_root / "GTAVI_Screenshots"),
        ("artwork", image_root / "GTAVI_Artwork_Wallpapers"),
        ("vintage", image_root / "GTAVI_Vintage_Vice_City_Pack"),
    )
    entries: list[ImageCorpusEntry] = []
    excluded_artworks = 0
    for kind, directory in groups:
        if not directory.is_dir():
            issues.append(
                CorpusIssue(
                    "error",
                    "missing_directory",
                    _relative(root, directory),
                    "required corpus directory is missing",
                )
            )
            continue
        all_images = sorted(
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.casefold() in SUPPORTED_IMAGE_SUFFIXES
        )
        if kind == "artwork":
            selected = [
                path
                for path in all_images
                if path.suffix.casefold() == ".jpg" and path.stem.casefold().endswith("_landscape")
            ]
            excluded_artworks += len(all_images) - len(selected)
        else:
            selected = all_images
        for path in selected:
            sidecar = path.with_suffix(".md")
            if not sidecar.is_file():
                issues.append(
                    CorpusIssue(
                        "error",
                        "missing_sidecar",
                        _relative(root, path),
                        "canonical image has no matching .md",
                    )
                )
            entries.append(
                ImageCorpusEntry(
                    kind,
                    path,
                    sidecar,
                    _image_provenance(image_root, path, kind),
                )
            )
        selected_stems = {path.with_suffix("") for path in selected}
        for sidecar in sorted(directory.rglob("*.md")):
            if kind == "artwork" and not sidecar.stem.casefold().endswith("_landscape"):
                continue
            if sidecar.with_suffix("") not in selected_stems:
                issues.append(
                    CorpusIssue(
                        "error",
                        "orphan_sidecar",
                        _relative(root, sidecar),
                        "semantic sidecar has no canonical image pair",
                    )
                )
    return entries, excluded_artworks


def _select_smoke(
    videos: list[VideoCorpusEntry], images: list[ImageCorpusEntry], smoke: SmokeMode
) -> tuple[list[VideoCorpusEntry], list[ImageCorpusEntry]]:
    if smoke == "trailer1":
        return [item for item in videos if item.label == "gta_vi_official_trailer_1"], []
    selected_videos = [item for item in videos if item.label == "gta_iv_gameplay_ep000"]
    selected_images = []
    for kind in ("screenshot", "artwork", "vintage"):
        candidate = next((item for item in images if item.kind == kind), None)
        if candidate is not None:
            selected_images.append(candidate)
    return selected_videos, selected_images


async def _inspect_video(
    root: Path,
    entry: VideoCorpusEntry,
    issues: list[CorpusIssue],
    probe: FFprobe,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        relative = _relative(root, entry.path)
        if entry.path.is_symlink() or entry.sidecar_path.is_symlink():
            issues.append(
                CorpusIssue(
                    "error", "unexpected_symlink", relative, "corpus pairs must be regular files"
                )
            )
            return
        if not entry.path.is_file():
            issues.append(
                CorpusIssue("error", "missing_video", relative, "expected video is missing")
            )
            return
        if not entry.sidecar_path.is_file():
            issues.append(
                CorpusIssue(
                    "error", "missing_sidecar", relative, "video has no matching semantic timeline"
                )
            )
            return
        try:
            sidecar_bytes = await asyncio.to_thread(entry.sidecar_path.read_bytes)
            content = sidecar_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(
                CorpusIssue(
                    "error", "invalid_sidecar", _relative(root, entry.sidecar_path), str(exc)
                )
            )
            return
        try:
            result, entry.sha256 = await asyncio.gather(
                probe.inspect(entry.path), asyncio.to_thread(_sha256, entry.path)
            )
        except (OSError, ProbeError, ValueError) as exc:
            issues.append(CorpusIssue("error", "video_probe_failed", relative, str(exc)))
            return
        entry.byte_size = entry.path.stat().st_size
        entry.sidecar_sha256 = hashlib.sha256(sidecar_bytes).hexdigest()
        entry.duration_seconds = result.duration_seconds
        entry.format_name = result.format_name
        entry.video_codec = result.video_codec
        entry.audio_codec = result.audio_codec
        if result.duration_seconds is None or result.duration_seconds <= 0:
            issues.append(
                CorpusIssue(
                    "error", "missing_duration", relative, "ffprobe returned no positive duration"
                )
            )
            return
        parsed = parse_operator_timeline(
            content,
            media_duration_seconds=result.duration_seconds,
            duration_tolerance_seconds=0.001,
        )
        entry.observations = [item.model_dump(mode="json") for item in parsed.observations]
        entry.observation_count = len(parsed.observations)
        if parsed.observations:
            entry.first_timestamp = parsed.observations[0].start_seconds
            entry.last_timestamp = parsed.observations[-1].end_seconds
        else:
            issues.append(
                CorpusIssue(
                    "error",
                    "empty_timeline",
                    _relative(root, entry.sidecar_path),
                    "no valid temporal observations were parsed",
                )
            )
        for item in parsed.issues:
            issues.append(
                CorpusIssue(
                    "error",
                    item.code,
                    _relative(root, entry.sidecar_path),
                    item.message,
                    item.line_number,
                )
            )
        ordered = sorted(
            parsed.observations, key=lambda item: (item.start_seconds, item.end_seconds)
        )
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.start_seconds < previous.end_seconds:
                issues.append(
                    CorpusIssue(
                        "warning",
                        "overlap",
                        _relative(root, entry.sidecar_path),
                        f"{current.start_seconds:.3f}-{current.end_seconds:.3f}s overlaps {previous.start_seconds:.3f}-{previous.end_seconds:.3f}s",
                    )
                )
            gap = current.start_seconds - previous.end_seconds
            if gap > 120:
                issues.append(
                    CorpusIssue(
                        "warning",
                        "suspicious_gap",
                        _relative(root, entry.sidecar_path),
                        f"timeline gap is {gap:.3f}s",
                    )
                )
        if entry.first_timestamp is not None and entry.first_timestamp > 5:
            issues.append(
                CorpusIssue(
                    "warning",
                    "suspicious_first_timestamp",
                    _relative(root, entry.sidecar_path),
                    f"first observation starts at {entry.first_timestamp:.3f}s",
                )
            )
        if entry.last_timestamp is not None and result.duration_seconds - entry.last_timestamp > 5:
            issues.append(
                CorpusIssue(
                    "warning",
                    "suspicious_last_timestamp",
                    _relative(root, entry.sidecar_path),
                    f"last observation ends {result.duration_seconds - entry.last_timestamp:.3f}s before physical duration",
                )
            )


async def _inspect_image(
    root: Path,
    entry: ImageCorpusEntry,
    issues: list[CorpusIssue],
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        relative = _relative(root, entry.path)
        if entry.path.is_symlink() or entry.sidecar_path.is_symlink():
            issues.append(
                CorpusIssue(
                    "error", "unexpected_symlink", relative, "corpus pairs must be regular files"
                )
            )
            return
        if not entry.path.is_file() or not entry.sidecar_path.is_file():
            return
        try:
            sidecar_bytes = await asyncio.to_thread(entry.sidecar_path.read_bytes)
            semantic_text = sidecar_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(
                CorpusIssue(
                    "error", "invalid_sidecar", _relative(root, entry.sidecar_path), str(exc)
                )
            )
            return
        if not semantic_text.strip():
            issues.append(
                CorpusIssue(
                    "error",
                    "empty_description",
                    _relative(root, entry.sidecar_path),
                    "image semantic description is empty",
                )
            )
            return
        try:
            metadata, entry.sha256 = await asyncio.gather(
                inspect_static_image(
                    entry.path,
                    MIME_BY_SUFFIX[entry.path.suffix.casefold()],
                    max_pixels=settings.image_max_pixels,
                ),
                asyncio.to_thread(_sha256, entry.path),
            )
        except (InvalidImageError, OSError, ValueError) as exc:
            issues.append(CorpusIssue("error", "invalid_image", relative, str(exc)))
            return
        entry.byte_size = entry.path.stat().st_size
        entry.sidecar_sha256 = hashlib.sha256(sidecar_bytes).hexdigest()
        entry.semantic_text = semantic_text
        entry.content_type = metadata.content_type
        entry.format_name = metadata.format_name
        entry.width = metadata.width
        entry.height = metadata.height


async def preflight_gta_corpus(
    corpus_root: Path,
    *,
    only: CorpusOnly = "all",
    smoke: SmokeMode | None = None,
    concurrency: int = 1,
    ffprobe_binary: str | None = None,
) -> CorpusPreflightReport:
    if not 1 <= concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    root = corpus_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("corpus root must be a directory")
    issues: list[CorpusIssue] = []
    for game_dir in ("gta_iv", "gta_v", "gta_vi"):
        if not (root / game_dir).is_dir():
            issues.append(
                CorpusIssue(
                    "error", "missing_directory", game_dir, "required game directory is missing"
                )
            )
    for symlink in sorted(
        path
        for game_dir in ("gta_iv", "gta_v", "gta_vi")
        if (root / game_dir).exists()
        for path in (root / game_dir).rglob("*")
        if path.is_symlink()
    ):
        issues.append(
            CorpusIssue(
                "error",
                "unexpected_symlink",
                _relative(root, symlink),
                "symlinks are not accepted in the canonical corpus",
            )
        )

    videos = _expected_videos(root)
    expected_temporal_paths = {
        path for entry in videos for path in (entry.path, entry.sidecar_path)
    }
    actual_temporal_paths = {
        path
        for game_dir in ("gta_iv", "gta_v", "gta_vi")
        for path in (root / game_dir).glob("*")
        if path.is_file() and path.suffix.casefold() in {".mkv", ".mp4", ".md"}
    }
    if only in {"all", "temporal"}:
        for unexpected in sorted(actual_temporal_paths - expected_temporal_paths):
            issues.append(
                CorpusIssue(
                    "error",
                    "unexpected_temporal_file",
                    _relative(root, unexpected),
                    "file is outside the canonical 20 video/timeline manifest",
                )
            )
    images: list[ImageCorpusEntry]
    excluded: int
    if only == "temporal" or smoke == "trailer1":
        images, excluded = [], 0
    else:
        images, excluded = _discover_images(root, issues)
    if smoke is not None:
        videos, images = _select_smoke(videos, images, smoke)
    else:
        actual_video_count = sum(
            path.suffix.casefold() in {".mkv", ".mp4"} for path in actual_temporal_paths
        )
        actual_timeline_count = sum(
            path.suffix.casefold() == ".md" for path in actual_temporal_paths
        )
        if only in {"all", "temporal"} and (
            actual_video_count != EXPECTED_TEMPORAL_COUNT
            or actual_timeline_count != EXPECTED_TEMPORAL_COUNT
        ):
            issues.append(
                CorpusIssue(
                    "error",
                    "temporal_count_mismatch",
                    ".",
                    f"expected 20 videos and 20 timelines, found {actual_video_count} and {actual_timeline_count}",
                )
            )
        if only in {"all", "static"} and len(images) != EXPECTED_STATIC_COUNT:
            issues.append(
                CorpusIssue(
                    "error",
                    "static_count_mismatch",
                    "gta_vi/Images",
                    f"expected {EXPECTED_STATIC_COUNT} canonical images, found {len(images)}",
                )
            )
    if only == "temporal":
        images = []
    elif only == "static":
        videos = []

    semaphore = asyncio.Semaphore(concurrency)
    probe = FFprobe(ffprobe_binary or settings.ffprobe_binary)
    await asyncio.gather(
        *(_inspect_video(root, entry, issues, probe, semaphore) for entry in videos),
        *(_inspect_image(root, entry, issues, semaphore) for entry in images),
    )

    by_sha: dict[str, list[ImageCorpusEntry]] = {}
    for image in images:
        if image.sha256:
            by_sha.setdefault(image.sha256, []).append(image)
    for sha256, duplicates in sorted(by_sha.items()):
        if len(duplicates) < 2:
            continue
        paths = ", ".join(_relative(root, item.path) for item in duplicates)
        issues.append(
            CorpusIssue(
                "warning",
                "duplicate_sha",
                paths,
                f"SHA-256 {sha256} occurs {len(duplicates)} times",
            )
        )
        provenances = {
            json.dumps(item.provenance, sort_keys=True, ensure_ascii=False) for item in duplicates
        }
        if len(provenances) > 1:
            issues.append(
                CorpusIssue(
                    "error",
                    "cross_provenance_sha_conflict",
                    paths,
                    f"SHA-256 {sha256} is assigned to different intended provenance",
                )
            )

    return CorpusPreflightReport(root, videos, images, issues, excluded)


def render_preflight_report(report: CorpusPreflightReport) -> str:
    total_observations = sum(item.observation_count for item in report.videos)
    image_shas = [item.sha256 for item in report.images if item.sha256]
    duplicate_sha_count = len(image_shas) - len(set(image_shas))
    cross_provenance_conflicts = sum(
        item.code == "cross_provenance_sha_conflict" for item in report.issues
    )
    lines = [
        "GTA canonical corpus preflight",
        f"Root: {report.corpus_root}",
        f"Temporal: {len(report.videos)} videos / {sum(bool(item.sidecar_sha256) for item in report.videos)} timelines / {total_observations} observations",
        f"Static: {len(report.images)} images / {sum(bool(item.sidecar_sha256) for item in report.images)} descriptions",
        f"Unique static SHA: {len(set(image_shas))} Duplicate identical files: {duplicate_sha_count} Cross-provenance conflicts: {cross_provenance_conflicts}",
        f"Excluded noncanonical artwork variants: {report.excluded_noncanonical_artworks}",
        f"Errors: {report.error_count} Warnings: {report.warning_count}",
    ]
    for video in report.videos:
        lines.append(
            f"VIDEO {video.label} duration={video.duration_seconds} container={video.format_name} "
            f"video={video.video_codec} audio={video.audio_codec} bytes={video.byte_size} "
            f"sha256={video.sha256} observations={video.observation_count} "
            f"first={video.first_timestamp} last={video.last_timestamp}"
        )
    for kind in ("screenshot", "artwork", "vintage"):
        selected = [item for item in report.images if item.kind == kind]
        if selected:
            lines.append(
                f"STATIC {kind} count={len(selected)} bytes={sum(item.byte_size for item in selected)}"
            )
    for issue in report.issues:
        location = issue.path
        if issue.line_number is not None:
            location += f":{issue.line_number}"
        lines.append(f"{issue.severity.upper()} {issue.code} {location}: {issue.message}")
    lines.append("PREFLIGHT PASS" if report.ok else "PREFLIGHT FAIL")
    return "\n".join(lines)
