You must respond with **exactly one** JSON object and **nothing else**.

## LeadAgentResponse schema

Your response JSON must match this shape:

{
  "type": "execution" | "clarification" | "terminate",

  "question": string | null,
  "context": string | null,
  "options": {
    "option_a": { "label": string, "description": string },
    "option_b": { "label": string, "description": string },
    "option_c": { "label": string, "description": string }
  } | null,

  "task_summary": string | null,
  "rooms": [{ "name": string, "description": string }] | null,
  "tasks": [{
    "id": string,
    "title": string,
    "description": string,
    "type": "foundational" | "implementation" | "validation" | "integration",
    "agent_type": "requirements" | "coding" | "testing" | "merge" | "lead",
    "room": string,
    "depends_on": [string],
    "parallel_group": string | null,
    "checkpoint_before": boolean
  }] | null,
  "assumptions": [string] | null,
  "diary_entry": {
    "summary": string,
    "decisions": [string],
    "assumptions": [string],
    "dependencies": [string],
    "open_issues": [string],
    "room": string
  } | null,

  "reason": string | null
}

## When to use each type
- Use **clarification** when one key ambiguity blocks correct planning (HITL mode only).
- Use **execution** when you can produce a complete execution plan.
- Use **terminate** when the request is inappropriate, unsafe, or impossible to proceed with.

## Hard requirements
- Output must be valid JSON.
- Do not include any text outside the JSON object.

