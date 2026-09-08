"""Single-use execution from an exact independently reviewed implementation."""

from __future__ import annotations
import argparse
import json

from .core import BASE, GOAL_SHA, Engine, digest, require, seal, stamp
from .runtime import ROOT, Docker, git, history, preflight
from .verify import scope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["preflight", "execute"])
    args = parser.parse_args()
    if args.operation == "preflight":
        preflight()
        print("Historical authority and local binding verified; mutations=0")
        return
    a, verification = history()
    scope(git("diff", "--name-only", BASE, "HEAD").splitlines())
    review = json.loads((ROOT / "pre-mutation-review.json").read_text())
    require(
        review["verdict"] == "PASS"
        and review["must_fix"] == 0
        and review["claim_accuracy"] == "PASS"
        and review["exact_retained_cleanup"] == "ALLOW",
        "REVIEW_NOT_ALLOW",
    )
    require(
        review["implementation_head"] == git("rev-parse", "HEAD")
        and review["implementation_tree"] == git("rev-parse", "HEAD^{tree}")
        and review["goal_sha256"] == GOAL_SHA,
        "REVIEW_BINDING_DRIFT",
    )
    require(
        not git("status", "--porcelain", "--untracked-files=normal"),
        "IMPLEMENTATION_DIRTY",
    )
    seal(
        ROOT,
        "implementation-binding.json",
        {**review, "history_verification_sha256": digest(verification), **stamp()},
    )
    seal(ROOT, "execution-once.json", {"base": BASE, **stamp()})
    engine = Engine(a, Docker(a), ROOT)
    try:
        result = engine.run()
    except Exception as error:
        result = {
            "terminal": "cleanup_blocked_checkpoint",
            "reason_code": str(error),
            "mutations_completed": engine.removed,
            "mutation_counts": engine.counts,
            "last_completed_receipt": engine.previous,
            "initial_disposition": engine.initial,
            "required_successor_authority": "New explicitly authorized Goal; no retry under this Goal",
        }
    result.update(
        {
            "goal_sha256": GOAL_SHA,
            "implementation_head": review["implementation_head"],
            "starting_head": BASE,
            "historical_evidence_preserved": history()[1]["historical_files_verified"],
            **stamp(),
        }
    )
    seal(ROOT, "final-cleanup-result.json", result)
    files = {
        str(p.relative_to(ROOT)): __import__("hashlib")
        .sha256(p.read_bytes())
        .hexdigest()
        for p in ROOT.rglob("*")
        if p.is_file()
    }
    seal(
        ROOT,
        "private-evidence-index.json",
        {"files": files, "files_sha256": digest(files)},
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
