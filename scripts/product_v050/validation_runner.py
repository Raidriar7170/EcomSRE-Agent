"""Four single-use slots on the original episode/Provider ledger."""

from datetime import UTC, datetime
import json
from scripts.product_v050.final_closure import ClosureRunner
from ecomsre.product.knowledge import validation_batch_v050 as batch
from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION

SLOTS = ("N4", "N5", "N6", "N7")
STRATA = {"N4": "POSITIVE_INCIDENT", "N5": "NO_INCIDENT", "N6": "CONFUSABLE_CORE_KNOWN"}


class ValidationRunner(ClosureRunner):
    def __init__(
        self, evolution, environment_id, *, episode_root, batch_id=batch.BATCH
    ):
        super().__init__(evolution, environment_id, episode_root=episode_root)
        original = super()._get("plan")
        if original is None or str(self.episode_root) != original["episode_root"]:
            raise ValueError("original episode ledger root required")
        with self.store.connect() as c:
            self.batch = batch.load(c, batch_id=batch_id)
        if self.batch is None or self.batch["environment_id"] != environment_id:
            raise ValueError("validation batch not installed")

    def _get(self, key):
        # Collection uses the established audited Payment control, not a new
        # source contract or a new development run.
        if key == "control-repair":
            return super()._get(key)
        return super()._get(self.batch["batch_id"] + ":" + key)

    def _keep(self, key, value):
        return super()._keep(self.batch["batch_id"] + ":" + key, value)

    @property
    def plan(self):
        return dict(
            campaign=self.batch.get("campaign", "live-final-closure-08"),
            slots=self.slots,
        )

    @property
    def slots(self):
        p = self.batch["plan"]
        return {
            s: next(k for k, v in p["holdout_episodes"].items() if v == STRATA[s])
            for s in STRATA
        } | {"N7": p["recurrence_episode"]}

    def reserve_episode(self, slot):
        if slot not in SLOTS or self._get("episode:" + slot):
            raise ValueError("single-use validation slot required")
        with self.store.connect() as c:
            batch.verify(c, self.batch)
            state = c.execute(
                "SELECT state FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (self.batch["registration_id"],),
            ).fetchone()[0]
            if slot == "N7" and state != "ACTIVE":
                raise ValueError("recurrence requires new validated promotion")
        if "ingestion" in self.batch["plan"]["collection"]:
            batch.verify_ingestion_preparation(self.evo, self.batch)
        prep = self._get("preparation:" + slot)
        if prep is None or not datetime.fromisoformat(
            prep["earliest_legal_observation"]
        ) <= datetime.now(UTC) <= datetime.fromisoformat(prep["deadline"]):
            raise ValueError("fixed preparation required before reservation")
        for prior in SLOTS[: SLOTS.index(slot)]:
            if self._get("collection:" + prior) is None:
                raise ValueError("prior raw collection incomplete")
            if not (self._get("terminal:" + prior) or {}).get("succeeded"):
                raise ValueError("prior episode failed or incomplete; no replacement")
            if prior in STRATA and not (self._get("qualification:" + prior) or {}).get(
                "qualified"
            ):
                raise ValueError("prior independent control qualification failed")
        current = self.episode_ledger()
        if any(
            current.get(k) != v
            for k, v in self.batch["original_episode_ledger"].items()
        ):
            raise ValueError("original live ledger changed")
        if (
            len(current) - len(self.batch["original_episode_ledger"])
            >= self.batch["new_live_limit"]
        ):
            raise ValueError("four new live starts exhausted")
        if len(current) >= self.batch["cumulative_live_limit"]:
            raise ValueError("cumulative live limit exhausted")
        original = super()._get("plan")
        if (
            len(self.episode_ledger()) - original["baseline_episodes"]
            >= self.batch["round_live_limit"]
        ):
            raise ValueError("closure live limit exhausted")
        start, end = __import__(
            "ecomsre.product.knowledge.selection_lock_v050",
            fromlist=["collection_range"],
        ).collection_range(self.batch["plan"])
        if not start <= datetime.now(UTC) <= end:
            raise ValueError("fixed batch collection deadline exceeded")
        reservation = self._keep(
            "episode:" + slot,
            dict(
                slot=slot,
                episode_id=self.slots[slot],
                reserved_at=datetime.now(UTC).isoformat(),
            ),
        )
        path = (
            self.episode_root
            / self.plan["campaign"]
            / "episodes"
            / slot
            / "started.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("x") as stream:
            json.dump(
                dict(
                    episode_id=reservation["episode_id"],
                    start={"utc": reservation["reserved_at"]},
                    status="STARTED",
                    product_recovery_writes=0,
                ),
                stream,
            )
        path.chmod(0o600)
        return reservation

    def incident(self, slot):
        if slot == "N2":
            parent = ClosureRunner(
                self.evo, self.environment_id, episode_root=self.episode_root
            )
            return parent.incident(slot)
        return super().incident(slot)

    def seal_collection(
        self, slot, iid, *, raw_index, scrape_receipt=None, ingestion_receipt=None
    ):
        obj = self.evo.investigations.objects.put_json(raw_index)
        receipt = (
            None
            if scrape_receipt is None
            else self.evo.investigations.objects.put_json(scrape_receipt).object_sha256
        )
        return self._keep(
            "collection:" + slot,
            dict(
                incident_id=iid,
                sealed_at=datetime.now(UTC).isoformat(),
                raw_index_sha256=obj.object_sha256,
                scrape_receipt_sha256=receipt,
                ingestion_receipt_sha256=(
                    None
                    if ingestion_receipt is None
                    else self.evo.investigations.objects.put_json(
                        ingestion_receipt
                    ).object_sha256
                ),
                diagnosis_sha256=self.evo.knowledge._diagnosis(iid).result_sha256,
                preparation_sha256=batch.sha(self._get("preparation:" + slot)),
                reservation_sha256=batch.sha(self._get("episode:" + slot)),
            ),
        )

    def qualify(self, candidate, slot):
        iid = self.incident(slot)
        q = self.evo.control_qualifications(candidate, {iid: STRATA[slot]})[iid]
        return self._keep("qualification:" + slot, q)

    def evaluate_and_promote(self, candidate):
        for slot in STRATA:
            if not self.qualify(candidate, slot)["qualified"]:
                raise ValueError(
                    "control qualification failed; no evaluation or promotion"
                )
        cases = {self.incident(s): label for s, label in STRATA.items()}
        self.evo.freeze(
            candidate.registration_id, cases, derived_controls_version=CONTROL_VERSION
        )
        result = self.evo.evaluate(candidate.registration_id)
        self._keep("evaluation", result.model_dump(mode="json"))
        if result.gate_passed:
            self.evo.promote(candidate.registration_id)
        return result


def require_recurrence(*, diagnosis, bindings, candidate, incident_id, before, after):
    """Zero calls alone is never proof of normal learned reuse."""
    if (
        diagnosis["terminal"] != "EXTENSION_KNOWN"
        or diagnosis["provider_calls"] != 0
        or before != after
        or not bindings
        or {b["registration_id"] for b in bindings} != {candidate.registration_id}
        or {b["incident_id"] for b in bindings} != {incident_id}
        or {b["candidate_sha256"] for b in bindings} != {candidate.compiled_sha256}
        or not diagnosis["supporting_evidence_refs"]
    ):
        raise ValueError("NORMAL_RECURRENCE_REQUIREMENTS_FAILED")
