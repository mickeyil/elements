# Web App

A Vue 3 + TypeScript browser interface served by `elemctl web`. Connects to the controller as an observer client, relaying state and frames to the browser. Also provides HTTP APIs for device management and layout editing.

## Architecture

```
Browser (Vue 3)  ◄──── HTTP / WebSocket ────►  elemctl web  ◄──── unix socket (observer) ────►  Controller
```

`elemctl web` is a thin relay — it does not hold playback state. The controller remains the authority. The relay forwards snapshots, events, and binary program frames to the browser, and proxies device management API calls back to the controller.

## Pages

| Route | Page | What it does |
|-------|------|-------------|
| `/` | StatusPage | Device status panel, playback info |
| `/viewer` | ViewerPage | Live animation preview (canvas rendering of RGB frames) |
| `/layouts/:deviceUid` | LayoutEditorPage | 2D pixel layout editor for simulator devices |

## Device Management API

| Method | Endpoint | Action |
|--------|----------|--------|
| `GET` | `/api/devices` | List devices |
| `POST` | `/api/devices` | Add device |
| `PATCH` | `/api/devices/:deviceUid` | Edit device |
| `DELETE` | `/api/devices/:deviceUid` | Remove device |

These proxy to the controller's `add_device` / `edit_device` / `remove_device` commands.

## Current Scope

The web app is currently observer-only for playback — it can watch animations and manage devices/layouts, but playback controls (load, play, pause, seek) are driven from the TUI. Web-side playback control is planned future work.

## Key Files

| File | Role |
|------|------|
| `controller/elemctl/web.py` | Python relay server (observer client + HTTP) |
| `web_ui/src/main.ts` | App entry point |
| `web_ui/src/App.vue` | Root component |
| `web_ui/src/router.ts` | Route definitions |
| `web_ui/src/pages/StatusPage.vue` | Device status + controls |
| `web_ui/src/pages/ViewerPage.vue` | Animation viewer |
| `web_ui/src/pages/LayoutEditorPage.vue` | Layout editor |
| `web_ui/src/lib/deviceApi.ts` | REST API client |
| `web_ui/src/lib/viewerRenderer.ts` | Canvas rendering for animation frames |
| `web_ui/src/composables/useRelayState.ts` | Relay state management |

## Running

```bash
./elemctl web   # starts relay on http://127.0.0.1:8080/
```

Requires a running controller (`./elemctl server`).
