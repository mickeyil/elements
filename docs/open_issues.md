# Open Issues

## Scene workflow: path-based submission exists, scene catalog does not

**Status:** Deferred for follow-up work.

### What is implemented

- Backend `load_scene` is implemented.
- The TUI has a `/scene PATH` command that reads a local scene file and submits it directly.
- Scene validation and execution work for the current one-session scene model.

Example of what works today:

```text
/scene scenes/validate.json
```

### What is still missing

- There is no scene catalog/browser in the TUI.
- There is no saved-scene selection flow similar to `/programs`.
- There is no built-in way to browse or pick saved scenes by name.

Examples of future flows that do not exist yet:

```text
/scenes
```

### Why this matters

Right now scenes can be submitted by file path, but they are not yet reusable named assets inside the TUI. That makes it harder to:

- keep standard validation scenes around
- reload the same scene repeatedly
- manage sim-only / esp-only / mirrored presets
- version scene definitions in a simple operator-facing workflow

### Suggested follow-up direction

When this is revisited, the likely next step is one of:

1. A TUI scene catalog/browser built on top of saved scene definitions.
2. Optional scene discovery conventions if you want the TUI to look in a standard scenes directory.

This is intentionally deferred until after the next few changes.
