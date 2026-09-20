"""Explicit agent tool for promoting a verified web dataset to a shared release."""

from __future__ import annotations

import jsonschema

from agentic_analytics.agent.schemas import obj
from agentic_analytics.agent.tools.documents import OFFICIAL_SOURCE_REGISTRY
from agentic_analytics.lakehouse.shared import SharedLakehouse, SharedLakehouseError
from agentic_analytics.lakehouse.store import StoreError


class SharedLakehouseTools:
    """Expose one narrow, recoverable global mutation to the runtime."""

    TOOL_NAME = "promote_dataset_to_shared_lakehouse"

    def __init__(self, store, workspace_id, *, shared=None):
        self.store, self.workspace_id = store, workspace_id
        self.store.workspace(workspace_id)
        self.shared = shared or SharedLakehouse(store)

    def promote(self, dataset_id, reason):
        return self.shared.promote(
            self.workspace_id,
            dataset_id,
            official_sources=OFFICIAL_SOURCE_REGISTRY,
            reason=reason,
        )

    def recover(self, arguments, intent):
        try:
            result = self.shared.recover_promotion(arguments["dataset_id"])
            if result is not None and arguments.get("reason"):
                result["reason_acknowledged"] = arguments["reason"].strip()
            return result
        except SharedLakehouseError as exc:
            return {"status": "blocked", "code": exc.code, "message": str(exc),
                    "publication_performed": False}
        except (StoreError, OSError, ValueError, TypeError, KeyError) as exc:
            return {"status": "blocked", "code": "SHARED_PROMOTION_RECOVERY_FAILED", "message": str(exc),
                    "publication_performed": False}

    def extra_tools(self):
        parameters = obj({
            "dataset_id": {
                "type": "string",
                "pattern": r"^dataset_[0-9a-f]{64}$",
                "description": "An already published dataset in the current workspace.",
            },
            "reason": {
                "type": "string", "minLength": 10, "maxLength": 500,
                "description": "Short reason this verified official dataset should become shared reference data.",
            },
        }, ["dataset_id", "reason"])

        def handler(arguments):
            try:
                jsonschema.validate(arguments, parameters)
                return self.promote(**arguments)
            except jsonschema.ValidationError as exc:
                return {"status": "blocked", "code": "INVALID_ARGUMENTS", "message": exc.message,
                        "publication_performed": False}
            except SharedLakehouseError as exc:
                return {"status": "blocked", "code": exc.code, "message": str(exc),
                        "publication_performed": False}
            except (StoreError, OSError, ValueError, TypeError, KeyError) as exc:
                return {"status": "blocked", "code": "SHARED_PROMOTION_FAILED", "message": str(exc),
                        "publication_performed": False}

        return {self.TOOL_NAME: {
            "schema": {"type": "function", "function": {
                "name": self.TOOL_NAME,
                "description": (
                    "Promote one already published, source-verified dataset into the immutable shared lakehouse "
                    "release for future finance workspaces. This is a global persistent write. Call it only when "
                    "the current user explicitly asks to add, publish or save the dataset permanently to the "
                    "shared/main lakehouse. Ordinary web research and workspace analysis never authorize it."
                ),
                "parameters": parameters,
            }},
            "handler": handler,
            "mutating": True,
            "recover": self.recover,
        }}
