"""Explicit access to application-published reference data from an empty domain."""
from __future__ import annotations

import copy

import duckdb
import jsonschema

from agentic_analytics.agent.schemas import obj
from agentic_analytics.lakehouse.registry import get_bindings
from agentic_analytics.lakehouse.store import StoreError, VersionConflict


class ReferenceCatalogueTools:
    def __init__(self, store, workspace_id, catalogues):
        self.store, self.workspace_id = store, workspace_id
        # Descriptors originate in application configuration, never model input.
        self.catalogues = copy.deepcopy(catalogues)
        self.cards = {}
        for catalogue_id, descriptor in self.catalogues.items():
            card = {"catalogue_id": catalogue_id, "title": descriptor["title"], "status": "unavailable"}
            snapshot_id = descriptor.get("snapshot_id")
            if not snapshot_id:
                card["reason"] = descriptor.get("reason") or "No published reference catalogue is available."
            else:
                try:
                    with duckdb.connect(str(store.snapshot_path(snapshot_id)), read_only=True,
                                        config={"enable_external_access": "false"}) as connection:
                        bindings = get_bindings(connection)
                    ready = [binding for binding in bindings.values() if binding.get("status") == "ready"]
                    card.update(snapshot_id=snapshot_id, ready_metric_count=len(ready),
                                source_systems=sorted({str(binding["source_system"]) for binding in ready if binding.get("source_system")})[:30])
                    if ready:
                        card["status"] = "available"
                    else:
                        card["reason"] = "The published catalogue contains no ready numeric metrics."
                except (StoreError, duckdb.Error, OSError, ValueError, KeyError):
                    card["reason"] = "The published reference catalogue could not be verified."
            self.cards[catalogue_id] = card

    def available_catalogues(self):
        workspace = self.store.workspace(self.workspace_id)
        cards = copy.deepcopy(list(self.cards.values()))
        for card in cards:
            if card["status"] != "available":
                card["next_step"] = "Reference data is unavailable here. Research the requested official source or explain the missing source; do not repeat catalogue attachment."
            elif workspace["snapshot_id"] == card["snapshot_id"]:
                card.update(status="attached", next_step="Use discover, describe and dimension_values in this workspace.")
            elif not self.store.is_empty_domain_snapshot(workspace["snapshot_id"]):
                card.update(status="unavailable", reason="This workspace already has a different pinned catalogue. It cannot be replaced by this attachment tool.",
                            next_step="Use the current workspace catalogue, or research the requested official source.")
            else:
                card["next_step"] = "When the user requests reference data from these sources, call attach_reference_catalogue with this catalogue_id and the current expected_version, then discover the requested metric."
        return cards

    @staticmethod
    def _result(workspace, catalogue_id, *, changed, recovered=False):
        next_step = "The reference catalogue is available alongside the existing imported sources. Run discover for the requested measure, then describe and choose its actual dimensions and period. This attachment did not calculate or certify a comparison."
        if workspace.get("analysis_head"):
            next_step += (" The preserved_analysis_id may still use the previous snapshot. To extend that analysis with the newly attached reference data, "
                          "reuse its saved plan's existing columns, periods, alignment, operations and scope requirements, add the verified reference selections, "
                          "and call execute to save a new comparison on the current snapshot. Do not use revise_analysis across snapshots; the preserved analysis remains unchanged.")
        return {"status": "ok", "catalogue_id": catalogue_id, "snapshot_id": workspace["snapshot_id"],
                "workspace_version": workspace["version"], "workspace_revision_id": workspace["revision_id"],
                "catalogue_attached": True, "workspace_changed": changed, "recovered": recovered,
                "preserved_dataset_count": len(workspace.get("datasets", [])),
                "preserved_analysis_id": workspace.get("analysis_head"),
                "next_step": next_step}

    def attach_reference_catalogue(self, catalogue_id, expected_version):
        card = self.cards.get(catalogue_id)
        if not card or card["status"] != "available":
            return {"status": "blocked", "code": "REFERENCE_CATALOGUE_UNAVAILABLE", "catalogue_id": catalogue_id,
                    "message": "The requested application-published reference catalogue is unavailable.",
                    "workspace_changed": False, "available_catalogues": self.available_catalogues()}
        before = self.store.workspace(self.workspace_id)
        workspace = self.store.attach_reference_catalogue(self.workspace_id, card["snapshot_id"], catalogue_id,
                                                         expected_version=expected_version)
        return self._result(workspace, catalogue_id, changed=workspace["revision_id"] != before["revision_id"])

    def recover(self, arguments, intent):
        workspace = self.store.workspace(self.workspace_id)
        card = self.cards.get(arguments.get("catalogue_id")) or {}
        receipt = workspace.get("reference_catalogue") or {}
        if (receipt.get("catalogue_id") == arguments.get("catalogue_id")
                and receipt.get("snapshot_id") == card.get("snapshot_id")
                and receipt.get("previous_revision_id") == intent.get("input_revision")):
            return self._result(workspace, arguments["catalogue_id"], changed=True, recovered=True)
        if workspace["revision_id"] == intent.get("input_revision"):
            return None
        return {"status": "blocked", "code": "UNKNOWN_MUTATION_OUTCOME",
                "message": "Workspace changed without the exact reference attachment receipt; refusing uncertain replay."}

    def extra_tools(self):
        parameters = obj({"catalogue_id": {"type": "string", "enum": sorted(self.catalogues)},
                          "expected_version": {"type": "integer", "minimum": 0}})

        def handler(arguments):
            try:
                jsonschema.validate(arguments, parameters)
                return self.attach_reference_catalogue(**arguments)
            except jsonschema.ValidationError as exc:
                return {"status": "blocked", "code": "INVALID_ARGUMENTS", "message": exc.message}
            except VersionConflict as exc:
                workspace = self.store.workspace(self.workspace_id)
                return {"status": "blocked", "code": "VERSION_CONFLICT", "message": str(exc),
                        "submitted_version": arguments.get("expected_version"), "current_version": workspace["version"],
                        "current_revision_id": workspace["revision_id"], "workspace_changed": False,
                        "next_step": "Read the current workspace revision and retry the same catalogue selection with its current version."}
            except (StoreError, OSError, ValueError, TypeError) as exc:
                return {"status": "blocked", "code": "REFERENCE_CATALOGUE_ATTACH_FAILED", "message": str(exc),
                        "workspace_changed": False}

        return {"attach_reference_catalogue": {"schema": {"type": "function", "function": {
            "name": "attach_reference_catalogue",
            "description": "Make an available application-published reference catalogue accessible in an empty-domain workspace when the user requests its data. Use available_catalogues in context. Existing imported sources, datasets and past analyses are preserved. Pins the offered immutable release; cannot replace an existing nonempty catalogue. After attachment use discover and source metadata before calculation. Never call for an unavailable catalogue.",
            "parameters": parameters}}, "handler": handler, "mutating": True, "recover": self.recover}}
