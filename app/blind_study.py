#!/usr/bin/env python3
"""Sealed, auditable workflow for a genuine blind aerodynamic prediction.

The calculation inputs and method are frozen before reference data is made
available.  Predictions are sealed in a second immutable manifest.  Only a
valid prediction seal permits the reference package to be attached.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil

from blind_readiness import (
    assess_blind_readiness,
    verify_blind_readiness,
)


BLIND_SCHEMA = "repairmach.blind-study/1.0"
PREDICTION_SCHEMA = "repairmach.blind-predictions/1.0"
REFERENCE_SCHEMA = "repairmach.blind-reference/1.0"
REFERENCE_ROLE_TOKENS = ("reference", "benchmark", "book", "tunnel", "эталон", "книг", "продув")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_blind_package(
    package_dir: Path,
    *,
    repairmach_version: str,
    project_name: str,
    geometry_path: Path,
    project_config_path: Path,
    scenario_catalog_path: Path,
    settings_path: Path,
    selected_scenario: dict,
    method_declaration: dict,
    additional_inputs: list[tuple[str, Path]] | None = None,
    solver_paths: list[tuple[str, Path]] | None = None,
    geometry_certificate_path: Path | None = None,
    require_geometry_certificate: bool = False,
    runtime_executables: dict[str, Path] | None = None,
) -> Path:
    """Copy and seal all pre-reference inputs of a blind study."""
    package_dir = Path(package_dir)
    manifest_path = package_dir / "blind_manifest.json"
    if package_dir.exists() and any(package_dir.iterdir()):
        raise FileExistsError(f"Папка слепого расчёта уже не пуста: {package_dir}")
    preflight = preflight_blind_inputs(
        geometry_path=geometry_path,
        project_config_path=project_config_path,
        selected_scenario=selected_scenario,
        method_declaration=method_declaration,
        additional_inputs=additional_inputs,
        solver_paths=solver_paths,
    )
    if not preflight["valid"]:
        raise ValueError("Предрасчётная проверка не пройдена: " + "; ".join(preflight["errors"]))
    readiness = None
    if geometry_certificate_path is not None:
        readiness = assess_blind_readiness(
            geometry_path=geometry_path,
            certificate_path=geometry_certificate_path,
            selected_scenario=selected_scenario,
            method_declaration=method_declaration,
            additional_inputs=additional_inputs,
            runtime_executables=runtime_executables,
        )
        if not readiness["valid"]:
            raise ValueError(
                "Единый допуск слепого расчёта не пройден: "
                + "; ".join(readiness["errors"])
            )
    elif require_geometry_certificate:
        raise ValueError("Для слепого расчёта обязателен положительный сертификат геометрии")
    else:
        preflight["warnings"].append(
            "Сертификат геометрии не приложен: совместимый пакет 9.1 не является полным слепым допуском"
        )
    package_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = package_dir / "inputs"
    inputs_dir.mkdir()
    (package_dir / "predictions").mkdir()
    (package_dir / "after_unblind").mkdir()

    sources = [
        ("geometry", Path(geometry_path), f"geometry{Path(geometry_path).suffix.lower()}"),
        ("project_config", Path(project_config_path), "project_config.json"),
        ("scenario_catalog", Path(scenario_catalog_path), "calculation_scenarios.json"),
        ("settings", Path(settings_path), "repairmach_settings.json"),
    ]
    if geometry_certificate_path is not None:
        sources.append(("geometry_certificate", Path(geometry_certificate_path), "geometry_certificate.json"))
    for role, source in additional_inputs or []:
        sources.append((str(role), Path(source), f"{_safe_name(role)}{Path(source).suffix.lower()}"))

    frozen_inputs = []
    used_names: set[str] = set()
    for role, source, requested_name in sources:
        if not source.is_file():
            raise FileNotFoundError(f"Не найден входной файл {role}: {source}")
        frozen_name = _unique_name(requested_name, used_names)
        destination = inputs_dir / frozen_name
        shutil.copy2(source, destination)
        frozen_inputs.append(_file_record(role, source, destination, package_dir))
    if readiness is not None:
        readiness_path = inputs_dir / "blind_readiness.json"
        _write_json(readiness_path, readiness)
        frozen_inputs.append(
            _file_record("blind_readiness", readiness_path, readiness_path, package_dir)
        )

    solvers = []
    for role, solver in solver_paths or []:
        solver = Path(solver)
        if not solver.is_file():
            raise FileNotFoundError(f"Не найден решатель {role}: {solver}")
        solvers.append(
            {
                "role": str(role),
                "path": str(solver.resolve()),
                "size_bytes": solver.stat().st_size,
                "sha256": file_sha256(solver),
            }
        )

    manifest = {
        "schema": BLIND_SCHEMA,
        "state": "inputs_sealed",
        "created_at": _utc_now(),
        "repairmach_version": str(repairmach_version),
        "project": str(project_name),
        "scenario": deepcopy(selected_scenario),
        "method_declaration": deepcopy(method_declaration),
        "preflight": preflight,
        "readiness": readiness,
        "inputs": frozen_inputs,
        "solvers": solvers,
        "blind_rules": {
            "reference_data_present": False,
            "pointwise_tuning_after_seal": "prohibited",
            "method_change_after_seal": "requires_new_package",
            "reference_import_allowed_after": "predictions_sealed",
            "geometry_certificate_required": bool(require_geometry_certificate),
        },
    }
    manifest["seal"] = _seal(manifest)
    _write_json(manifest_path, manifest)
    return manifest_path


def preflight_blind_inputs(
    *,
    geometry_path: Path,
    project_config_path: Path,
    selected_scenario: dict,
    method_declaration: dict,
    additional_inputs: list[tuple[str, Path]] | None = None,
    solver_paths: list[tuple[str, Path]] | None = None,
) -> dict:
    """Reject a blind package whose method or reference geometry is not frozen."""
    errors = []
    warnings = []
    geometry_path = Path(geometry_path)
    project_config_path = Path(project_config_path)
    if not geometry_path.is_file():
        errors.append(f"Не найдена геометрия: {geometry_path}")
    elif geometry_path.suffix.lower() != ".vsp3":
        errors.append("Основная геометрия слепого расчёта должна быть VSP3")
    if not project_config_path.is_file():
        errors.append(f"Не найден project_config.json: {project_config_path}")
    else:
        try:
            config = _read_json(project_config_path)
            reference = config["default_reference"]
            for key in ("area", "longitudinal_length", "lateral_length"):
                value = float(reference[key])
                if not math.isfinite(value) or value <= 0.0:
                    errors.append(f"Опорная величина {key} должна быть положительной")
            center = [float(value) for value in reference["center"]]
            if len(center) != 3 or not all(math.isfinite(value) for value in center):
                errors.append("Центр масс должен содержать три конечные координаты")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Некорректные опорные величины project_config.json: {exc}")

    if selected_scenario.get("availability") != "ready":
        errors.append("Для слепого расчёта нужен исполняемый сценарий со статусом ready")
    if selected_scenario.get("execution") != "vspaero_study":
        errors.append("Слепой аэродинамический сценарий должен использовать vspaero_study")
    if selected_scenario.get("tail_incidence") != "fixed":
        errors.append("Положение GO должно быть фиксировано в сценарии до расчёта")
    if not str(selected_scenario.get("tail_geometry_name", "")).strip():
        errors.append("В сценарии не указано имя фиксированного GO")
    try:
        if not math.isfinite(float(selected_scenario.get("tail_incidence_deg"))):
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Угол GO должен быть конечным числом")
    criteria = selected_scenario.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        errors.append("До расчёта должны быть объявлены критерии допуска")
    else:
        for key in ("numerical_percent", "total_mean_percent"):
            try:
                value = float(criteria[key])
                if not math.isfinite(value) or value < 0.0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                errors.append(f"Не задан корректный критерий {key}")
    if not selected_scenario.get("vspaero_cases"):
        errors.append("Сценарий не содержит расчётных точек VSPAERO")

    if method_declaration.get("pointwise_tuning") is not False:
        errors.append("Поточечная подстройка должна быть явно запрещена")
    if method_declaration.get("geometry_adjustment_after_seal") is not False:
        errors.append("Изменение геометрии после печати должно быть явно запрещено")
    if not str(method_declaration.get("method_version", "")).strip():
        errors.append("Не зафиксирована версия методики")

    for role, path in additional_inputs or []:
        normalized_role = str(role).lower()
        if any(token in normalized_role for token in REFERENCE_ROLE_TOKENS):
            errors.append(f"Эталонный вход {role} запрещён до запечатывания прогноза")
        if not Path(path).is_file():
            errors.append(f"Не найден дополнительный вход {role}: {path}")
    if not solver_paths:
        warnings.append("Контрольные суммы решателей не записаны")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def verify_blind_package(manifest_path: Path) -> dict:
    """Verify the manifest seal and every frozen local input."""
    manifest_path = Path(manifest_path)
    package_dir = manifest_path.parent.resolve()
    payload = _read_json(manifest_path)
    errors = []
    if payload.get("schema") != BLIND_SCHEMA:
        errors.append("Неизвестная схема blind_manifest")
    errors.extend(_verify_seal(payload))
    for record in payload.get("inputs", []):
        relative = Path(str(record.get("relative_path", "")))
        candidate = (package_dir / relative).resolve()
        if not _is_relative_to(candidate, package_dir):
            errors.append(f"Входной путь выходит за пределы пакета: {relative}")
            continue
        errors.extend(_verify_file_record(record, candidate))
    for record in payload.get("solvers", []):
        errors.extend(_verify_file_record(record, Path(str(record.get("path", "")))))
    if payload.get("blind_rules", {}).get("reference_data_present") is not False:
        errors.append("В исходном пакете обнаружен признак эталонных данных")
    readiness = payload.get("readiness")
    certificate_required = bool(
        payload.get("blind_rules", {}).get("geometry_certificate_required")
    )
    if readiness is not None:
        errors.extend(verify_blind_readiness(readiness))
        input_by_role = {
            str(record.get("role")): record
            for record in payload.get("inputs", [])
            if isinstance(record, dict)
        }
        geometry_record = input_by_role.get("geometry", {})
        certificate_record = input_by_role.get("geometry_certificate", {})
        readiness_record = input_by_role.get("blind_readiness", {})
        if geometry_record.get("sha256") != readiness.get("geometry", {}).get("sha256"):
            errors.append("Единый допуск относится к другой геометрии пакета")
        if certificate_record.get("sha256") != readiness.get("certificate", {}).get("sha256"):
            errors.append("Единый допуск относится к другому сертификату пакета")
        readiness_path = (
            package_dir / str(readiness_record.get("relative_path", ""))
        ).resolve()
        readiness_file_valid = _is_relative_to(readiness_path, package_dir)
        if readiness_file_valid and readiness_path.is_file():
            try:
                readiness_file_valid = _read_json(readiness_path) == readiness
            except (OSError, json.JSONDecodeError):
                readiness_file_valid = False
        else:
            readiness_file_valid = False
        if not readiness_file_valid:
            errors.append("Отдельный файл единого допуска отсутствует или не совпадает с манифестом")
        if sha256_payload(payload.get("scenario", {})) != readiness.get("scenario", {}).get("sha256"):
            errors.append("Единый допуск относится к другому сценарию пакета")
        if sha256_payload(payload.get("method_declaration", {})) != readiness.get("method_declaration_sha256"):
            errors.append("Единый допуск относится к другой декларации метода")
    elif certificate_required:
        errors.append("В обязательном слепом пакете отсутствует единый допуск")
    return {
        "valid": not errors,
        "state": payload.get("state"),
        "package_fingerprint": payload.get("seal", {}).get("payload_sha256"),
        "errors": errors,
    }


def seal_predictions(manifest_path: Path, prediction_paths: list[Path]) -> Path:
    """Freeze prediction artifacts without modifying the sealed input manifest."""
    manifest_path = Path(manifest_path)
    verification = verify_blind_package(manifest_path)
    if not verification["valid"]:
        raise ValueError("Исходный пакет повреждён: " + "; ".join(verification["errors"]))
    if not prediction_paths:
        raise ValueError("Не указан ни один файл прогноза")

    package_dir = manifest_path.parent
    after_unblind = package_dir / "after_unblind"
    if after_unblind.exists() and any(after_unblind.iterdir()):
        raise ValueError("В пакет уже помещены данные после раскрытия; прогноз не может быть запечатан")
    output_path = package_dir / "prediction_seal.json"
    if output_path.exists():
        raise FileExistsError("Прогноз уже запечатан; для нового прогноза создайте новый пакет")
    predictions_dir = package_dir / "predictions"
    used_names: set[str] = set()
    records = []
    for index, source_value in enumerate(prediction_paths, start=1):
        source = Path(source_value)
        if not source.is_file():
            raise FileNotFoundError(f"Не найден файл прогноза: {source}")
        name = _unique_name(source.name or f"prediction_{index}", used_names)
        destination = predictions_dir / name
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        records.append(_file_record(f"prediction_{index}", source, destination, package_dir))

    seal_payload = {
        "schema": PREDICTION_SCHEMA,
        "state": "predictions_sealed",
        "sealed_at": _utc_now(),
        "blind_manifest": "blind_manifest.json",
        "input_package_fingerprint": verification["package_fingerprint"],
        "predictions": records,
        "reference_data_used": False,
    }
    seal_payload["seal"] = _seal(seal_payload)
    _write_json(output_path, seal_payload)
    return output_path


def verify_prediction_seal(prediction_seal_path: Path) -> dict:
    prediction_seal_path = Path(prediction_seal_path)
    package_dir = prediction_seal_path.parent.resolve()
    payload = _read_json(prediction_seal_path)
    errors = []
    if payload.get("schema") != PREDICTION_SCHEMA:
        errors.append("Неизвестная схема prediction_seal")
    errors.extend(_verify_seal(payload))
    blind_path = package_dir / str(payload.get("blind_manifest", ""))
    blind = verify_blind_package(blind_path) if blind_path.is_file() else {
        "valid": False,
        "package_fingerprint": None,
        "errors": ["Не найден blind_manifest.json"],
    }
    errors.extend(blind["errors"])
    if payload.get("input_package_fingerprint") != blind.get("package_fingerprint"):
        errors.append("Прогноз относится к другой версии входного пакета")
    for record in payload.get("predictions", []):
        candidate = (package_dir / str(record.get("relative_path", ""))).resolve()
        if not _is_relative_to(candidate, package_dir):
            errors.append("Путь прогноза выходит за пределы пакета")
            continue
        errors.extend(_verify_file_record(record, candidate))
    return {"valid": not errors, "state": payload.get("state"), "errors": errors}


def attach_reference_after_seal(
    prediction_seal_path: Path,
    reference_paths: list[Path],
) -> Path:
    """Attach reference data only after a valid prediction was sealed."""
    prediction_seal_path = Path(prediction_seal_path)
    verification = verify_prediction_seal(prediction_seal_path)
    if not verification["valid"]:
        raise ValueError("Прогноз не прошёл проверку: " + "; ".join(verification["errors"]))
    if not reference_paths:
        raise ValueError("Не указан ни один эталонный файл")

    package_dir = prediction_seal_path.parent
    output_path = package_dir / "after_unblind" / "reference_manifest.json"
    if output_path.exists():
        raise FileExistsError("Эталон уже подключён к этому слепому расчёту")
    reference_dir = package_dir / "after_unblind" / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    records = []
    for index, source_value in enumerate(reference_paths, start=1):
        source = Path(source_value)
        if not source.is_file():
            raise FileNotFoundError(f"Не найден эталонный файл: {source}")
        destination = reference_dir / _unique_name(source.name, used_names)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        records.append(_file_record(f"reference_{index}", source, destination, package_dir))
    payload = {
        "schema": REFERENCE_SCHEMA,
        "state": "unblinded",
        "attached_at": _utc_now(),
        "prediction_seal": "prediction_seal.json",
        "reference_files": records,
    }
    payload["seal"] = _seal(payload)
    _write_json(output_path, payload)
    return output_path


def _file_record(role: str, source: Path, frozen: Path, package_dir: Path) -> dict:
    return {
        "role": str(role),
        "original_path": str(source.resolve()),
        "relative_path": frozen.resolve().relative_to(package_dir.resolve()).as_posix(),
        "size_bytes": frozen.stat().st_size,
        "sha256": file_sha256(frozen),
    }


def _verify_file_record(record: dict, path: Path) -> list[str]:
    role = str(record.get("role", "file"))
    if not path.is_file():
        return [f"Не найден зафиксированный файл {role}: {path}"]
    errors = []
    if path.stat().st_size != int(record.get("size_bytes", -1)):
        errors.append(f"Изменён размер файла {role}")
    if file_sha256(path) != record.get("sha256"):
        errors.append(f"Не совпадает SHA-256 файла {role}")
    return errors


def _seal(payload: dict) -> dict:
    return {
        "algorithm": "sha256",
        "payload_sha256": hashlib.sha256(_canonical_bytes(payload)).hexdigest(),
    }


def _verify_seal(payload: dict) -> list[str]:
    seal = payload.get("seal")
    if not isinstance(seal, dict) or seal.get("algorithm") != "sha256":
        return ["Отсутствует поддерживаемая печать SHA-256"]
    unsigned = deepcopy(payload)
    unsigned.pop("seal", None)
    expected = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    return [] if expected == seal.get("payload_sha256") else ["Печать манифеста не совпадает"]


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_name(value: str) -> str:
    text = "".join(character if character.isalnum() or character in "-_" else "_" for character in str(value))
    return text.strip("_") or "input"


def _unique_name(requested: str, used: set[str]) -> str:
    source = Path(requested)
    candidate = source.name
    counter = 2
    while candidate.lower() in used:
        candidate = f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    used.add(candidate.lower())
    return candidate


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
