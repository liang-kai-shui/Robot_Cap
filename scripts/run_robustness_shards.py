"""Resume offline P4 by profile with unchanged task indices and fault seeds."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from robot.backends.perturbed import PROFILES
from robustness_benchmark import summarize


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_group(output, name, profiles, extra, expected):
    original = output / name
    shard_root = output / "p4-shards" / name
    if (original / "experiment.json").exists():
        metadata = load(original / "experiment.json")
        rows = [json.loads(x) for x in (original / "raw_results.jsonl").read_text(encoding="utf-8").splitlines()]
        for profile in profiles:
            dest = shard_root / profile
            dest.mkdir(parents=True, exist_ok=True)
            if not (dest / "experiment.json").exists():
                save(dest / "experiment.json", {**metadata, "profiles": {profile: metadata["profiles"][profile]}})
                shutil.copy2(original / "scenarios.json", dest / "scenarios.json")
                selected = [row for row in rows if row["profile"] == profile]
                (dest / "raw_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                                       for row in selected), encoding="utf-8")
                for row in selected:
                    filename = f"{profile}-{row['task_id']}-{row['repeat']}.json"
                    shutil.copy2(original / filename, dest / filename)
        print(f"{name}: reuse {len(rows)} completed episodes", flush=True)

    def run(profile):
        dest = shard_root / profile
        dest.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, "-u", str(ROOT / "robustness_benchmark.py"),
                "--output", str(dest), "--profile", profile, *extra]
        if (dest / "experiment.json").exists():
            args.append("--resume")
        with (dest / "process.log").open("w", encoding="utf-8") as log:
            code = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
        if code:
            raise RuntimeError(f"{name}/{profile}: exit {code}; see process.log")
        print(f"{name}/{profile}: completed", flush=True)
        return dest

    with ThreadPoolExecutor(max_workers=3) as pool:
        folders = list(pool.map(run, profiles))
    metadata = load(folders[0] / "experiment.json")
    rows = []
    profile_data = {}
    for dest in folders:
        config = load(dest / "experiment.json")
        assert {k: v for k, v in config.items() if k != "profiles"} == {
                k: v for k, v in metadata.items() if k != "profiles"}, "Mismatched shard metadata"
        profile_data.update(config["profiles"])
        rows.extend(json.loads(x) for x in (dest / "raw_results.jsonl").read_text(encoding="utf-8").splitlines())
    assert len(rows) == expected and len({(r["task_id"], r["repeat"], r["profile"]) for r in rows}) == expected
    indexes = {task: index for index, task in enumerate(metadata["task_ids"])}
    for row in rows:
        assert row["fault_seed"] == metadata["seed"] + indexes[row["task_id"]] * 10000 + row["repeat"]
    rows.sort(key=lambda row: (indexes[row["task_id"]], row["repeat"], profiles.index(row["profile"])))
    original.mkdir(parents=True, exist_ok=True)
    merged_metadata = {**metadata, "profiles": profile_data}
    save(original / "experiment.json", merged_metadata)
    save(original / "summary.json", {**merged_metadata, "results": summarize(rows)})
    shutil.copy2(folders[0] / "scenarios.json", original / "scenarios.json")
    (original / "raw_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    for dest in folders:
        for path in dest.glob("*.json"):
            if path.name not in ("experiment.json", "scenarios.json", "summary.json"):
                shutil.copy2(path, original / path.name)
    print(f"{name}: merged {len(rows)} unique episodes", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runs/flash-comparison-2026-10-07/controls")
    args = parser.parse_args()
    output = Path(args.output)
    run_group(output, "robustness-fixed", list(PROFILES), ["--runs", "3", "--seed", "20261006"], 330)
    run_group(output, "robustness-random", ["ideal", "range-3cm", "move-8pct", "odometry-3pct", "combined"],
              ["--random-worlds", "30", "--world-seed", "161803", "--seed", "20261007"], 150)
    save(output / "completed.json", {"completed_utc": datetime.now(timezone.utc).isoformat(),
                                    "p4_execution": "profile shards, maximum 3 parallel processes"})
