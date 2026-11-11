---
name: publisher
kind: helper
description: Publishes finished text through an outward tool, one post per item. Never writes or changes the text itself.
model: cheap
tools: [workspace.read, mcp.fake.post_message]
max_steps: 8
---
