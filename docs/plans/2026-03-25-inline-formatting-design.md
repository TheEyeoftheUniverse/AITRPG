# Inline Formatting Design

**Date:** 2026-03-25

## Goal

Allow a safe subset of inline HTML in narrative text so the WebUI can render emphasis that LLMs naturally produce.

## Decision

- Support only these inline tags in narrative-facing messages:
  `b`, `strong`, `i`, `em`, `s`, `del`
- Keep every other HTML tag escaped and visible as plain text.
- Keep theatrical effect tags on their existing parsing path. They are not part of the inline formatting whitelist.

## Rendering Model

- First escape the full assistant/system message.
- Then restore only exact whitelist tags with no attributes.
- Apply the same whitelist renderer to normal assistant bubbles and `system-echo`.
- Keep user messages and structured side panels on full escaping.

## Prompt Contract

- Tell the narrative prompt template that Markdown is still forbidden.
- Tell the prompt that only the six whitelist tags are allowed outside the existing theatrical tags.
- Explicitly forbid every other HTML tag, attribute, style, link, or script.

## Risks

- If the model emits malformed tags, they remain escaped and harmless.
- If the model emits unsupported HTML, it stays visible as raw text instead of breaking the DOM.
