"""Read the separate final-evidence archive without importing historical code."""
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import zipfile

from .decisions import DecisionPolicy, Gate, Verification, evaluate_case
from .reports import write_report
from .scheduler import restore_pairs
from .storage import digest, write_json


class Evidence:
    def __init__(self, path):
        self.path = Path(path)
        self.cache = {}
        self.archive = zipfile.ZipFile(path) if self.path.is_file() else None
        if self.archive:
            candidates = [n for n in self.archive.namelist() if n.endswith("/evidence-manifest.json")]
            candidates = [n for n in candidates if self.archive.read(n).find(b"FINAL_EVALUATION_Q06_Q20") >= 0]
            if len(candidates) != 1: raise ValueError("Expected one closed final study")
            self.prefix = candidates[0].rsplit("/", 1)[0] + "/"
        else:
            candidates = [self.path] if (self.path / "evidence-manifest.json").exists() else list(self.path.glob("**/evidence-manifest.json"))
            if candidates and candidates[0].is_file(): candidates = [p.parent for p in candidates]
            candidates = [p for p in candidates if json.loads((p / "evidence-manifest.json").read_text())["scope"] == "FINAL_EVALUATION_Q06_Q20"]
            if len(candidates) != 1: raise ValueError("Expected one closed final study")
            self.root = candidates[0].resolve()

    def raw(self, name):
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or ":" in name or "\\" in name:
            raise ValueError("Unsafe evidence path")
        if name not in self.cache:
            if self.archive:
                self.cache[name] = self.archive.read(self.prefix + name)
            else:
                target = self.root.joinpath(*relative.parts).resolve()
                target.relative_to(self.root)
                self.cache[name] = target.read_bytes()
        return self.cache[name]

    def load(self, name):
        return json.loads(self.raw(name))

    def close(self):
        if self.archive: self.archive.close()


def replay(path, output):
    evidence = Evidence(path)
    try:
        manifest = evidence.load("evidence-manifest.json")
        if any(sha256(evidence.raw(name)).hexdigest() != expected for name, expected in manifest["files"].items()):
            raise ValueError("Final evidence hash mismatch")
        identity, summary = evidence.load("identity.json"), evidence.load("summary.json")
        if digest({k: v for k, v in identity.items() if k != "identity_sha256"}) != identity["identity_sha256"]:
            raise ValueError("Measurement identity mismatch")
        import io
        with zipfile.ZipFile(io.BytesIO(evidence.raw("source-used.zip"))) as original:
            if any(sha256(original.read("src/query_cost_predictor/" + name)).hexdigest() != value for name, value in identity["source_sha256"].items()):
                raise ValueError("Archived measurement source mismatch")
        values = identity["config"]["proposed_decision_policy"]
        policy = DecisionPolicy(primary_reduction_percent=values["primary_runtime_reduction_percent"],
                                profile_percents=tuple(values["supplementary_runtime_reduction_percents"]))
        rows, decisions, aa_pairs = [], [], []
        for recorded in summary["cases"]:
            value = dict(recorded)
            prefix = "cases/" + value["query_id"] + "/" + value["database"] + "/" + value["configuration"]
            raw_case = evidence.load(prefix + "/result.json")
            if raw_case["status"] != value["status"]: raise ValueError("Case summary differs from stored outcome")
            gates = value["gates"]
            verification = Verification(*(Gate(gates[name]["status"]) for name in ("generation", "safety", "preconditions", "snapshot", "fixtures")))
            aa, ab = (), ()
            if prefix + "/timing.json" in manifest["files"]:
                timing = evidence.load(prefix + "/timing.json")
                aa, ab = restore_pairs(timing["aa_pairs"]), restore_pairs(timing["ab_pairs"])
                for phase in ("aa", "ab"):
                    for pair in timing[phase + "_pairs"]:
                        for form, field in (("A", "a_us"), ("B", "b_us")):
                            measurement = evidence.load(prefix + f"/measurements/{phase}-{pair['pair_index']:02d}-{form}.json")
                            if measurement["execution_us"] != pair[field]: raise ValueError("Raw pair differs from timing table")
                aa_pairs.extend(aa)
            decision = evaluate_case(verification, aa, ab, policy=policy)
            if decision.outcome.value != value["status"]: raise ValueError("Replayed decision differs from final evidence")
            value["gates"] = {name: record["status"] for name, record in gates.items()}
            if value["query_type"] == "Inner join": value["query_type"] = "Join elimination (original INNER JOIN)"
            rows.append(value); decisions.append((value["query_id"], decision))
        if dict(Counter(r["status"] for r in rows)) != summary["case_status_counts"]:
            raise ValueError("Case counts do not reconcile")
        profile = []
        for percent in policy.profile_percents:
            passing = Counter(qid for qid, d in decisions if d.timing and any(p.reduction_percent == percent and p.meets_rule for p in d.timing.profile))
            profile.append({"runtime_reduction_percent": percent, "case_count": sum(passing.values()), "query_all_four_count": sum(n == 4 for n in passing.values())})
        if profile != [{k: r[k] for k in profile[0]} for r in summary["supplementary_threshold_profile"]]:
            raise ValueError("Supplementary profile differs")
        output = Path(output); output.mkdir(parents=True, exist_ok=False)
        generations = [{"query_id": q["id"], **evidence.load("generation/" + q["id"] + "/result.json")} for q in identity["queries"]]
        result = write_report(output, rows, identity["queries"], scope="REPLAY_OF_FROZEN_FINAL_STUDY",
                              identity_sha256=identity["identity_sha256"], generations=generations)
        audit = {"status": "PASSED", "measurement_identity": identity["identity_sha256"],
                 "verified_raw_files": len(manifest["files"]), "archived_source_files": len(identity["source_sha256"]),
                 "replayed_cases": len(rows), "primary_all_four": sum(q["recommended_in_all_four"] for q in result["queries"]),
                 "supplementary_profile": profile,
                 "aa_strict_counts": {"pairs": len(aa_pairs), "B_faster": sum(p.b_us < p.a_us for p in aa_pairs),
                                      "A_faster": sum(p.a_us < p.b_us for p in aa_pairs)},
                 "new_model_calls": 0, "new_sql_executions": 0,
                 "scope": "Historical observations replayed by the engineering release; no fresh correctness checks or timings"}
        write_json(output / "audit.json", audit)
        return audit
    finally:
        evidence.close()
