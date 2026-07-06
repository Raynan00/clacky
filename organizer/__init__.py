"""Desktop organizer — the 'hands' capability.

Public surface:
    build_plan(root, llm_complete) -> Plan      # dry-run, no side effects
    execute(plan) -> Batch                      # applies a confirmed plan
    undo.undo_last() -> str                     # reverses the last batch
"""
from .planner import build_plan, Plan, Move
from .executor import execute
from . import undo, guards

__all__ = ["build_plan", "execute", "undo", "guards", "Plan", "Move"]
