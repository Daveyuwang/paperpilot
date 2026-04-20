# Plan: Production-Grade Deep Research, Deliverable Generation & Console UX

## Problem Summary

1. **No generated title** — Deep Research/Proposal creates deliverables using the raw user input as title instead of the LLM-generated title
2. **Clarification robustness** — The pre-run clarification flow is too simplistic (only checks empty/short topic), needs LLM-driven validation
3. **Agent state feedback** — Both Paper QA and Console have agent-driven backends but the UI doesn't clearly show agent tool usage/thinking
4. **Console needs inline Deliverable + Sources panels** — Users shouldn't have to switch tabs to see/manage deliverables and sources while chatting

---

## Implementation Plan

### Phase 1: Title Generation Fix (Frontend)

**File:** `src/components/DeepResearchView.tsx`

After the result comes back and a deliverable is created, rename it using the generated title:

```
// Line ~207: After creating deliverable
if (!delId) {
  const newDel = createDeliverable(workspaceId, "deep_research", res.generated_title || input.topic || "Deep Research Brief");
  delId = newDel.id;
} else if (res.generated_title) {
  renameDeliverable(workspaceId, delId, res.generated_title);
}
```

Same fix needed in `ProposalPlanView.tsx`.

---

### Phase 2: Smarter Clarification (Backend)

**File:** `backend/app/api/deep_research.py` — `_validate_and_clarify()`

Replace the simple heuristic checks with an LLM call that evaluates:
- Is the topic specific enough to research?
- Are there ambiguous terms that need disambiguation?
- Is the scope too broad for the output length?

Emit a `needs_clarification` result with LLM-generated questions when the topic is vague. Keep the existing fast-path checks (empty topic, both sources disabled) as pre-LLM guards.

**New function:** `_llm_validate_topic()` — calls the LLM with a structured output schema to decide if clarification is needed.

---

### Phase 3: Agent State Feedback UI

**Goal:** Show users what the agent is doing — tool calls, thinking steps — in a visually distinct way.

#### 3a. Wire agentic RAG into WebSocket

**File:** `backend/app/api/ws.py`

Replace `run_agent_turn` (old graph) and `run_console_turn` with `run_agentic_turn` from `app.agents.agentic_rag.stream`. This gives us proper tool-start events.

#### 3b. New "AgentActivity" component

**File:** `src/components/shared/AgentActivity.tsx`

A compact, animated component that shows:
- Current tool being called (with friendly label)
- A subtle "thinking" animation between tool calls
- Collapsible list of completed steps

Replaces the current `ActivityStrip` in QAPanel during streaming.

#### 3c. Enhanced status messages

The agentic RAG already emits `on_tool_start` events mapped to friendly labels. The frontend `getActivityLabel()` already handles these. We just need to:
- Show tool names as discrete "steps" (not just a single label)
- Add a mini progress indicator showing tools completed

---

### Phase 4: Console Inline Panels (Key Feature)

**Goal:** Console page gets a split layout with collapsible side panels for Deliverables and Sources, so users can see and interact with them without leaving the chat.

#### Layout Change

**File:** `src/components/ConsolePage.tsx`

Transform from single-column chat to:
```
ConsolePage (flex h-full)
├── Chat Column (flex-1, min-w-0)
│   ├── Context Header
│   └── QAPanel
└── Side Panel (w-[320px], collapsible, border-l)
    ├── Tab bar: [Deliverable | Sources]
    ├── Deliverable mini-view (compact section list, status, quick actions)
    └── Sources mini-view (included sources list, toggle include/exclude)
```

#### New Components

1. **`src/components/console/ConsoleSidePanel.tsx`** — The collapsible right panel with tabs
2. **`src/components/console/MiniDeliverableView.tsx`** — Compact deliverable view showing:
   - Active deliverable title + type
   - Section list with status indicators (empty/has content/AI drafted)
   - Click section → sets focused section in store (affects console context)
   - Quick "Draft this section" button that sends a console message
3. **`src/components/console/MiniSourcesView.tsx`** — Compact sources view showing:
   - Included sources with toggle
   - Source count badge
   - Quick search/add from workspace sources

#### State Integration

The side panels read from existing stores (`deliverableStore`, `sourceStore`). When the agent modifies deliverables or sources (via tool calls), the stores update and the panels reflect changes in real-time.

A toggle button in the Console header opens/closes the side panel. State persisted in `workspaceStore`.

---

## Execution Order

1. Phase 1 (title fix) — 10 min, immediate value
2. Phase 4 (console side panels) — largest UX impact, do next
3. Phase 3 (agent feedback) — wire agentic RAG + new UI
4. Phase 2 (smarter clarification) — backend LLM validation

---

## Files Modified

- `src/components/DeepResearchView.tsx` — title fix
- `src/components/ProposalPlanView.tsx` — title fix
- `src/components/ConsolePage.tsx` — split layout
- `src/components/console/ConsoleSidePanel.tsx` — NEW
- `src/components/console/MiniDeliverableView.tsx` — NEW
- `src/components/console/MiniSourcesView.tsx` — NEW
- `src/components/shared/AgentActivity.tsx` — NEW
- `src/components/QAPanel.tsx` — use AgentActivity
- `src/store/workspaceStore.ts` — consolePanelOpen state
- `backend/app/api/ws.py` — wire agentic RAG
- `backend/app/api/deep_research.py` — smarter clarification
