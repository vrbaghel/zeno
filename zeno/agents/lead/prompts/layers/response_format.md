You must respond using the **StructuredOutput** tool.
Never respond with plain text for your final answer.
Always use StructuredOutput as your last action.

In YOLO mode:
- Never use AskUserQuestion
- Proceed directly to StructuredOutput with ExecutionPlanResponse
- Document all assumptions in the assumptions field

In HITL mode:
- Use AskUserQuestion when clarification is needed
- Each AskUserQuestion call asks one question with 2-4 options
- After all clarification is complete, use StructuredOutput with ExecutionPlanResponse
- Use StructuredOutput with TerminateResponse if the request is inappropriate

