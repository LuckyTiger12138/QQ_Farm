---
name: qq-farm-add-seed
description: Add a QQ Farm seed crop to the D:\\qq-farm project. Use when asked to add a new crop or seed, create its selectable template files, make it available in settings, or prepare replaceable X placeholder images without overwriting user-provided screenshots.
---

# QQ Farm Add Seed

Use `scripts/add_seed.py` for every new special crop. It updates `configs/plants.json`; `models/game_data.py` and the settings crop dropdowns load this data dynamically, so do not edit the GUI solely to expose the crop.

```powershell
python C:\Users\FuXing\.codex\skills\qq-farm-add-seed\scripts\add_seed.py --repo D:\qq-farm --name "新作物"
```

The script allocates the next special-crop IDs from the record with the highest `seed_id >= 30000`, adds the standard 12-hour / 2911-exp record, creates `seed_<name>.png` and `ws_<name>.png`, and adds only those two new template keys to `templates/disabled.json`.

## Template Safety

- Use only the verified X placeholders `shop_宝华玉兰.png` and `ws_宝华玉兰.png`; never copy a previous crop screenshot such as 何首乌.
- Do not overwrite an existing template. Treat it as a user-provided replacement and stop for direction.
- Keep only new X placeholders disabled. Do not re-disable or otherwise change older template entries.
- After the user replaces either X image with a real screenshot, enable that template through the template manager.

## Verify

Run after a successful addition:

```powershell
python -m py_compile models\game_data.py
@'
from models.game_data import get_crop_by_name
print(get_crop_by_name("新作物"))
'@ | python
git diff --check
```

The crop tuple should be `(name, seed_id, 999, 43200, 2911, 50)`. Confirm both new template files exist, retain the X-placeholder hash reported by the script, and that only their `seed_` / `ws_` keys were appended to `disabled.json`. Do not run real-window game tests for this data/template change.
