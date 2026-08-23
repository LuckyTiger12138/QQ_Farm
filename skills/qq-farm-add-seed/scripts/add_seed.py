#!/usr/bin/env python3
"""Add a QQ Farm special crop with verified replaceable X templates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


PLACEHOLDER_SOURCES = {
    "seed": ("shop_宝华玉兰.png", "290854fc0532083bf43c79a76c8ddbfc9f5eb0750404581408808eaf0d046560"),
    "ws": ("ws_宝华玉兰.png", "b4e100beab672bb6da70955cc308a22bd1e5a45aa48986b188d8206ad4e4c59d"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json_write(path: Path, value: Any) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    try:
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def make_crop(name: str, previous: dict[str, Any]) -> dict[str, Any]:
    crop_id = int(previous["id"]) + 1
    return {
        "id": crop_id,
        "name": name,
        "all_state_spine": f"spine/v2/xiyouzhongzi/{crop_id}",
        "mutant": "",
        "fruit": {"id": int(previous["fruit"]["id"]) + 1, "count": 50},
        "seed_id": int(previous["seed_id"]) + 1,
        "land_level_need": 999,
        "seasons": 1,
        "grow_phases": "种子:8640;发芽:8640;小叶子:8640;大叶子:8640;初熟:8640;成熟:0;",
        "exp": 2911,
        "size": 2,
        "offsetPosition": {"x": 100, "y": 0},
        "mutantEffectScale": {"x": 1.75, "y": 1.5},
        "harvestOffsetPosition": {"x": 0, "y": 0},
        "harvestRandom": True,
        "harvestAllSpineRes": "spine/v2/shouge/Crop_sg_final",
        "harvestAllOffsetPosition": "5:30;50:-30;150:-75;200:-25;150:-30",
        "mature_effect": "effect/prefab/effect_plant_maturation",
        "mature_effect_offset": {"x": 0, "y": 60},
        "rare_plant_light_pos": "{\"x\":113,\"y\":125,\"rotation\":0,\"scale\":{\"x\":1,\"y\":1}}",
        "exp_root": 0,
        "exp_alter": 0,
        "fruit_root": 0,
        "fruit_alter": 0,
    }


def add_seed(repo: Path, name: str) -> dict[str, Any]:
    templates = repo / "templates"
    plants_path = repo / "configs" / "plants.json"
    disabled_path = templates / "disabled.json"
    if not name or name.strip() != name:
        raise ValueError("--name must be non-empty and cannot have surrounding whitespace")
    if any(char in name for char in '\\/:*?\"<>|'):
        raise ValueError("--name contains characters that cannot be used in a template filename")

    plants = json.loads(plants_path.read_text(encoding="utf-8"))
    disabled_data = json.loads(disabled_path.read_text(encoding="utf-8"))
    if not isinstance(plants, list) or not isinstance(disabled_data.get("disabled"), list):
        raise ValueError("plants.json or disabled.json has an unexpected structure")
    if any(item.get("name") == name for item in plants if isinstance(item, dict)):
        raise ValueError(f"crop already exists: {name}")

    targets = {kind: templates / f"{prefix}_{name}.png" for kind, prefix in (("seed", "seed"), ("ws", "ws"))}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite existing templates: " + ", ".join(existing))

    sources: dict[str, Path] = {}
    for kind, (filename, expected_hash) in PLACEHOLDER_SOURCES.items():
        source = templates / filename
        if not source.is_file():
            raise FileNotFoundError(f"verified placeholder source is missing: {source}")
        if sha256(source) != expected_hash:
            raise ValueError(f"placeholder source was changed and is no longer a verified X image: {source}")
        sources[kind] = source

    special = [
        (index, item)
        for index, item in enumerate(plants)
        if isinstance(item, dict) and int(item.get("seed_id", 0)) >= 30000
    ]
    if not special:
        raise ValueError("no special crop with seed_id >= 30000 exists to derive the next IDs")
    insert_after, previous = max(special, key=lambda pair: int(pair[1]["seed_id"]))
    if not isinstance(previous.get("fruit"), dict):
        raise ValueError("latest special crop does not have a fruit record")
    crop = make_crop(name, previous)
    plants.insert(insert_after + 1, crop)

    disabled = disabled_data["disabled"]
    new_disabled = [f"seed_{name}", f"ws_{name}"]
    disabled.extend(key for key in new_disabled if key not in disabled)

    atomic_json_write(plants_path, plants)
    atomic_json_write(disabled_path, disabled_data)
    for kind, target in targets.items():
        shutil.copyfile(sources[kind], target)
        os.utime(target, None)

    return {
        "id": crop["id"],
        "seed_id": crop["seed_id"],
        "fruit_id": crop["fruit"]["id"],
        "templates": {kind: str(path) for kind, path in targets.items()},
        "hashes": {kind: sha256(path) for kind, path in targets.items()},
        "disabled": new_disabled,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="QQ Farm repository root")
    parser.add_argument("--name", required=True, help="new crop name")
    args = parser.parse_args()
    try:
        result = add_seed(args.repo.resolve(), args.name)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
