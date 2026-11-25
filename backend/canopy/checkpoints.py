"""JSON-only LangGraph saver, committed atomically at completed-turn boundaries.

LangGraph's worker threads stage checkpoints in memory, never share a SQLAlchemy session.
Only the coordinating thread writes the final snapshot into the caller's transaction.
Mid-turn failures roll back all actions and replay the turn, not partially completed nodes.
"""

import json
from threading import RLock

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple


def json_copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


class TurnCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, thread_id: str, snapshot: dict | None):
        super().__init__()
        self.thread_id = thread_id
        self.snapshot = json_copy(snapshot) if snapshot else None
        self.lock = RLock()

    def _check(self, config):
        c = config["configurable"]
        if c.get("thread_id") != self.thread_id or c.get("checkpoint_ns", ""):
            raise ValueError("Checkpoint thread is outside the authorized conversation")

    def get_tuple(self, config):
        self._check(config)
        with self.lock:
            if self.snapshot is None:
                return None
            saved = json_copy(self.snapshot)
        if config["configurable"].get("checkpoint_id") not in {None, saved["checkpoint"]["id"]}:
            return None
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": self.thread_id,
                    "checkpoint_ns": "",
                    "checkpoint_id": saved["checkpoint"]["id"],
                }
            },
            checkpoint=saved["checkpoint"],
            metadata=saved["metadata"],
            pending_writes=[],
        )

    def put(self, config, checkpoint, metadata, new_versions):
        self._check(config)
        with self.lock:
            self.snapshot = json_copy({"checkpoint": checkpoint, "metadata": metadata})
        return {
            "configurable": {
                "thread_id": self.thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(self, config, writes, task_id, task_path=""):
        self._check(config)
        # No mid-turn recovery is exposed. The final END checkpoint is the durable boundary.
        # Validate plain JSON anyway; never deserialize pickle or opaque framework objects.
        json_copy(list(writes))

    def list(self, config, *, filter=None, before=None, limit=None):
        if config and not before and limit != 0:
            if saved := self.get_tuple(config):
                yield saved
