# CRITICAL SAFETY RULES — READ FIRST

These rules MUST be followed in every session. No exceptions.

## Commands that MUST NEVER be run:
- `git rm -rf` (or any variant with `-r` and `-f` together)
- `rm -rf` (or any recursive forced remove)
- `find ... -delete` (mass file deletion)

## Every destructive operation requires:
1. **BACKUP FIRST**: `cp -r /home/k4s9/Doudizhu-Arena /home/k4s9/.Codex/backups/doudizhu-arena/$(date +%Y-%m-%d_%H-%M-%S)/`
2. **REPORT**: Tell the user the backup path and size
3. **CONFIRM**: Wait for explicit user confirmation

## Files must be deleted one at a time
Each file deletion requires individual user confirmation. No batch deletes.

## Daily Backup
At the start of the first session each day, back up the project to:
`/home/k4s9/.Codex/backups/doudizhu-arena/daily-YYYY-MM-DD/`
Keep 3 days of daily backups. Clean older ones automatically.

## Python Environment
ALL Python scripts in this project MUST be run using the `doudizhu-arena` conda environment. NEVER use bare `python` or `python3`.

Acceptable forms:
- `/home/k4s9/miniconda3/envs/doudizhu-arena/bin/python script.py`
- `/home/k4s9/miniconda3/envs/doudizhu-arena/bin/python -m module`
- `conda run -n doudizhu-arena python script.py`

Banned forms:
- `python script.py`
- `python3 script.py`
- `~/miniconda3/envs/doudizhu-arena/bin/python` (use full path, not ~ shortcut)