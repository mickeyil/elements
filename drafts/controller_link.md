# Controller Link

This is the device side of the controller↔device wire. It owns
discovery, the outbound TCP dial, the `DEVICE_HELLO` handshake, and the
parser that runs above the connected socket. It replaces the v2
monolith in `src/firmware/controller_connection.{h,cpp}` and the
duplicated parser in `src/network_sim.cpp`.

## Where it sits

Three sibling modules make up "everything between this device and the
controller." Each owns one concern; the App ticks them in dependency
order each loop:

```
network.poll();
link.poll();
sync.poll();
```

```
NetworkInterface     "am I on a usable LAN?"          (this doc: out of scope)
ControllerLink       "do I have an active command link?"   (this doc)
ClockSyncClient      "is SyncedClock being kept fresh?"    (drafts/synced_clock.md)
```

`NetworkInterface` brings up Wi-Fi (or, in sim, just reports up) and
reports transitions. Reconnect is its internal concern; nothing above
gets a `reconnect()` knob. `ClockSyncClient` is a separate UDP loop on
its own port that feeds `SyncedClock`. Both are described in their own
files; this doc is about the link itself.

The link does not own Wi-Fi. The earlier draft considered bundling them
on the grounds that all current network consumers are link-family, but
the *network up, no controller, background playing* state is a
first-class app state and gets muddier when Wi-Fi disappears inside the
link. Keeping them as separate siblings makes that state cheap to
express.

### The polling contract

> **The App polls modules unconditionally in dependency order; each
> module self-gates and handles prerequisite loss.**

There is *no* `if (network.is_up())` guard wrapping the link and sync
calls. Wrapping them would be a bug: each module owns internal state
that has to be reset on prerequisite loss — the link's TCP socket and
parser buffer, the sync client's filter window, outstanding round, and
UDP socket. If the App skipped their `poll()` calls while the network
was down, those resets wouldn't run, and the next time the prerequisite
came back the modules would resume from stale state.

Instead:

- `ControllerLink::poll()` reads `network.is_up()` internally. On the
  tick where the network drops it transitions to `NetworkDown` and
  tears down TCP and discovery. On the tick where the network comes
  back it starts a fresh discovery cycle.
- The App bridges link → sync with one explicit line:
  `sync.set_controller(link.controller_ip_addr())`.
  `controller_ip_addr()` returns 0 when the link is not ready, so the
  setter goes idle on link-down and re-targets on link-up without any
  conditional. `ClockSyncClient` does not depend on `ControllerLink`;
  see `drafts/synced_clock.md` for what the sync side does on each
  reset trigger.

Within one tick the cascade completes: `network.poll()` flips
`is_up()` false → `link.poll()` observes that and tears down →
the App's `sync.set_controller(...)` line passes through 0 →
`sync.poll()` is the no-op idle path. No orchestration in a wrapper
class, no conditional gating in the App.

## How a device joins

The device makes the TCP connection, not the other way around. This is
the key direction-flip from v2.

When the device boots and Wi-Fi is up, it broadcasts a small UDP HELLO
packet on the local network announcing itself: "I'm device `<uid>`,
looking for a controller." A controller listening on the discovery port
sees the HELLO, decides whether it wants to take this device, and
replies with a UDP OFFER directly back: "I'm at `<ip:tcp_port>`, here's
a nonce, this offer is valid for `<ttl_ms>` milliseconds."

The device receives the OFFER, opens an outbound TCP connection to the
controller, and immediately sends a `DEVICE_HELLO` message carrying its
identity and the nonce it just received. The controller validates that
— known UID, fresh nonce, compatible protocol version — and either
starts sending commands (the connection is good) or silently closes
(the device goes back to discovery).

Once `DEVICE_HELLO` is on the wire, the link is **Ready** in the
everyday sense: controller sends commands, device executes and ACKs,
until one side hangs up.

## Why outbound TCP

Two reasons that justify the wire break.

The device doesn't run a TCP server. No listening port means no attack
surface to harden, no duplicate-connection rejection logic, and no
fiddly "is the server bound after Wi-Fi reconnect?" dance. The device
makes one outbound connection and either has it or doesn't.

It's also the conventional shape. Outbound TCP from device to a known
server matches every IoT library, MQTT client, HTTP poller, and
embedded networking example. Server-side device is the unusual choice
that has to justify itself; client-side has zero overhead in operator
intuition.

The price is that the controller has to respond to HELLO with an OFFER
instead of just listening passively, but that's a small protocol
addition on the more capable side of the link.

## Public surface

The link exposes a deliberately small API:

```cpp
class ControllerLink {
public:
    void poll();
    bool is_ready() const;
    uint32_t controller_ip_addr() const;   // network byte order
};
```

`poll()` is the single per-tick entry point. It advances whichever
stage the link is in: drives discovery if Wi-Fi is up but no OFFER has
landed, dials TCP after an OFFER, writes `DEVICE_HELLO` once connected,
runs the parser once Ready. Safe to call when Wi-Fi is down (no-op).

`is_ready()` is the one-bit summary the App keys mode transitions off.
True iff the TCP socket is up and `DEVICE_HELLO` has been written. The
App's "controlled vs background" branch is `if (link.is_ready())`.

`controller_ip_addr()` returns the controller's IPv4 address in network
byte order, or 0 when `!is_ready()`. The App pipes this directly into
`sync.set_controller(...)` each tick: a non-zero value targets the
sync client at the active controller; the 0 sentinel takes it idle.
The TCP port the link uses internally isn't exposed (the link is the
only thing that needs it); the sync port isn't exposed because it's a
project-wide constant `ClockSyncClient` already knows from
configuration.

A `LinkState` enum exists internally (`NetworkDown / Discovering /
Connecting / Ready`) and drives `poll()`'s dispatch, but is not on the
public surface. A `state()` accessor can be added later when
diagnostics need it; until then, exposing it would be a hook with no
caller.

There's also no `controller_endpoint()` returning a struct with
ports/nonces. That information is the link's internal business.

## Internal layers

```
TcpTransport            platform TCP I/O (ESP / Posix)
    ↕ bytes
CommandParser           framing + dispatch + ACK
    ↕ HandlerResult
Session  Playback  Storage  Status  System    one per category
```

`TcpTransport` is just a connection. It connects, reads, writes,
disconnects. It knows nothing about the protocol above it — bytes in,
bytes out. Two impls: `EspTcpTransport` wraps `WiFiClient`,
`PosixTcpTransport` wraps BSD sockets. They share a five-method
interface.

`CommandParser` sits above the transport and turns the byte stream into
commands. It buffers incoming bytes, extracts complete length-prefixed
messages, dispatches each on the high nibble of the opcode, encodes
ACKs, and surfaces control signals (today: "reboot was requested") to
the outer loop. Platform-agnostic — the same source compiles on ESP
and host.

The five **handlers** are where the protocol semantics live. Each owns
exactly one opcode category (the 16 opcodes that share a high nibble),
and most are pass-throughs to a domain owner:

- **Session** applies the hardware profile (`SetProfile` is its only
  opcode after sync left for UDP).
- **Playback** routes the playback transport commands (load, start,
  jump, pause, resume, stop) into `Playback` and ACKs based on its
  return.
- **Storage** routes background-blob commands into `BackgroundStore`.
- **Status** assembles the `QueryDeviceStatus` response from current
  state.
- **System** signals reboot intent; the outer loop performs the actual
  reboot after the ACK is flushed.

Each handler returns a `HandlerResult { status, optional ACK payload,
optional control signal }`. The parser sends the ACK; handlers don't
touch the socket directly, which keeps "did this handler remember to
ACK?" out of the bug catalog.

## Becoming Ready is its own ritual

Connecting and identifying happen *before* the parser starts running.
The sequence inside `poll()` when an OFFER lands is:

1. The discovery side (private to this module) has stashed a fresh
   `ControllerOffer` snapshot.
2. `TcpTransport::connect(controller_ip, tcp_port)` is called. The
   implementation has a hard cap on how long this can block (~500ms on
   ESP via `WiFiClient::connect(ip, port, timeout_ms)`).
3. On success, `send_device_hello(transport, identity, offer.nonce)`
   writes a single framed `DEVICE_HELLO` message carrying the device's
   UID, boot token, the OFFER nonce, and the protocol version.
4. The link is now **Ready**. Subsequent `poll()` ticks drive
   `CommandParser::poll()` for the rest of the lifetime of this
   connection.

If `DEVICE_HELLO` is rejected, the controller closes the socket. The
device notices via the next `read()` returning `< 0`, the parser
reports `disconnected`, the link drops back to **Discovering**.
There's deliberately no separate ACK for `DEVICE_HELLO` — "the
controller is still here a moment later" is the de-facto acceptance
signal, and avoiding the ACK keeps the handshake to one round-trip.

There's a small window between writing `DEVICE_HELLO` and the
controller accepting (or rejecting) it during which `is_ready()` is
true. Under the device-initiated sync flow (see `synced_clock.md`),
that means a sync ping might briefly fire at a controller that's about
to close. That's fine: sync packets carry UID and boot_token (same
robustness the OFFER nonce gives the TCP handshake), and a controller
that doesn't recognize them just discards. We are *not* reintroducing a
`DEVICE_HELLO_ACK` to close that window.

## ACKs and errors

Every command ACKs. The v2 fire-and-forget shape on `Start`/`Pause`/etc.
is gone — the controller needs to know when a command was rejected
(common case: "synced program rejected because the clock isn't leased")
so it doesn't get stuck in a split-brain where it thinks the device is
playing and the device thinks the controller is confused.

The ACK status set is small but covers the rejections that actually
happen: `Ok`, `Error`, `WrongState`, `ProfileMismatch`, `BadPayload`,
`UnknownCommand`, `Unsynced`.

A malformed *frame* (length zero, length over the cap, socket EOF)
drops the connection — the sender is corrupt or the peer is gone. A
well-formed frame with an unknown opcode just ACKs `UnknownCommand`
and the connection continues; that's the normal protocol-evolution
case (controller knows about a command this firmware doesn't).

## Identity

UID is the canonical identifier. There is no numeric `device_id` in v3
— the UID alone names the device on the wire, in logs, and in
controller state.

UIDs are **fixed 16-byte ASCII slots**, null-padded if shorter. Two
prefix conventions tell you what kind of device you're looking at
without any out-of-band lookup:

- **`esp-XXXXXXXXXXXX`** — real ESP devices. The 12 hex chars are the
  last six bytes of the chip's MAC address. Always exactly 16 bytes
  long, composed by the device's ESP-side factory.
- **`sim-...........`** — sim binary processes. The operator (or its
  config) passes a full UID like `sim-foo` to the launcher; the
  launcher hands it to the sim binary verbatim, the binary copies it
  into the wire slot, and the slot is null-padded to 16 bytes. The
  `sim-` prefix is **launcher/config policy** — the device binary
  itself is shape-only and trusts what it is given. Controller-side
  validation on `DEVICE_HELLO` is the second line of defence.

Parser rule on receive: read exactly 16 bytes, trim at the first `\0`,
require any remaining bytes to also be `\0`, and require the trimmed
content to be printable ASCII. Then compare normalized strings against
the controller's configured device list.

The `DEVICE_HELLO` payload carries four things:

- **uid[16]** — fixed-size, as above.
- **boot_token** — a fresh 32-bit random value generated on every boot.
  Lets the controller detect "device rebooted, drop stale state on my
  end."
- **offer_nonce** — the nonce the controller sent in OFFER. Lets the
  controller reject TCP connections that aren't following a fresh
  OFFER (stale-OFFER race after a controller restart, etc.).
- **protocol_version** — the wire protocol generation (`3` for v3).
  The controller can refuse devices whose version it doesn't
  understand.

Notably absent: any cryptographic auth (HMAC, TLS). For a LAN-scoped
controller with a small device fleet, the threat model doesn't justify
the complexity (mbedTLS dependency, key provisioning, key rotation,
debugging cost). If a remote (VPS) controller ever becomes a real
plan, that's the moment auth earns its weight — and adding it is a v4
wire break, which is fine in this project's "no legacy" stance.

`protocol_version` is the cheap escape hatch that makes v4 possible
without painful migrations. Always include it; ignore it for now.

### What the controller config holds (not on the wire)

Two things live in the controller's config and never travel over the
link:

- **`strip_id`** — the program-routing identifier. The DSL/composer
  refers to outputs by `strip_id` ("main", "left", "kitchen"). It maps
  one DSL strip name to one configured device. Programmatic, not for
  display.
- **`label`** — an optional UI display string ("Living Room Left").
  When present, controller UIs show the label; when absent, they fall
  back to the UID.

Keeping these controller-side means the wire stays minimal (one
identifier, the UID) and renaming a device for the UI doesn't touch
the device or the protocol.

## Sim parity

`network_sim.cpp`'s ~660-line monolith collapses to: socket setup, a
`PosixTcpTransport` and `PosixUdpTransport`, the same shared
parser/handlers/`ClockSyncClient` that firmware uses, and the sim-only
RGB frame send loop. The duplicate parser/dispatch/framing is **deleted
in the same change** — no half-migrated state.

The system handler's reboot has two impls: `EspSystemPlatform` calls
`ESP.restart()` after a short flush delay; `SimSystemPlatform` exits
with the reboot sentinel code so the launcher re-execs the binary.
RAM is wiped naturally, `boot_token` is regenerated through the
normal startup path, and file-backed background storage survives
because the file does. The launcher contract is tracked in
`drafts/TODO.md` § "Sim launcher: process-restart on reboot command".

Everything else is shared: the parser, all five handlers, `WireReader`,
`HandlerResult`, opcode constants, `send_device_hello`,
`ClockSyncClient`. Sim runs the same sync code as firmware against a
controller process on the same host; the measured offset is ~0 because
the clocks happen to be the same machine, and that's the truth, not a
stub.

## Out of scope for this module

- **Wi-Fi / LAN connectivity** — `NetworkInterface` brings the network
  up; this module assumes there's a network and gates its work on
  `network.is_up()`.
- **Clock sync** — `ClockSyncClient` is a sibling that ticks alongside
  this module. The App pipes `controller_ip_addr()` into
  `sync.set_controller(...)` each tick; the sync client is otherwise
  standalone. See `drafts/synced_clock.md`.
- **Sim frame UDP** — sim binary only. Real ESP firmware writes pixels
  to FastLED, not the network. The sim sends RGB previews to the
  controller's `sim_frame_port` (6042) for UI rendering. The frame
  header carries `uid[16] + frame_index:u32 + t_program:f32 + rgb...`
  so the controller can demux multiple sims (loopback IP collisions
  rule out source-IP demux). This is its own protocol, not controller
  link.

## Open decisions

These are pinned but not yet settled. Most don't block writing the
link itself; they show up where flagged.

- **Reboot signal path out of `poll()`** — either `ControllerLink::poll()`
  returns a small result struct, or the App reads a getter after each
  tick. Same content either way; pick whichever reads better in the
  App's loop.
- **`ClockSyncClient` is standalone.** It takes neither
  `ControllerLink&` nor any link-readiness getter. The App bridges
  the two with `sync.set_controller(link.controller_ip_addr())` each
  tick; remote clock epoch identity travels in the PONG payload as a
  `controller_boot_token` field. See `drafts/synced_clock.md`.
- **Whether to expose `state()` and `LinkState` publicly** — deferred
  until diagnostics need it. Easy to add when something starts logging
  link state transitions.
- **Whether to add a `ConnectionLayer` wrapper** — defer until the App
  orchestration of `(network, link, sync)` proves duplicated between
  firmware and sim. Currently the App just ticks them in order.
- **Trimmed `SessionHandler` fate** — one opcode left after
  `SyncLease` moved off TCP. Cosmetic; could fold into another
  handler.

## Open items (engineering work, separate from decisions)

- `Playback::handle_start` / `handle_resume` / `handle_jump` need to
  return status. Tracked in `drafts/TODO.md` § Playback.
- `BackgroundStore` needs a sim impl. Backing is a file at a stable
  path; re-exec on reboot preserves it the same way ESP flash does.
  Interface is defined in `drafts/background_store.h`.

---

## Appendix A: wire framing

Length-prefixed framing for both directions:

```
┌──────────────┬──────┬─────────────┐
│ length: u32  │ type │  payload    │
│ (LE)         │ u8   │  (varies)   │
└──────────────┴──────┴─────────────┘
```

`length` counts `type + payload` (not itself). All multi-byte values
are little-endian. All floats must be finite.

## Appendix B: opcodes and payloads

Opcodes are organized by high nibble (`opcode >> 4`); see "Internal
layers" above.

| Opcode | Cmd                 | Direction       | Payload                                                            |
|--------|---------------------|-----------------|--------------------------------------------------------------------|
| 0x00   | `DeviceHello`       | device → ctrlr  | `char uid[16], u32 boot_token, u32 offer_nonce, u8 protocol_version` |
| 0x01   | `SetProfile`        | ctrlr → device  | `u16 strip_length`                                                 |
| 0x10   | `Load`              | ctrlr → device  | `u8[] blob`                                                        |
| 0x11   | `Start`             | ctrlr → device  | `i64 program_start_us` (synced: anchor; unsynced: ignored)         |
| 0x12   | `Jump`              | ctrlr → device  | `f32 t_program`                                                    |
| 0x13   | `Pause`             | ctrlr → device  | (empty)                                                            |
| 0x14   | `Resume`            | ctrlr → device  | `i64 program_start_us`                                             |
| 0x15   | `Stop`              | ctrlr → device  | (empty)                                                            |
| 0x20   | `StoreBackground`   | ctrlr → device  | `u16 strip_length, u32 expected_crc32, u8[] blob`                  |
| 0x21   | `ClearBackground`   | ctrlr → device  | (empty)                                                            |
| 0x30   | `Reboot`            | ctrlr → device  | (empty)                                                            |
| 0x40   | `QueryDeviceStatus` | ctrlr → device  | (empty); ACK payload below                                         |
| 0x80   | `Ack`               | both directions | `u8 status` followed by optional payload                           |

`uid[16]` is null-padded ASCII. See "Identity" above for the parser
rule.

`0x02` is reserved (was `SyncLease` in an earlier v3 draft). Sync now
lives entirely on UDP, see `drafts/synced_clock.md`.

`QueryDeviceStatus` ACK payload (14 bytes):

| u8   | u8    | u16                  | u16                       | u32                  | u32              |
|------|-------|----------------------|---------------------------|----------------------|------------------|
| mode | flags | profile_strip_length | background_strip_length   | background_blob_len  | background_crc32 |

`mode`: 0 attached_controlled, 1 detached_grace_hold, 2 detached_blank,
3 detached_background. `flags`: bit0 profile_present, bit1
background_present.

`DeviceHello` is sent device → controller as the first TCP message
after connect; it never appears in the inbound dispatch. If the
controller ever sends opcode 0x00 to the device, the parser ACKs
`UnknownCommand` (but this is a controller bug — the device doesn't
speak that direction for 0x00).

Unknown high nibbles (anything not `0x0_..0x4_`), and stray inbound
0x8_ replies, get `AckStatus::UnknownCommand`.

## Appendix C: discovery packets (UDP, port 6040)

These are owned by this module (the discovery side of the link), kept
in the appendix because they're reference data rather than narrative.

**HELLO** (device → broadcast, every ~500 ms while looking for a
controller):

| u16 magic | char uid[16] | u32 boot_token | u8 protocol_version |

`magic` = `0x454C` ('EL'). Total: 23 bytes.

**OFFER** (controller → device, unicast, in response to a HELLO it
wants to claim):

| u16 magic | u8 type=0x02 | u32 controller_ipv4_be | u16 tcp_port | u32 nonce | u16 ttl_ms |

Total: 15 bytes.

**REJECT** (controller → device, unicast, for duplicate UID):

| u16 magic | u8 type=0x01 | u8 reason |

`reason` = 0x01 for duplicate UID. Device backs off HELLOs for 5 s on
receipt.

## Appendix D: ACK status codes

| Code | Name              | When                                                        |
|------|-------------------|-------------------------------------------------------------|
| 0    | `Ok`              | success                                                     |
| 1    | `Error`           | generic failure (alloc, write, internal)                    |
| 2    | `WrongState`      | command requires different playback state                   |
| 3    | `ProfileMismatch` | blob's strip_length doesn't match the active profile        |
| 4    | `BadPayload`      | payload too short, malformed, or non-finite floats          |
| 5    | `UnknownCommand`  | opcode not recognized by any handler                        |
| 6    | `Unsynced`        | synced program rejected because `is_synced()` is false      |

## Appendix E: port allocation

Four UDP/TCP ports total, in a contiguous block for friendly firewall
rules:

| Port | Protocol | Purpose                                                                  |
|------|----------|--------------------------------------------------------------------------|
| 6040 | UDP      | Discovery: HELLO broadcast, OFFER unicast, REJECT unicast                |
| 6041 | TCP      | Controller listens; devices dial in (controller-link)                    |
| 6042 | UDP      | Sim → controller frame previews (sim-only, dev convenience)              |
| 6043 | UDP      | Clock sync: device-initiated ping/pong (see drafts/synced_clock.md)      |

The controller config carries all four numbers. The device side gets
the TCP port from each OFFER (so the device doesn't hardcode anything
about the controller). The discovery and sync ports are the
well-known numbers the device side has at compile time. The sim frame
port lives only in the sim's CLI flags and the controller's UI
receiver; real ESP firmware never references it.
