"""Замер базовой нагрузки ПК (этап 0.5).

Запуск:  python scripts/baseline.py --label idle --seconds 60
Результат: data/baselines/<время>-<label>.json  (потом сравниваем «до» и «после» Jarvis/BoostPC)
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


def gpu_sample() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()[0]
        util, used, total, temp = (float(x) for x in out.split(","))
        return {"util": util, "mem_used_mb": used, "mem_total_mb": total, "temp_c": temp}
    except Exception:
        return None


def summarize(values: list[float]) -> dict:
    return {"avg": round(statistics.fmean(values), 2), "max": round(max(values), 2)} if values else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--label", default="baseline")
    args = ap.parse_args()

    procs = list(psutil.process_iter(["pid", "name"]))
    for p in procs:
        try:
            p.cpu_percent(None)  # «прайминг»: дальше будет среднее за период замера
        except psutil.Error:
            pass
    psutil.cpu_percent(None)

    cpu, ram, gpu_util, gpu_mem = [], [], [], []
    t_end = time.monotonic() + args.seconds
    print(f"Замер {args.seconds} с... ничего тяжёлого не запускай.")
    while time.monotonic() < t_end:
        time.sleep(args.interval)
        cpu.append(psutil.cpu_percent(None))
        ram.append(psutil.virtual_memory().percent)
        g = gpu_sample()
        if g:
            gpu_util.append(g["util"])
            gpu_mem.append(g["mem_used_mb"])

    top = []
    for p in procs:
        try:
            top.append({"name": p.info["name"], "pid": p.pid, "cpu_avg_pct": p.cpu_percent(None),
                        "rss_mb": round(p.memory_info().rss / 2**20, 1)})
        except psutil.Error:
            pass
    top_cpu = sorted(top, key=lambda x: x["cpu_avg_pct"], reverse=True)[:10]
    top_ram = sorted(top, key=lambda x: x["rss_mb"], reverse=True)[:10]

    vm = psutil.virtual_memory()
    report = {
        "label": args.label,
        "time": datetime.now().isoformat(timespec="seconds"),
        "seconds": args.seconds,
        "cpu_percent": summarize(cpu),
        "ram_percent": summarize(ram),
        "ram_total_gb": round(vm.total / 2**30, 1),
        "processes": len(psutil.pids()),
        "gpu_util_percent": summarize(gpu_util),
        "gpu_mem_used_mb": summarize(gpu_mem),
        "top_cpu": top_cpu,
        "top_ram": top_ram,
    }
    out_dir = ROOT / "data" / "baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{args.label}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"CPU: {report['cpu_percent']}  RAM%: {report['ram_percent']}  процессов: {report['processes']}")
    if gpu_util:
        print(f"GPU: {report['gpu_util_percent']}  VRAM МБ: {report['gpu_mem_used_mb']}")
    print("Топ по CPU:", ", ".join(f"{t['name']} {t['cpu_avg_pct']}%" for t in top_cpu[:5]))
    print("Топ по RAM:", ", ".join(f"{t['name']} {t['rss_mb']} МБ" for t in top_ram[:5]))
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
