from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Profile:
    id: str
    kind: str
    label: str
    candidate_ready: bool
    production_ready: bool
    config: Path
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ManufacturingEnvelope:
    width_mm: float
    depth_mm: float
    coordinate_mapping: str


class ProfileRegistry:
    _MANUFACTURING_MAPPING_STATES = {"unverified", "validated"}

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._profiles: dict[str, dict[str, Profile]] = {"printer": {}, "resin": {}, "quality": {}}
        self._candidate_combinations: set[tuple[str, str, str]] = set()
        self._production_combinations: set[tuple[str, str, str]] = set()
        self.reload()

    def _safe_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ProfileError("Profile config path escapes PROFILE_ROOT.")
        return candidate

    @staticmethod
    def _positive_metadata_float(profile: Profile, key: str) -> float:
        raw = profile.metadata.get(key)
        if isinstance(raw, bool):
            raise ProfileError(f"{profile.id} {key} must be a positive finite number.")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ProfileError(f"{profile.id} {key} must be a positive finite number.") from exc
        if not math.isfinite(value) or value <= 0:
            raise ProfileError(f"{profile.id} {key} must be a positive finite number.")
        return value

    @classmethod
    def _validate_printer_geometry(cls, profile: Profile) -> None:
        width_key = "manufacturing_envelope_width_mm"
        depth_key = "manufacturing_envelope_depth_mm"
        mapping_key = "manufacturing_envelope_coordinate_mapping"
        envelope_keys = (width_key, depth_key, mapping_key)
        has_any_envelope_key = any(key in profile.metadata for key in envelope_keys)

        if not has_any_envelope_key:
            if profile.production_ready:
                raise ProfileError(
                    f"Production-ready printer {profile.id} must define a validated manufacturing envelope."
                )
            return

        missing = [key for key in envelope_keys if key not in profile.metadata]
        if missing:
            raise ProfileError(
                f"{profile.id} manufacturing envelope is incomplete; missing: {', '.join(missing)}."
            )

        width = cls._positive_metadata_float(profile, width_key)
        depth = cls._positive_metadata_float(profile, depth_key)
        mapping = str(profile.metadata.get(mapping_key, "")).strip()
        if mapping not in cls._MANUFACTURING_MAPPING_STATES:
            raise ProfileError(
                f"{profile.id} {mapping_key} must be one of: "
                + ", ".join(sorted(cls._MANUFACTURING_MAPPING_STATES))
                + "."
            )

        if "display_width_mm" in profile.metadata:
            display_width = cls._positive_metadata_float(profile, "display_width_mm")
            if width > display_width + 1e-9:
                raise ProfileError(f"{profile.id} manufacturing envelope width exceeds display width.")
        if "display_height_mm" in profile.metadata:
            display_height = cls._positive_metadata_float(profile, "display_height_mm")
            if depth > display_height + 1e-9:
                raise ProfileError(f"{profile.id} manufacturing envelope depth exceeds display height.")

        if profile.production_ready and mapping != "validated":
            raise ProfileError(
                f"Production-ready printer {profile.id} requires physically validated manufacturing-envelope coordinate mapping."
            )

    def _load_kind(self, kind: str, directory: str) -> None:
        folder = self.root / directory
        if not folder.exists():
            return
        for path in sorted(folder.glob("*.json")):
            raw = json.loads(path.read_text("utf-8"))
            profile_id = str(raw.get("id", "")).strip()
            if not profile_id or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in profile_id):
                raise ProfileError(f"Invalid {kind} profile id in {path.name}.")
            config_rel = str(raw.get("config", "")).strip()
            if not config_rel:
                raise ProfileError(f"Missing config path in {path.name}.")
            config_path = self._safe_path(config_rel)
            profile = Profile(
                id=profile_id,
                kind=kind,
                label=str(raw.get("label") or profile_id),
                candidate_ready=bool(raw.get("candidate_ready", raw.get("production_ready", False))),
                production_ready=bool(raw.get("production_ready", False)),
                config=config_path,
                metadata={k: v for k, v in raw.items() if k != "config"},
            )
            if profile.production_ready and not profile.candidate_ready:
                raise ProfileError(f"Production-ready {kind} profile must also be candidate-ready: {profile_id}")
            if kind == "printer":
                self._validate_printer_geometry(profile)
            if profile_id in self._profiles[kind]:
                raise ProfileError(f"Duplicate {kind} profile id: {profile_id}")
            self._profiles[kind][profile_id] = profile

    @staticmethod
    def _parse_combinations(data: dict[str, Any], key: str) -> set[tuple[str, str, str]]:
        combinations: set[tuple[str, str, str]] = set()
        for combo in data.get(key, []):
            if not isinstance(combo, list) or len(combo) != 3:
                raise ProfileError(f"Each {key} entry must be [printer, resin, quality].")
            combinations.add(tuple(str(x) for x in combo))
        return combinations

    def reload(self) -> None:
        self._profiles = {"printer": {}, "resin": {}, "quality": {}}
        self._load_kind("printer", "printers")
        self._load_kind("resin", "resins")
        self._load_kind("quality", "quality")
        compatibility_path = self.root / "compatibility.json"
        data = json.loads(compatibility_path.read_text("utf-8")) if compatibility_path.exists() else {}
        # Backward compatibility with the scaffold's original `combinations` key.
        production_raw = dict(data)
        if "production_combinations" not in production_raw and "combinations" in production_raw:
            production_raw["production_combinations"] = production_raw["combinations"]
        self._candidate_combinations = self._parse_combinations(data, "candidate_combinations")
        self._production_combinations = self._parse_combinations(production_raw, "production_combinations")
        if not self._production_combinations.issubset(self._candidate_combinations):
            raise ProfileError("Every production combination must also be approved as a candidate combination.")

    def get(self, kind: str, profile_id: str) -> Profile:
        try:
            return self._profiles[kind][profile_id]
        except KeyError as exc:
            raise ProfileError(f"Unknown {kind} profile: {profile_id}") from exc

    def printer_manufacturing_envelope(
        self,
        printer_id: str,
        *,
        require_coordinate_mapping: bool = False,
    ) -> ManufacturingEnvelope:
        profile = self.get("printer", printer_id)
        self._validate_printer_geometry(profile)
        width_key = "manufacturing_envelope_width_mm"
        depth_key = "manufacturing_envelope_depth_mm"
        mapping_key = "manufacturing_envelope_coordinate_mapping"
        if any(key not in profile.metadata for key in (width_key, depth_key, mapping_key)):
            raise ProfileError(f"Printer {printer_id} does not define a manufacturing envelope.")
        envelope = ManufacturingEnvelope(
            width_mm=self._positive_metadata_float(profile, width_key),
            depth_mm=self._positive_metadata_float(profile, depth_key),
            coordinate_mapping=str(profile.metadata[mapping_key]).strip(),
        )
        if require_coordinate_mapping and envelope.coordinate_mapping != "validated":
            raise ProfileError(
                f"Printer {printer_id} manufacturing-envelope coordinate mapping is not physically validated."
            )
        return envelope

    @staticmethod
    def _require_configs(profiles: tuple[Profile, Profile, Profile]) -> None:
        missing = [str(p.config) for p in profiles if not p.config.is_file()]
        if missing:
            raise ProfileError("Validated profile references missing PrusaSlicer configuration files.")

    def resolve_candidate(self, printer_id: str, resin_id: str, quality_id: str) -> tuple[Profile, Profile, Profile]:
        profiles = (
            self.get("printer", printer_id),
            self.get("resin", resin_id),
            self.get("quality", quality_id),
        )
        if not all(profile.candidate_ready for profile in profiles):
            raise ProfileError("The selected resin profile is not ready for acceptance-candidate slicing.")
        if (printer_id, resin_id, quality_id) not in self._candidate_combinations:
            raise ProfileError("The selected printer/resin/quality combination is not approved for acceptance testing.")
        self._require_configs(profiles)
        return profiles

    def resolve_production(self, printer_id: str, resin_id: str, quality_id: str) -> tuple[Profile, Profile, Profile]:
        profiles = self.resolve_candidate(printer_id, resin_id, quality_id)
        if not all(profile.production_ready for profile in profiles):
            raise ProfileError("The selected resin production profile is not validated for production.")
        if (printer_id, resin_id, quality_id) not in self._production_combinations:
            raise ProfileError("The selected printer/resin/quality combination is not approved for production.")
        self.printer_manufacturing_envelope(printer_id, require_coordinate_mapping=True)
        return profiles

    def public_summary(self) -> dict[str, Any]:
        safe_keys = {
            "manufacturer", "model", "native_format", "layer_height_mm", "color_required",
            "calibration_status", "blocked_reason", "max_print_height_mm", "display_pixels_x",
            "display_pixels_y", "display_width_mm", "display_height_mm",
            "manufacturing_envelope_width_mm", "manufacturing_envelope_depth_mm",
            "manufacturing_envelope_coordinate_mapping", "manufacturing_envelope_status",
        }
        def items(kind: str) -> list[dict[str, Any]]:
            return [
                {
                    "id": p.id,
                    "label": p.label,
                    "candidate_ready": p.candidate_ready,
                    "production_ready": p.production_ready,
                    **{k: v for k, v in p.metadata.items() if k in safe_keys},
                }
                for p in self._profiles[kind].values()
            ]
        return {
            "printers": items("printer"),
            "resins": items("resin"),
            "quality": items("quality"),
            "candidate_combinations": [list(combo) for combo in sorted(self._candidate_combinations)],
            "production_combinations": [list(combo) for combo in sorted(self._production_combinations)],
        }

    @property
    def candidate_ready(self) -> bool:
        for printer_id, resin_id, quality_id in self._candidate_combinations:
            try:
                self.resolve_candidate(printer_id, resin_id, quality_id)
                return True
            except ProfileError:
                continue
        return False

    @property
    def production_ready(self) -> bool:
        for printer_id, resin_id, quality_id in self._production_combinations:
            try:
                self.resolve_production(printer_id, resin_id, quality_id)
                return True
            except ProfileError:
                continue
        return False
