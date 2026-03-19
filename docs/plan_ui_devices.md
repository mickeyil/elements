**Devices Management Plan**

This plan covers:
- renaming the Status page to `Devices`
- removing redundant page labeling
- adding real web-based `New device` support
- supporting controller-owned write commands from the web app
- using a dedicated writer UDS connection strategy in the relay
- keeping the existing observer connection and snapshot/event flow untouched

This is the right next step if the web app should support device configuration in the same spirit as the TUI.

## Goals

1. Rename the sidebar/page from `Status` to `Devices`
2. Remove redundant `Device Inventory` wording
3. Add a real `New device` action in the web UI
4. Support controller-owned write commands from the web app
5. Allow both TUI and web to issue commands under a practical `last action wins` model

## Non-goals

- no generic raw command tunnel to the browser
- no offline/local-only mode
- no conflict-resolution system beyond serialized command handling
- no device edit flow yet unless explicitly added after `add_device`
- no changes to layout persistence in this round

---

## Round 1: Rename and page polish

**Goal**
Align the UI vocabulary before adding write flows.

### 1. Rename `Status` to `Devices`
Update visible UI text in:
- [App.vue](/home/mickey/dev/elements/controller/web_ui/src/App.vue)
- [StatusPage.vue](/home/mickey/dev/elements/controller/web_ui/src/pages/StatusPage.vue)

Visible changes:
- sidebar nav label: `Devices`
- page semantics: device-focused, not status-focused

The route path can remain `/` for now.

### 2. Remove `Device Inventory`
In [StatusPage.vue](/home/mickey/dev/elements/controller/web_ui/src/pages/StatusPage.vue):
- remove the `Device Inventory` toolbar label
- keep the summary counts row only

This matches the earlier discussion:
- once the user is already in `Devices`, the label is redundant

### 3. Keep the existing device grid and `⋮` menu behavior unchanged for now
Do not add a fake `New device` button yet if it does nothing.

At the end of this round, the UI is cleaner but not functionally expanded yet.

### 4. Acceptance criteria
- sidebar says `Devices`
- the page no longer says `Device Inventory`
- no behavior changes

---

## Round 2: Allow multiple writer clients in the controller UDS server

**Goal**
Relax the current single-writer rule so both TUI and web relay can send commands.

### 1. Remove the single-writer restriction in [server.py](/home/mickey/dev/elements/controller/elemctl/server.py)
Today:
- only one `ROLE_WRITER` connection is allowed
- second writer gets `writer role already in use`

Change:
- allow multiple clients to complete `hello` with `role=writer`
- keep `observer` connections read-only
- keep all command handling serialized by the existing server tick/read loop

Concretely:
- remove `self._writer_fd`
- remove the `writer role already in use` rejection path in `_handle_prehello_message()`
- remove writer-slot cleanup in `_close_client()`

### 2. Keep role semantics
Do **not** make observers writable.

Keep:
- `ROLE_OBSERVER` cannot send commands
- `ROLE_WRITER` can send commands

This preserves a clean protocol distinction.

### 3. Preserve probe behavior consciously
Today, writer hello triggers `self._service.probe_all()` in [server.py](/home/mickey/dev/elements/controller/elemctl/server.py).

With multiple writers, the simplest v1 behavior is:
- keep `probe_all()` on writer connect
- accept that each writer connection may trigger one probe-all

That matters because Round 3 starts with short-lived per-command writer connections.

For v1, that is acceptable because:
- device write commands are rare
- the simplicity is worth more than optimizing this path now

This is a deliberate tradeoff, not an oversight.

If command volume grows later, options include:
- suppressing repeated probe-all on writer connect
- moving the relay to a long-lived writer connection

### 4. Tests
In [test_service.py](/home/mickey/dev/elements/controller/tests/test_service.py):
- replace/update `test_second_writer_rejected`
- add coverage that:
  - two writer clients can connect
  - both can send commands successfully
  - observer commands are still rejected

### 5. Acceptance criteria
- multiple writer clients can connect at once
- observer clients remain read-only
- no regression in command handling

### 6. Note on sequencing
Round 2 is logically distinct, but in practice its meaningful acceptance testing happens with Round 3 because the relay writer path is its first real consumer.

Separate commits are still fine if useful for history.

---

## Round 3: Add a dedicated writer path in the web relay

**Goal**
Give the relay a safe controller-write path without disturbing the existing observer reader loop.

### 1. Keep the existing observer connection unchanged
In [web.py](/home/mickey/dev/elements/controller/elemctl/web.py):
- do not repurpose the current observer UDS loop
- it should continue to own:
  - snapshots
  - events
  - frames
  - controller-connected status

This minimizes risk and avoids reply-routing races on one socket.

### 2. Use short-lived writer connections for v1
Do **not** introduce a long-lived shared writer client yet.

Instead, for each controller write request:
1. open a fresh `UdsClient(..., role=ROLE_WRITER)`
2. send one command
3. wait for the reply
4. close the client

Why this is the right v1 choice:
- much simpler than a shared writer broker
- no reply demultiplexing
- no background reconnect state
- device commands are rare

### 3. Accept the handshake/probe cost in v1
Each short-lived writer connection performs:
- hello/protocol negotiation
- server-side `probe_all()` on writer connect

That is acceptable for now because:
- `add_device` and future `edit_device` are rare operator actions
- correctness and simplicity matter more than shaving a little latency

This should be documented in code/comments if helpful so the behavior is not surprising later.

### 4. Add a relay helper to send one controller command
In [web.py](/home/mickey/dev/elements/controller/elemctl/web.py), add a helper like:
- `_send_controller_cmd(cmd: dict) -> dict`

Responsibilities:
- create a writer `UdsClient`
- send the command
- wait for the matching reply
- return parsed reply
- surface connection/timeout errors as HTTP errors

### 5. Follow-up: explicit command-reply timeout
The first implementation can rely on the underlying `UdsClient` socket timeout.

As a follow-up, tighten `_send_controller_cmd()` so the relay enforces its own explicit reply deadline and returns a clean HTTP error if the controller connection stays open but never replies.

This is a robustness improvement, not a blocker for the initial device-management round.

### 6. Keep backend-offline behavior consistent
If the relay cannot reach the controller UDS socket for a write command:
- return an error response
- let the browser show failure normally

This is separate from browser/backend connectivity.

### 7. Acceptance criteria
- relay can issue controller commands via a separate writer path
- observer data flow remains unchanged
- no shared read/write races on one socket

### 8. Note on sequencing
Round 2 and Round 3 form one backend feature slice in practice:
- Round 2 enables multiple writers
- Round 3 consumes that capability from the relay

So combined testing/verification is expected here.

---

## Round 4: Add browser device endpoints

**Goal**
Expose browser-safe HTTP endpoints for device management.

### 1. Do not add a generic `/api/cmd`
Avoid a raw command tunnel.

Instead add specific endpoints in [web.py](/home/mickey/dev/elements/controller/elemctl/web.py), starting with:
- `POST /api/devices`

Later, if needed:
- `PATCH /api/devices/<device_uid>`

### 2. Define the `POST /api/devices` contract
Request body:
```json
{
  "device_type": "sim",
  "device_uid": "sim-left",
  "strip_id": "left",
  "length": 144
}
```

Relay translates this into controller command:
```json
{
  "id": <generated>,
  "cmd": "add_device",
  "device_type": "...",
  "device_uid": "...",
  "strip_id": "...",
  "length": 144
}
```

### 3. Validate request shape in the relay
Before forwarding:
- required fields present
- types correct
- `device_type` allowed
- `length` positive integer
- `device_uid` and `strip_id` non-empty strings

Let the controller still remain the final validator.

### 4. Response behavior
On success:
- return controller success payload or a small normalized success response
- then rely on the next snapshot/device-status updates to refresh the UI

Suggested HTTP response:
- `200` with controller reply result, or a normalized `{ ok: true }`

On failure:
- controller validation failure -> `400`
- controller unavailable -> `503`
- unexpected relay error -> `500`

### 5. Add tests
In [test_web.py](/home/mickey/dev/elements/controller/tests/test_web.py):
- valid `POST /api/devices` forwards command
- controller error becomes clean HTTP error
- invalid payload rejected before forward
- controller unavailable returns `503`

### 6. Acceptance criteria
- browser has a safe `POST /api/devices` endpoint
- controller-side validation still governs actual config mutation
- no generic command tunnel exposed

---

## Round 5: Add `New device` UI in the web app

**Goal**
Add a real browser flow for creating a device.

### 1. Update the Devices page header
In [StatusPage.vue](/home/mickey/dev/elements/controller/web_ui/src/pages/StatusPage.vue):
- add a compact page-level action in the header/toolbar area:
  - `+ New device`

This should be a visible page action, not hidden in a device menu.

### 2. Disable the action when the controller is offline
Unlike layout editing, device creation is controller-owned.

So when `controllerConnected === false`:
- disable the `New device` button
- show a simple tooltip/title:
  - `Controller offline`

No overlay is needed.

This matches the ownership boundary already established elsewhere in the UI.

### 3. Add a modal for device creation
Implement a small modal component, for example:
- [NewDeviceModal.vue](/home/mickey/dev/elements/controller/web_ui/src/components/NewDeviceModal.vue)

Fields:
- `device_type`
- `device_uid`
- `strip_id`
- `length`

Use the same basic field semantics as the TUI’s new-device flow.

### 4. Submit via `POST /api/devices`
Add a small frontend API helper, e.g. in:
- [controller/web_ui/src/lib/deviceApi.ts](/home/mickey/dev/elements/controller/web_ui/src/lib/deviceApi.ts)

Functions:
- `createDevice(payload)`

Modal behavior:
- validate locally
- submit
- show inline error on failure
- close on success

### 5. Refresh behavior
After success:
- do not manually patch device state if avoidable
- rely on the controller/relay snapshot flow to refresh the device list

Optional:
- optimistically close modal immediately on success response

### 6. Accessibility and interaction
- modal closes on `Escape`
- focus first field on open if practical
- submit button shows pending state
- cancel button available

### 7. Tests
Frontend:
- open/close modal
- submit valid payload calls API
- failed submit shows error
- button disabled when controller is offline

### 8. Acceptance criteria
- Devices page has a working `New device` action
- device creation works through the relay/controller path
- failure states are clear
- no TUI dependency required

---

## Round 6: Future device edit flow

This is not part of the first implementation, but should be the natural follow-on.

Later add:
- `PATCH /api/devices/<device_uid>`
- per-device `Edit device` action in the `⋮` menu
- modal prefilled from the current device

This should reuse the same writer-command infrastructure from earlier rounds.

---

## Open decisions already settled

- Use a **second writer path strategy** in the relay, not the existing observer socket
- Prefer **specific endpoints** over a generic `/api/cmd`
- Multiple writers are acceptable
- `last action wins` is acceptable for rare races
- `New device` should not be a fake/dead control before the backend path exists
- In v1, short-lived writer connections and their probe-all cost are acceptable

---

## Recommended sequence

1. **Round 1**: rename `Status` -> `Devices`, remove `Device Inventory`
2. **Round 2**: allow multiple writer clients in the UDS server
3. **Round 3**: add relay writer-command helper using short-lived writer connections
4. **Round 4**: add `POST /api/devices`
5. **Round 5**: add `New device` modal in the web UI
6. **Round 6**: later, add device editing

That is the revised detailed implementation plan.
