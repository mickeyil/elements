# Web App

A Vue 3 + TypeScript browser interface served by `elemctl web`. The Python web UI server connects to the controller as an observer client, forwards state and frames to the browser, and provides HTTP APIs for device management and layout editing.

## Architecture

```
Browser (Vue 3)  ◄──── HTTP / WebSocket ────►  elemctl web  ◄──── unix socket (observer) ────►  Controller
```

`elemctl web` runs the web UI server — it does not hold playback state. The controller remains the authority. The server forwards snapshots, events, and binary program frames to the browser, and proxies device management API calls back to the controller.

## Pages

| Route | Page | What it does |
|-------|------|-------------|
| `/` | StatusPage | Device status panel, playback info |
| `/viewer` | ViewerPage | Live animation preview (canvas rendering of RGB frames) |
| `/layouts/:deviceUid` | LayoutEditorPage | 2D pixel layout editor for simulator devices |

## Device Management API

| Method | Endpoint | Action |
|--------|----------|--------|
| `POST` | `/api/devices` | Add device |
| `PATCH` | `/api/devices/:deviceUid` | Edit device |
| `DELETE` | `/api/devices/:deviceUid` | Remove device |

These proxy to the controller's `add_device` / `edit_device` / `remove_device` commands.

## Layout API

| Method | Endpoint | Action |
|--------|----------|--------|
| `GET` | `/api/layouts/:deviceUid` | Load the saved layout for a simulator device |
| `POST` | `/api/layouts/:deviceUid` | Save a layout document for a simulator device |

These endpoints back the layout editor and operate on simulator layout documents keyed by `deviceUid`.

## Current Scope

The web app drives playback: load a program and control play/pause/resume/stop from the browser, alongside watching animations and managing devices/layouts. Playback state is reflected from the controller's full-state snapshots. Seek is not yet available (planned future work).

## Key Files

| File | Role |
|------|------|
| `controller/elemctl/web.py` | Python web UI server (observer client + HTTP) |
| `controller/web_ui/src/main.ts` | App entry point |
| `controller/web_ui/src/App.vue` | Root component |
| `controller/web_ui/src/router.ts` | Route definitions |
| `controller/web_ui/src/pages/StatusPage.vue` | Device status + controls |
| `controller/web_ui/src/pages/ViewerPage.vue` | Animation viewer |
| `controller/web_ui/src/pages/LayoutEditorPage.vue` | Layout editor |
| `controller/web_ui/src/lib/deviceApi.ts` | REST API client |
| `controller/web_ui/src/lib/viewerRenderer.ts` | Canvas rendering for animation frames |
| `controller/web_ui/src/composables/useServerState.ts` | Web app connection and controller state management |
| `controller/web_ui/dist/` | Built frontend assets served by `elemctl web` |

## Running

```bash
./elemctl web   # starts the web UI server on http://127.0.0.1:8080/
```

Requires a running controller (`./elemctl server`).
