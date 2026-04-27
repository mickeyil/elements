# Controller Link

Device-side architecture for receiving commands from the controller over TCP
and dispatching them. Replaces the v2 monolith
`src/firmware/controller_connection.{h,cpp}` and the duplicate parser in
`src/network_sim.cpp`.

The wire format itself is in `docs/protocol.md` (v2). This file is the v3
redesign and supersedes that wire-format material once implemented.

## Goals

- separate the wire layer from playback / storage / system concerns
- one shared parser across firmware and sim — no duplicate switch statements
- explicit ACK status for every command
- handler boundaries that mirror domain ownership

## Wire framing (unchanged from v2)

```
┌──────────────┬──────┬─────────────┐
│ length: u32  │ type │  payload    │
│ (LE)         │ u8   │  (varies)   │
└──────────────┴──────┴─────────────┘
```

`length` counts `type + payload`, not itself. Total bytes on the wire =
`4 + length`. Replies use the same framing with `type = 0x80` (Ack).

## Wire payloads

All multi-byte values are little-endian. All floats must be finite. The
parser rejects malformed payloads with `BadPayload`.

### Inbound

| Opcode | Cmd                 | Payload                                                                |
|--------|---------------------|------------------------------------------------------------------------|
| 0x00   | `SetProfile`        | `u16 strip_length`                                                     |
| 0x01   | `Attach`            | `u16 device_id, u16 frame_port`                                        |
| 0x02   | `SyncLease`         | `u16 seq, u32 boot_token, i64 offset_us, u32 valid_for_ms`             |
| 0x10   | `Load`              | `u8[] blob`                                                            |
| 0x11   | `Start`             | `i64 program_start_us` (synced: remote-clock anchor; unsynced: ignored, anchor = `now_local_us`) |
| 0x12   | `Jump`              | `f32 t_program`                                                        |
| 0x13   | `Pause`             | (empty)                                                                |
| 0x14   | `Resume`            | `i64 program_start_us` (synced: remote-clock anchor; unsynced: ignored, anchor preserves cursor) |
| 0x15   | `Stop`              | (empty)                                                                |
| 0x20   | `StoreBackground`   | `u16 strip_length, u32 expected_crc32, u8[] blob`                      |
| 0x21   | `ClearBackground`   | (empty)                                                                |
| 0x30   | `Reboot`            | (empty)                                                                |
| 0x40   | `QueryDeviceStatus` | (empty); ACK payload below                                             |

Changes from v2: `Load` drops `u16 gen`. `Jump` becomes just `f32 t_program`
(was `i64 t0, f32 t_rel, u16 gen`). `SyncLease` adds `u32 valid_for_ms`
(was the void `SyncResult` in v2). Every command above ACKs in v3 — the v2
silent-no-op shape on Start / Jump / Pause / Resume / Stop is gone.

### Outbound (Ack only, opcode 0x80)

`u8 status` followed by an optional payload defined per command. Today
only `QueryDeviceStatus` carries an ACK payload:

| u8  | u8    | u16                  | u16                       | u32                  | u32              |
|-----|-------|----------------------|---------------------------|----------------------|------------------|
| mode | flags | profile_strip_length | background_strip_length   | background_blob_len  | background_crc32 |

`mode`: 0 attached_controlled, 1 detached_grace_hold, 2 detached_blank,
3 detached_background.
`flags`: bit0 = profile_present, bit1 = background_present.

## Category map

Opcodes are organized by high nibble (`opcode >> 4`). One handler per
category. **Renumbered from v2** — see "Why renumber" below.

| Nibble | Category | Opcodes                                                  |
|--------|----------|----------------------------------------------------------|
| `0x0_` | session  | `SetProfile` 0x00, `Attach` 0x01, `SyncLease` 0x02       |
| `0x1_` | playback | `Load` 0x10, `Start` 0x11, `Jump` 0x12, `Pause` 0x13, `Resume` 0x14, `Stop` 0x15 |
| `0x2_` | storage  | `StoreBackground` 0x20, `ClearBackground` 0x21           |
| `0x3_` | system   | `Reboot` 0x30                                            |
| `0x4_` | status   | `QueryDeviceStatus` 0x40                                 |
| `0x8_` | replies  | `Ack` 0x80 (outbound only)                               |

Unknown high nibbles, and 0x8_ inbound (replies sent by mistake), get
`AckStatus::UnknownCommand`.

### Why renumber

The v3 wire is already breaking for substantive reasons (`gen` removed,
`SyncResult` → `SyncLease`, playback commands gain ACKs, status codes
expand). Renumbering rides on that single break — high-nibble = category
becomes a permanent design property instead of a comment that explains why
0x16–0x18 are accidentally mixed.

## Architecture

```
TcpTransport (esp / posix impls)         platform TCP I/O
    ↓ bytes
CommandParser                            framing + dispatch + ACK
    ↓ HandlerResult
Session  Playback  Storage  Status  System          one per category
    ↓        ↓         ↓        ↓       ↓
SyncedClock Playback BgStore  reads  SystemPlatform (esp / sim impls)
```

### Dispatch

`CommandParser::poll()`:

1. Drain bytes from `TcpTransport` into the inbound buffer.
2. While at least one full message is buffered:
   - parse `[u32 length][u8 cmd_type][payload]`
   - dispatch on `cmd_type >> 4`:

```cpp
switch (cmd_type >> 4) {
    case 0x0: return _session.handle(cmd_type, reader);
    case 0x1: return _playback.handle(cmd_type, reader);
    case 0x2: return _storage.handle(cmd_type, reader);
    case 0x3: return _system.handle(cmd_type, reader);
    case 0x4: return _status.handle(cmd_type, reader);
    default:  return HandlerResult::error(AckStatus::UnknownCommand);
}
```

   - encode and send the ACK
   - apply any control signal (today: reboot)

**Frame lifecycle:**

- `length == 0` → close (no valid frame can have zero bytes).
- `length > TCP_MSG_MAX` → close (corrupt sender or attack).
- `length` valid but bytes still arriving → wait. Partial frames are
  normal, especially mid-`Load`. The parser grows the buffer to fit and
  comes back next tick.
- Socket EOF / read error → close.
- Well-formed frame with unknown opcode → ACK `UnknownCommand`, connection
  stays up.

### Static wiring

Handlers are passed by reference at parser construction. No registration
table, no dynamic dispatch, no plugin registry:

```cpp
CommandParser parser(transport, session, playback, storage, status, system);
```

### HandlerResult

Every handler returns `{ AckStatus, optional ack_payload, optional control_signal }`.
The parser sends the ACK; handlers do not touch the socket directly. This
removes "did this handler remember to ACK?" as a class of bug.

## Handler responsibilities

### Session (`0x0_`)

Owns: attach state, `device_id`, `frame_port`, `controller_ipv4`,
`last_sync_seq`, `boot_token`. Other handlers read attach state to gate
their commands; the firmware/sim outbound UDP path reads `controller_ipv4`
and `frame_port` to know where to send frames.

`SetProfile` while attached with a **different** profile resets the attach
state (the controller is no longer addressing the device it was attached
to — strip length etc. changed). Same-profile `SetProfile` is a no-op on
attach state. Pre-attach `SetProfile` just applies. The reset is
observable through `is_attached() == false` after the ACK; the controller
is expected to re-`Attach` if it wants to keep talking.

`Attach` records the controller's IPv4 from `TcpTransport::peer_address()`
so the outbound UDP frame loop has the destination.

`SyncLease` validates `boot_token` and seq freshness, then converts
`valid_for_ms → us` and calls
`SyncedClock::apply_sync_offset(offset_us, valid_for_us)`.

### Playback (`0x1_`)

Pure pass-through to `Playback` — no per-command state. **Requires**
`Playback::handle_start / handle_jump / handle_resume` to return a status
instead of `void`, so this handler can ACK truthfully. See the API change
note in `playback.md`.

### Storage (`0x2_`)

Pass-through to `BackgroundStore`. Gates on attached.

### System (`0x3_`)

Returns `HandlerResult::reboot()` on `Reboot` — ACK first, signal via
`PollResult::reboot_requested`. The handler does **not** hold a
`SystemPlatform&`; the firmware loop / sim main owns `SystemPlatform` and
calls `platform.reboot()` after a short flush delay so the ACK bytes
reach the controller. Keeping the platform reference out of the handler
avoids two owners of "when does the reboot actually fire" — the parser
layer just reports intent.

### Status (`0x4_`)

Read-only across `Playback`, `BackgroundStore`, and the current device-mode
tag. Returns ACK-with-payload.

## ACK status codes

| Code | Name              | When                                                        |
|------|-------------------|-------------------------------------------------------------|
| 0    | `Ok`              | success                                                     |
| 1    | `Error`           | generic failure (alloc, write, internal)                    |
| 2    | `WrongState`      | command requires attached / different playback state        |
| 3    | `ProfileMismatch` | blob's strip_length doesn't match the active profile        |
| 4    | `BadPayload`      | payload too short, malformed, or non-finite floats          |
| 5    | `UnknownCommand`  | opcode not recognized by any handler                        |
| 6    | `Unsynced`        | synced program rejected because `SyncedClock::is_synced()` is false |

## Sim parity

Two platform-specific interfaces:

- `TcpTransport` — `EspTcpTransport` (WiFiServer/WiFiClient) vs
  `PosixTcpTransport` (BSD sockets).
- `SystemPlatform` — `EspSystemPlatform` (deferred `ESP.restart`) vs
  `SimSystemPlatform` (ACK → close TCP → reset session → clear sync lease →
  new `boot_token` → resume discovery; no process exit).

`BackgroundStore` is also platform-specific (NVS+LittleFS on ESP; in-process
persistent backing on sim) — see `TODO: BackgroundStore portability` below.

`network_sim.cpp`'s ~660-line `main` collapses to: socket setup +
`PosixTcpTransport` + handler wiring + UDP HELLO loop + UDP frame send. The
duplicate parser/dispatch/framing is **deleted in the same change**, not
left behind for follow-up.

## Out of scope for this draft

- UDP frame send (device → controller). Stays in `firmware_app` /
  `network_sim_main`.
- UDP discovery (HELLO + clock-sync ping). Stays in `DiscoveryService` /
  the equivalent in sim. Discovery constants do not move into
  `wire_constants.h`.
- Telemetry channel (Serial logging on ESP).
- Outbound side opcodes other than `Ack`.

## Open items

- **`Playback` API change.** `handle_start` / `handle_resume` /
  `handle_jump` return void today; need to return status. Coordinate with
  `playback.md`.
- **`BackgroundStore` impl on sim.** The shared interface is in
  `background_store.h`; sim backing must survive a simulated reboot
  (ESP flash does). Backing choice — file under a stable path, in-process
  store with explicit reset hooks, etc. — is an impl-phase decision.
- **Hardware-profile application from `SessionHandler::SetProfile`.**
  `Playback::apply_hardware_profile` is the existing entry; decide whether
  Session holds a `Playback&` for that one call or a narrower
  `ProfileSink` interface.
- **Backpressure on slow ACK drain.** Today firmware blocks on send.
  Confirm the new transport interface preserves that, or define explicit
  behavior on partial write.
- **Migration order.** Build parser + reader + handler stubs first
  (testable in isolation against byte streams), implement handlers, then
  flip ESP and sim in one change with `network_sim.cpp`'s duplicate parser
  deleted in the same commit.
