"""Task plans for multi-step requests (local working state, not memory)."""

from .registry import Tool, ToolError

STEP_STATUSES = ["done", "failed", "skipped", "pending"]


class TaskPlanner:
    def __init__(self, store):
        self.store = store

    def create(self, title, steps):
        steps = [s.strip() for s in steps if s.strip()]
        if not steps:
            raise ToolError("A plan needs at least one step.")
        task_id = self.store.create_task(title.strip(), steps)
        return self.store.get_task(task_id)

    def update_step(self, task_id, step, status, note=None):
        if not self.store.get_task(task_id):
            raise ToolError(f"No task with id {task_id}.")
        if not self.store.update_step(task_id, step, status, note):
            raise ToolError(f"Task {task_id} has no step {step}.")
        return self.store.get_task(task_id)

    def list(self, status=None):
        return {"tasks": self.store.list_tasks(status)}

    def cancel(self, task_id):
        if not self.store.set_task_status(task_id, "cancelled"):
            raise ToolError(f"No task with id {task_id}.")
        return self.store.get_task(task_id)


def build_task_tools(planner):
    return [
        Tool("create_task_plan",
             "For a request needing several actions, record a plan of explicit steps before acting. "
             "Then perform the steps with tools and mark each one with update_task_step.",
             {"type": "object", "properties": {
                 "title": {"type": "string", "minLength": 1, "maxLength": 120},
                 "steps": {"type": "array", "minItems": 1, "maxItems": 15,
                           "items": {"type": "string", "minLength": 1, "maxLength": 200}}},
              "required": ["title", "steps"], "additionalProperties": False},
             planner.create),
        Tool("update_task_step",
             "Record the REAL outcome of a plan step (done/failed/skipped) with a short note. "
             "Never mark a step done unless its tool call succeeded.",
             {"type": "object", "properties": {
                 "task_id": {"type": "integer", "minimum": 1},
                 "step": {"type": "integer", "minimum": 1, "maximum": 15},
                 "status": {"type": "string", "enum": STEP_STATUSES},
                 "note": {"type": "string", "maxLength": 300}},
              "required": ["task_id", "step", "status"], "additionalProperties": False},
             planner.update_step),
        Tool("list_tasks", "List recent task plans and their step status.",
             {"type": "object", "properties": {
                 "status": {"type": "string", "enum": ["active", "completed", "failed", "cancelled"]}},
              "required": [], "additionalProperties": False},
             planner.list),
        Tool("cancel_task", "Cancel a task plan by id.",
             {"type": "object", "properties": {"task_id": {"type": "integer", "minimum": 1}},
              "required": ["task_id"], "additionalProperties": False},
             planner.cancel),
    ]
