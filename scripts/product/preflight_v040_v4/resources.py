"""Recoverable lifecycle: frozen intent -> observation -> validated birth -> exact cleanup."""

from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
import json
from .birth import validate_container, validate_storage
from .cleanup import cleanup_command, nonowned
from .common import Failure, LABEL, canonical, digest, git, load, now, require, seal
from .docker import Docker
from .identity import stable_running, validate_network_stage, lifecycle, immutable
from .budget import consume
from .journal import Journal


class Resources:
    def __init__(
        self, root: Path, plan: dict[str, Any], docker: Docker, source_head: str
    ) -> None:
        self.root, self.plan, self.docker, self.source_head = (
            root,
            plan,
            docker,
            source_head,
        )
        self.attempt = plan["attempt_id"]
        self.binding = docker.binding()
        docker.bound = self.binding
        self.journal = Journal(root / "journal", self.binding)
        self.births: list[dict[str, Any]] = []
        self.running: dict[str, dict[str, Any]] = {}
        self.capture_number = len(list((root / "captures").glob("*.json")))
        for path in sorted((root / "births").glob("*.json")):
            birth = load(path)
            require(
                birth["plan_digest"] == digest(plan)
                and birth["source_head"] == source_head
                and birth["binding"] == self.binding,
                "BIRTH_BINDING_DRIFT",
            )
            receipt = load(root / "journal/receipts" / (birth["create_key"] + ".json"))
            intent = load(root / "journal/intents" / (birth["create_key"] + ".json"))
            require(
                birth["create_receipt_digest"] == digest(receipt)
                and birth["create_intent_digest"] == digest(intent)
                and receipt["intent_digest"] == digest(intent),
                "BIRTH_CHAIN_DRIFT",
            )
            self.validate_birth(birth["kind"], birth["role"], birth["record"], receipt)
            self.births.append(birth)
        for path in sorted((root / "running").glob("*.json")):
            if not (
                root / "journal/receipts" / ("remove-" + path.stem + ".json")
            ).exists():
                self.running[path.stem] = load(path)

        for receipt_path in sorted((root / "journal/receipts").glob("create-*.json")):
            self.consume_observation(receipt_path.stem)
        self.consume_birth()

    def consume_observation(self, key: str) -> None:
        reservation = (
            self.root.parent / "budget/reservations" / (self.attempt + ".json")
        )
        if not reservation.exists():
            return
        receipt = load(self.root / "journal/receipts" / (key + ".json"))
        intent = load(self.root / "journal/intents" / (key + ".json"))
        require(
            receipt["intent_digest"] == digest(intent)
            and receipt["observation"]["binding"] == self.binding,
            "CREATE_OBSERVATION_CHAIN_DRIFT",
        )
        created = []
        for kind, role, name in intent["authority"]["target_plan"]:
            for row in receipt["observation"]["resources"][kind]:
                identifier = row["Name" if kind == "volume" else "Id"]
                if (
                    row["Name"].lstrip("/") == name
                    and identifier not in intent["authority"]["before_ids"][kind]
                ):
                    created.append(
                        {"kind": kind, "role": role, "identifier": identifier}
                    )
        if not created:
            return
        evidence_path = self.root / "first-runtime-creation.json"
        if not evidence_path.exists():
            seal(
                self.root,
                evidence_path.name,
                {
                    "create_key": key,
                    "intent_digest": digest(intent),
                    "receipt_digest": digest(receipt),
                    "observed_new_resources": created,
                    "cleanup_authority": "NOT_GRANTED_BY_BUDGET_ACCOUNTING",
                    **now(),
                },
            )
        receipts = [
            load(p) for p in (self.root.parent / "budget").glob("attempt-*.json")
        ]
        if not any(r["attempt_id"] == self.attempt for r in receipts):
            consume(
                self.root.parent, self.attempt, load(reservation)["runtime_surface"]
            )

    def consume_birth(self) -> None:
        reservation = (
            self.root.parent / "budget/reservations" / (self.attempt + ".json")
        )
        if not self.births or not reservation.exists():
            return
        receipts = [
            load(p) for p in (self.root.parent / "budget").glob("attempt-*.json")
        ]
        if any(r["attempt_id"] == self.attempt for r in receipts):
            return
        consume(self.root.parent, self.attempt, load(reservation)["runtime_surface"])

    def capture(self, label: str) -> dict[str, Any]:
        value = self.docker.capture()
        seal(self.root, f"captures/{self.capture_number:04d}-{label}.json", value)
        self.capture_number += 1
        return value

    def command(self, args: list[str]) -> dict[str, Any]:
        require(
            git("rev-parse", "HEAD") == self.source_head, "RUNTIME_SOURCE_HEAD_DRIFT"
        )
        require(
            not git("diff", "--name-only")
            and not git("diff", "--cached", "--name-only"),
            "RUNTIME_SOURCE_DIRTY",
        )
        self.docker.binding()
        result = self.docker.command(args, timeout=180)
        return {
            "client_exit_status": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            **now(),
        }

    def operation(
        self,
        key: str,
        args: list[str],
        target_plan: Any,
        post: Callable[[dict[str, Any]], bool],
        preflight: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        target_plan = json.loads(canonical(target_plan))
        intent_path = self.root / "journal/intents" / (key + ".json")
        if intent_path.exists():
            intent = load(intent_path)
            require(
                intent["authority"]["target_plan"] == target_plan
                and intent["command"] == args,
                "OPERATION_PLAN_DRIFT",
            )
            authority = intent["authority"]
        else:
            before = self.capture("before-" + key)
            preflight(before)
            authority = {
                "plan_digest": digest(self.plan),
                "target_plan": target_plan,
                "before_digest": digest(before),
                "before_ids": before["ids"],
            }
        return self.journal.once(
            key,
            args,
            authority,
            lambda: self.command(args),
            lambda: self.capture("after-" + key),
            post,
        )

    def network_ids(self, planned: dict[str, Any]) -> dict[str, str]:
        known = {
            b["record"]["Name"]: b["record"]["Id"]
            for b in self.births
            if b["kind"] == "network"
        }
        known["none"] = self.plan["builtin_none_id"]
        names = planned["network_names"]
        require(set(names) <= known.keys(), "NETWORK_BIRTH_MISSING")
        return {name: known[name] for name in names}

    def validate_birth(
        self, kind: str, role: str, record: dict[str, Any], receipt: dict[str, Any]
    ) -> None:
        observation = receipt["observation"]
        require(
            observation["binding"] == self.binding
            and record in observation["resources"][kind],
            "BIRTH_OBSERVATION_UNBOUND",
        )
        if kind == "container":
            planned = self.plan["containers"][role]
            volumes = {
                b["role"]: b["record"] for b in self.births if b["kind"] == "volume"
            }
            validate_container(
                record,
                planned,
                planned["image_config"],
                self.network_ids(planned),
                volumes,
                project=planned.get("project"),
                config_hash=planned.get("config_hash"),
            )
        else:
            validate_storage(kind, record, self.plan[kind + "s"][role])

    def birth(
        self, kind: str, role: str, record: dict[str, Any], create_key: str
    ) -> dict[str, Any]:
        receipt = load(self.root / "journal/receipts" / (create_key + ".json"))
        intent = load(self.root / "journal/intents" / (create_key + ".json"))
        require(receipt["intent_digest"] == digest(intent), "BIRTH_RECEIPT_UNBOUND")
        identifier = record["Name" if kind == "volume" else "Id"]
        require(
            identifier not in intent["authority"]["before_ids"][kind],
            "BIRTH_PREEXISTING_RESOURCE",
        )
        prior = [b for b in self.births if b["kind"] == kind and b["role"] == role]
        if prior:
            require(
                len(prior) == 1
                and prior[0]["record"] == record
                and prior[0]["create_key"] == create_key,
                "BIRTH_REPLACEMENT",
            )
            return prior[0]
        self.validate_birth(kind, role, record, receipt)
        planned = self.plan["containers"][role] if kind == "container" else None
        value = {
            "kind": kind,
            "role": role,
            "attempt_id": self.attempt,
            "record": record,
            "binding": self.binding,
            "plan_digest": digest(self.plan),
            "source_head": self.source_head,
            "plan_validated": True,
            "create_key": create_key,
            "create_intent_digest": digest(intent),
            "create_receipt_digest": digest(receipt),
            "process": planned["process"] if planned else None,
            "network_ids": self.network_ids(planned) if planned else None,
            **now(),
        }
        seal(self.root, f"births/{len(self.births):03d}-{role}.json", value)
        self.births.append(value)
        self.consume_birth()
        return value

    def create_group(
        self, key: str, args: list[str], targets: list[tuple[str, str, str]]
    ) -> list[dict[str, Any]]:
        """Even a partial/failed create independently seals every provable birth."""

        def preflight(view: dict[str, Any]) -> None:
            for kind, _, name in targets:
                require(
                    not any(
                        r["Name"].lstrip("/") == name for r in view["resources"][kind]
                    ),
                    "CREATE_NAME_EXISTS:" + name,
                )

        def post(view: dict[str, Any]) -> bool:
            return all(
                any(r["Name"].lstrip("/") == name for r in view["resources"][kind])
                for kind, _, name in targets
            )

        operation_error = None
        try:
            self.operation(key, args, targets, post, preflight)
        except Failure as error:
            operation_error = error
        path = self.root / "journal/receipts" / (key + ".json")
        require(path.exists(), "CREATE_OUTCOME_UNKNOWN")
        receipt = load(path)
        self.consume_observation(key)
        result = []
        failures = []
        for kind, role, name in targets:
            rows = [
                r
                for r in receipt["observation"]["resources"][kind]
                if r["Name"].lstrip("/") == name
            ]
            if len(rows) != 1:
                continue
            try:
                result.append(self.birth(kind, role, rows[0], key))
            except Failure as error:
                failures.append(str(error))
        if failures:
            seal(
                self.root,
                "birth-failures-" + key + ".json",
                {"failures": failures, "receipt_digest": digest(receipt)},
            )
            raise Failure("UNPROVABLE_CREATED_IDENTITY")
        if operation_error:
            raise operation_error
        require(
            receipt["outcome"].get("client_exit_status") in (None, 0),
            "CREATE_CLIENT_FAILED",
        )
        return result

    def storage(self, kind: str, key: str) -> dict[str, Any]:
        require(kind in ("network", "volume"), "STORAGE_KIND_DENIED")
        plan = self.plan[kind + "s"][key]
        args = [kind, "create", "--driver", plan["driver"]]
        for name, value in sorted(plan["labels"].items()):
            args += ["--label", name + "=" + value]
        args.append(plan["name"])
        births = self.create_group("create-" + key, args, [(kind, key, plan["name"])])
        return births[0]["record"]

    def compose_create(
        self, model_path: Path, project: str, group: str
    ) -> list[dict[str, Any]]:
        require(
            model_path.is_relative_to(self.root) and not model_path.is_symlink(),
            "COMPOSE_PATH_UNBOUND",
        )
        roles = {
            k: v
            for k, v in self.plan["containers"].items()
            if v.get("project") == project
        }
        require(
            digest(load(model_path)) == self.plan["compose_digests"][group],
            "COMPOSE_CONTENT_DRIFT",
        )
        args = [
            "compose",
            "--project-name",
            project,
            "-f",
            str(model_path),
            "create",
            "--no-build",
            "--no-recreate",
            "--pull",
            "never",
        ]
        return self.create_group(
            "create-" + group,
            args,
            [
                ("container", role, p["service"]["container_name"])
                for role, p in sorted(roles.items())
            ],
        )

    def start(self, role: str) -> dict[str, Any]:
        birth = self.find_birth("container", role)
        identifier = birth["record"]["Id"]
        args = ["container", "start", identifier]

        def preflight(view: dict[str, Any]) -> None:
            row = next(
                r for r in view["resources"]["container"] if r["Id"] == identifier
            )
            require(
                row["State"]["Status"] == "created"
                and row["State"]["Running"] is False,
                "START_ALREADY_CONSUMED",
            )
            require(
                immutable(row) == immutable(birth["record"])
                and row["HostConfig"].get("OomKillDisable")
                == birth["record"]["HostConfig"].get("OomKillDisable"),
                "START_BIRTH_IDENTITY_DRIFT",
            )
            validate_network_stage(row, birth["network_ids"], "created")

        def post(view: dict[str, Any]) -> bool:
            rows = [r for r in view["resources"]["container"] if r["Id"] == identifier]
            return len(rows) == 1 and rows[0]["State"]["Running"] is True

        receipt = self.operation(
            "start-" + role, args, {"birth": digest(birth)}, post, preflight
        )
        row = next(
            r
            for r in receipt["observation"]["resources"]["container"]
            if r["Id"] == identifier
        )
        validate_network_stage(row, birth["network_ids"], "running")
        if role != "kafka-volume-probe":
            lifecycle(
                birth["record"], row, role=role, stage="running", binding=self.binding
            )
        path = self.root / "running" / (role + ".json")
        if path.exists():
            require(load(path) == row, "RUNNING_ANCHOR_DRIFT")
        else:
            seal(self.root, "running/" + role + ".json", row)
        self.running[role] = row
        return row

    def find_birth(self, kind: str, role: str) -> dict[str, Any]:
        births = [b for b in self.births if b["kind"] == kind and b["role"] == role]
        require(len(births) == 1, "BIRTH_MISSING:" + role)
        birth = dict(births[0])
        proof = self.root / "oom-proof.json"
        if role == "kafka-volume-probe" and proof.exists():
            birth["oom_proof"] = load(proof)
        return birth

    def remove(self, birth: dict[str, Any]) -> None:
        kind, role = birth["kind"], birth["role"]
        identifier = birth["record"]["Name" if kind == "volume" else "Id"]
        birth = self.find_birth(kind, role)
        view = self.capture("cleanup-" + role)
        rows = [
            r
            for r in view["resources"][kind]
            if r["Name" if kind == "volume" else "Id"] == identifier
        ]
        if kind == "container" and rows and rows[0]["State"]["Running"]:
            args = cleanup_command(kind, birth, view, self.attempt, operation="stop")

            def preflight(observation: dict[str, Any]) -> None:
                require(
                    cleanup_command(
                        kind, birth, observation, self.attempt, operation="stop"
                    )
                    == args,
                    "STOP_PLAN_DRIFT",
                )

            def stopped(observation: dict[str, Any]) -> bool:
                found = [
                    r
                    for r in observation["resources"]["container"]
                    if r["Id"] == identifier
                ]
                return len(found) == 1 and found[0]["State"]["Running"] is False

            self.operation(
                "stop-" + role, args, {"birth": digest(birth)}, stopped, preflight
            )
        # Reconcile a successful interrupted stop even when the container is now stopped.
        stop_key = "stop-" + role
        if (self.root / "journal/intents" / (stop_key + ".json")).exists() and not (
            self.root / "journal/receipts" / (stop_key + ".json")
        ).exists():
            intent = load(self.root / "journal/intents" / (stop_key + ".json"))

            def stopped_again(observation: dict[str, Any]) -> bool:
                return any(
                    r["Id"] == identifier and r["State"]["Running"] is False
                    for r in observation["resources"]["container"]
                )

            self.operation(
                stop_key,
                intent["command"],
                intent["authority"]["target_plan"],
                stopped_again,
                lambda _: None,
            )
        key = "remove-" + role
        intent_path = self.root / "journal/intents" / (key + ".json")
        if intent_path.exists():
            args = load(intent_path)["command"]
        else:
            args = cleanup_command(
                kind,
                birth,
                self.capture("before-rm-" + role),
                self.attempt,
                operation="remove",
            )

        def preflight_remove(observation: dict[str, Any]) -> None:
            require(
                cleanup_command(
                    kind, birth, observation, self.attempt, operation="remove"
                )
                == args,
                "REMOVE_PLAN_DRIFT",
            )

        name = birth["record"]["Name"]

        def absent(observation: dict[str, Any]) -> bool:
            return not any(
                r["Name" if kind == "volume" else "Id"] == identifier
                or r["Name"] == name
                for r in observation["resources"][kind]
            )

        self.operation(key, args, {"birth": digest(birth)}, absent, preflight_remove)
        self.running.pop(role, None)

    def assert_stable(self, view: dict[str, Any]) -> None:
        by_id = {r["Id"]: r for r in view["resources"]["container"]}
        for role, first in self.running.items():
            require(first["Id"] in by_id, "RUNNING_ROLE_MISSING:" + role)
            stable_running(first, by_id[first["Id"]])

    def cleanup(self, baseline: dict[str, Any]) -> dict[str, Any]:
        containers = {b["role"]: b for b in self.births if b["kind"] == "container"}
        for role in ("worker", "api", "kafka-volume-probe"):
            if role in containers:
                self.remove(containers.pop(role))
        for kind in ("network", "volume"):
            for b in reversed(
                [
                    b
                    for b in self.births
                    if b["kind"] == kind and b["role"].startswith("product-")
                ]
            ):
                self.remove(b)
        for role in reversed(self.plan["sandbox_start_order"]):
            if role in containers:
                self.remove(containers.pop(role))
        require(not containers, "UNPLANNED_CLEANUP_ROLE")
        for kind in ("network", "volume"):
            for b in reversed(
                [
                    b
                    for b in self.births
                    if b["kind"] == kind and not b["role"].startswith("product-")
                ]
            ):
                self.remove(b)
        final = []
        for i in range(2):
            view = self.capture("final-" + str(i))
            require(
                not any(
                    (
                        (
                            r["Config"].get("Labels")
                            if kind == "container"
                            else r.get("Labels")
                        )
                        or {}
                    ).get(LABEL)
                    == self.attempt
                    for kind, rows in view["resources"].items()
                    for r in rows
                ),
                "FINAL_OWNED_NOT_ZERO",
            )
            require(
                nonowned(view, self.births, self.attempt)
                == nonowned(baseline, [], self.attempt),
                "NONOWNED_DRIFT",
            )
            final.append(digest(view))
        result = {
            "status": "CLEAN",
            "final_captures": final,
            "owned_remaining": 0,
            "nonowned_unchanged": True,
        }
        seal(self.root, "cleanup-result.json", result)
        return result
