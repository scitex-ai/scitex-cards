"""Safe failures for explicit canonical Cards row primitives."""

from ._db_export import ExportRefused


class CardAlreadyExistsError(RuntimeError):
    """An explicit insert-absence fence lost; no row effects were committed."""

    def __init__(self, task_id: str, found: int | None):
        self.task_id = task_id
        self.found = found
        super().__init__(
            f"insert-if-absent refused for task {task_id!r}: "
            "row already exists; nothing written"
        )


class CardPayloadShapeError(ExportRefused):
    """An existing row has an invalid canonical card object, never absence."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.public_summary = "The canonical card payload is unreadable."
        super().__init__(f"task {task_id!r}: canonical-card-payload-not-object")
