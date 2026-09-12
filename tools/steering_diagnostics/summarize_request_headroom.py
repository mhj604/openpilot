"""Describe saved request levels, not safe extra torque or a successful replay.

Reads completed JSONL/GZIP captures. No vehicle access, controller execution,
pickle loading, file writes, parameter changes, or generated CAN commands.
Thresholds only select descriptive samples; they are not controller settings.
"""
import argparse
import bisect
import collections
import gzip
import json
import statistics
from pathlib import Path


def describe(values):
    if not values:
        return None
    return dict(min=round(min(values), 4),
                median=round(statistics.median(values), 4),
                max=round(max(values), 4))


def read_capture(directory):
    meta = json.loads((directory / "metadata.json").read_text())["data"]
    limits = [int(meta["params"][k]) for k in ("SteerMaxBaseAdj", "SteerMaxAdj")]
    if len(set(limits)) != 1:
        raise ValueError("This report requires equal captured host torque limits")
    scale = limits[0]
    streams = collections.defaultdict(list)
    base = None
    with gzip.open(directory / "telemetry.jsonl.gz", "rt") as source:
        for line in source:
            event = json.loads(line)
            kind, data = event["kind"], event["data"]
            if kind in ("ready", "collector_started") and base is None:
                base = event["receive_ns"] / 1e9
            if "source_ns" not in event:
                continue
            t = event["source_ns"] / 1e9
            if kind == "can":
                for address, bus, hexadecimal in data:
                    if (address, bus) not in ((0x340, 128), (0x251, 0), (0x2b0, 0)):
                        continue
                    raw = int.from_bytes(bytes.fromhex(hexadecimal), "little")
                    if address == 0x340:
                        streams["tx"].append((t, dict(
                            torque=((raw >> 16) & 2047) - 1024,
                            request=(raw >> 27) & 1,
                            fault_request=(raw >> 28) & 1)))
                    elif address == 0x251:
                        streams["mdps"].append((t, dict(
                            driver=(raw & 2047) - 1024,
                            active=(raw >> 13) & 1,
                            fault=(raw >> 14) & 1,
                            fail=(raw >> 15) & 1,
                            unavailable=(raw >> 12) & 1,
                            error=(raw >> 37) & 1,
                            output=((raw >> 52) & 4095) * .1 - 204.8)))
                    else:
                        angle = raw & 65535
                        streams["sas"].append((t, dict(
                            angle=(angle - 65536 if angle >= 32768 else angle) * .1)))
            elif kind == "carState":
                kept = {key: data[key] for key in (
                    "vEgo", "steeringPressed", "steeringRateLimited", "canValid",
                    "steerFaultTemporary", "steerFaultPermanent")}
                kept["valid"] = event.get("valid", True)
                streams[kind].append((t, kept))
            elif kind == "carControl":
                streams[kind].append((t, dict(
                    active=data["active"],
                    command=data["actuators"]["steer"] * scale,
                    valid=event.get("valid", True))))
            elif kind == "controlsState":
                kept = {key: data["lateral"].get(key) for key in (
                    "active", "error", "p", "i", "f", "saturated",
                    "desiredLateralAccel", "actualLateralAccel")}
                streams[kind].append((t, dict(
                    op_active=data["active"], valid=event.get("valid", True), **kept)))
    if base is None:
        raise ValueError("Capture start marker missing")
    for values in streams.values():
        values.sort(key=lambda item: item[0])
    return base, scale, meta["commit"], streams


def longest_run(rows, predicate):
    first = last = None
    best = 0.0
    for row in rows:
        if predicate(row):
            if first is None or row["t"] - last > .030:
                first = row["t"]
            last = row["t"]
            best = max(best, last - first)
        else:
            first = last = None
    return round(best, 3)


def summarize(rows, scale):
    return dict(
        samples=len(rows),
        speed_kph=describe([r["speed"] for r in rows]),
        angle_deg=describe([r["angle"] for r in rows]),
        calculated_abs_request=describe([abs(r["command"]) for r in rows]),
        transmitted_abs_request=describe([abs(r["tx"]) for r in rows]),
        eps_abs_report=describe([abs(r["eps"]) for r in rows]),
        calculated_at_limit=sum(abs(r["command"]) >= scale - .5 for r in rows),
        transmitted_at_limit=sum(abs(r["tx"]) >= scale for r in rows),
        transmitted_near_limit=sum(abs(r["tx"]) >= scale * .99 for r in rows),
        rate_limited=sum(r["rate_limited"] for r in rows),
        generic_fault=sum(r["generic_fault"] for r in rows),
        controller_error=describe([r["error"] for r in rows]),
        integrator=describe([r["i"] for r in rows]),
        reported_saturation=sum(bool(r["saturated"]) for r in rows),
        max_alignment_ms=max((round(r["alignment_ms"], 3) for r in rows), default=None),
    )


def main(directory):
    base, scale, commit, streams = read_capture(directory)
    times = {key: [t for t, _ in values] for key, values in streams.items()}
    names = ("carState", "carControl", "controlsState", "mdps", "sas")

    def nearest(name, t):
        ts = times[name]
        index = bisect.bisect_left(ts, t)
        index = min(range(max(0, index - 1), min(len(ts), index + 1)),
                    key=lambda i: abs(ts[i] - t))
        return streams[name][index]

    rows = []
    missing_alignment = 0
    for t, tx in streams["tx"]:
        selected = [nearest(name, t) for name in names]
        alignment = max(abs(st - t) for st, _ in selected) * 1000
        cs, cc, ctl, md, sas = [data for _, data in selected]
        aligned = alignment <= 30
        missing_alignment += not aligned
        valid = cs["valid"] and cc["valid"] and ctl["valid"] and cs["canValid"]
        active = cc["active"] and ctl["op_active"] and ctl["active"]
        fault = any(md[k] for k in ("fault", "fail", "unavailable", "error"))
        normal = bool(aligned and valid and active and tx["request"]
                      and not tx["fault_request"] and md["active"] and not fault)
        clean = normal and not cs["steeringPressed"] and abs(md["driver"]) <= 50
        error = ctl["error"] or 0.0
        speed = cs["vEgo"] * 3.6
        # Screening index from the delayed controller error, NOT road truth.
        error_index = abs(error) / max(cs["vEgo"] ** 2, .01)
        same_direction = error * tx["torque"] < 0
        demand = bool(clean and error_index >= .003 and same_direction)
        generic_fault = cs["steerFaultTemporary"] or cs["steerFaultPermanent"]
        rows.append(dict(
            t=t - base, speed=speed, angle=sas["angle"], normal=normal,
            clean=clean, demand=demand, error_index=error_index,
            candidate=bool(demand and not cs["steeringRateLimited"]
                           and not generic_fault and abs(cc["command"]) < .9 * scale
                           and abs(tx["torque"] - cc["command"]) <= 4),
            tx=tx["torque"], command=cc["command"], eps=md["output"],
            driver=md["driver"], rate_limited=cs["steeringRateLimited"],
            mdps_fault=fault, mdps_active=md["active"], req=tx["request"],
            generic_fault=generic_fault, error=error, p=ctl["p"],
            i=ctl["i"], f=ctl["f"], saturated=ctl["saturated"],
            alignment_ms=alignment))

    bands = {}
    for lo, hi in ((2, 8), (8, 18), (18, 36), (36, 200)):
        in_band = lambda r: lo <= r["speed"] < hi
        normal = [r for r in rows if in_band(r) and r["normal"]]
        clean = [r for r in rows if in_band(r) and r["clean"]]
        demand = [r for r in rows if in_band(r) and r["demand"]]
        candidate = [r for r in rows if in_band(r) and r["candidate"]]
        bands["%s_to_%s_kph" % (lo, hi)] = dict(
            normal_samples=len(normal), clean_samples=len(clean),
            demand=summarize(demand, scale),
            under_limit_screen=summarize(candidate, scale),
            longest_under_limit_screen_s=longest_run(
                rows, lambda r: in_band(r) and r["candidate"]),
            under_limit_screen_times=[round(r["t"], 3) for r in candidate[:8]])

    known_windows = {
        "steering-20260908-230648": ((211.5, 212.4),),
        "steering-geometry-20260909-183631-219055": (
            (155, 157.15), (156.215, 157.15), (157.155, 157.9)),
    }
    windows = known_windows.get(directory.name, ())
    cases = {}
    for lo, hi in windows:
        selected = [r for r in rows if lo <= r["t"] <= hi]
        cases["%s_to_%s_s" % (lo, hi)] = dict(
            all_aligned=summarize([r for r in selected if r["alignment_ms"] <= 30], scale),
            clean=summarize([r for r in selected if r["clean"]], scale),
            raw_fault_samples=sum(r["mdps_fault"] for r in selected),
            max_abs_driver=max((abs(r["driver"]) for r in selected), default=None))

    result = dict(capture=directory.name, commit=commit, host_scale=scale,
                  tx_samples=len(rows), alignment_over_30ms=missing_alignment,
                  speed_bands=bands, confirmed_windows=cases,
                  caveats=[
                      "Samples are not independent experiments or exposure durations.",
                      "Under-limit screens are not proof of safe spare torque or bad tuning.",
                      "Error is the controller's delayed, model-derived error, not road truth.",
                      "CAN echo is not a measurement of ECU-internal received torque.",
                      "EPS report units are not established as Nm."])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.directories:
        main(path)
