---
name: comment-style
description: Use when writing or revising C++ comments in implementation code (under src/, not drafts/), especially class headlines, public method docs, and named constants. Tightens prose toward dead-simple, signature-derived language. Skip for drafts/exploratory design docs and markdown design files (those want full prose).
---

# Comment Style (implementation code)

Write comments at the level a CS undergrad reads without effort. Don't pad
with material the function signature, the type system, or basic networking
already gives the reader.

## Principles

1. **Don't restate the signature.** `void` return implies "always succeeds";
   don't write it. `const` implies no mutation; don't write it. A name like
   `is_bound()` is self-describing; don't add "True iff..."

2. **Drop textbook material.** "UDP is connectionless," "TCP is a byte
   stream," "this is the destructor": assume the reader knows. Keep only
   the project-specific contract.

3. **Mode labels over reason-clauses.** Prefer "client mode; the usual
   choice" to "Pass a specific number only when peers need to know where to
   send to you." Labels are crisper and easier to skim.

4. **No "iff."** Use a question or plain declarative.

5. **No `--` as punctuation.** Repo rule (AGENTS.md). Use `;`, `:`, parens,
   or separate sentences.

6. **One-line method docs by default.** Two only when a return enumeration
   or a non-obvious failure mode earns it.

7. **Tabular returns** for `>0/==0/<0` shapes; don't write prose paragraphs.

8. **Drop caveats the impl can't deliver.** "(but ESP can't tell anyway)"
   means the contract should fold the cases, not document the leak.

9. **Words, not abbreviations.** "implementation," not "impl."

## Before / after (anchors)

### Trivial void method

Before:

```cpp
// Release the local port. Idempotent. Always succeeds.
virtual void close() = 0;
```

After:

```cpp
// Release the local port.
virtual void close() = 0;
```

`void` return implies "always succeeds." A close() being safe to re-call is
already idiomatic; if it weren't, the doc would say so.

### Boolean getter

Before:

```cpp
// True iff a local port is currently bound. Does not promise
// the next send/recv will succeed; only that a socket is open.
virtual bool is_bound() const = 0;
```

After:

```cpp
// Is a local port currently bound? bind()/close() are the only
// things that change this.
virtual bool is_bound() const = 0;
```

Question form replaces "iff." The "doesn't promise next send/recv will
succeed" caveat is dropped: the reader can already see that bind state and
network state are different things.

### Method with multiple failure paths

Before (8 lines, including ESP-can't-distinguish caveat and a redundant
"like send()" cross-reference):

```cpp
// Read up to n bytes from the bound socket into dst. On a returned
// count > 0, the source address is written into *src_ip and
// *src_port (both pointers must be non-null). Returns:
//   >  0  packet received; src_ip/src_port populated.
//   == 0  no useful datagram available right now (try again next
//         tick). A genuinely-empty datagram is consumed and folded
//         onto this case; the protocol never produces zero-byte
//         packets, and ESP's WiFiUDP cannot distinguish an empty
//         datagram from no datagram in any event.
//   <  0  socket error or not bound. Like send(), this does NOT
//         release the socket; close() is the only release call.
```

After:

```cpp
// Read up to n bytes from the bound socket into dst. Returns:
//   >  0  bytes received (1..n); src_ip/src_port populated.
//   == 0  nothing useful: no packet waiting, or an empty packet
//         (this protocol never sends empty packets).
//   <  0  socket error or not bound.
```

### Class headline

Before (opens with the impl list and a textbook UDP paragraph; over-
namespaces fields with "the same byte layout the OS already uses..."):

```cpp
// Abstract UDP transport. Two impls live elsewhere:
//   - PosixUdpTransport (BSD sockets on host/sim)
//   - EspUdpTransport   (Arduino WiFiUDP on firmware)
//
// IPv4-only. Addresses are uint32_t in network byte order; the same
// byte layout the OS already uses (sin_addr.s_addr, IPAddress's
// 4-byte view).
//
// UDP is connectionless: bind a local port, then send/recv with
// explicit destination/source addresses. There is no
// connect/disconnect symmetry with TcpTransport.
```

After (one-sentence purpose, usage table, then the metadata):

```cpp
// A thin wrapper around the platform's UDP socket. Lets the app send
// and receive UDP packets without caring whether it runs on Arduino
// (WiFiUDP) or POSIX (BSD sockets).
//
// Usage:
//   bind(port)               claim a local port (0 = OS-assigned).
//   send(buf, len, ip, port) send one UDP packet to a peer.
//   recv(buf, n, &ip, &port) read one packet, learn who sent it.
//   close()                  release the local port.
//
// IPv4-only. Addresses are uint32_t in network byte order.
//
// Implementations:
//   - PosixUdpTransport (host/sim, BSD sockets)
//   - EspUdpTransport   (firmware, Arduino WiFiUDP)
```

### Constant naming

Before:

```cpp
constexpr size_t UDP_TRANSPORT_MAX_DATAGRAM_BYTES = 1460;
```

After:

```cpp
// Largest payload that fits in one UDP packet. Anything bigger gets
// silently split into multiple packets by the underlying implementation.
constexpr size_t MAX_PAYLOAD_SIZE = 1460;
```

Drop the over-namespaced prefix; the surrounding namespace already scopes
it. "datagram" is the techy synonym for "packet"; pick the everyday word.

## Self-check before finishing

- Did I say anything the signature already says?
- Did I include textbook material the reader knows?
- Did I write "iff" or use `--` as punctuation?
- Is each method comment one or two lines, unless a return-value table
  earns more?
- Does the class headline lead with purpose, not with the impl list?
- Did I write "implementation" instead of "impl"?

## When NOT to apply

- `drafts/*`: exploratory; verbose by design until the contract settles.
- `*.md` design docs: full prose for new readers.
- Function bodies: internal terse comments stay.
- First-pass scaffolding the user is going to tighten in a second pass.
