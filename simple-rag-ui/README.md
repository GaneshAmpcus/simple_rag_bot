# simple-rag UI

React + Vite + Tailwind frontend for the simple-rag backend.

## Setup

```bash
npm install
cp .env.example .env   # then set VITE_API_URL if backend isn't on localhost:8000
npm run dev
```

## Layout

- `src/context/AuthContext.jsx` — JWT storage, login/register/logout, session validation via `/auth/me`
- `src/api/client.js` — all backend calls (auth, sessions, chat, ingest)
- `src/components/Workspace.jsx` — three-pane layout, owns `activeSessionId`
- `src/components/Sidebar.jsx` — session list (`GET /sessions`), new chat
- `src/components/ChatWindow.jsx` — thread view, sends `POST /chat`, captures `session_id` on first turn
- `src/components/KBPanel.jsx` — always-visible right panel, uploads via `POST /ingest`

## Known gaps (match current backend)

- KB panel only supports **adding** documents. There's no `GET`/`DELETE` for KB docs yet, so
  "Manage documents" is a disabled placeholder — wire it up once those endpoints exist.
- No memory UI — no `GET /memories` endpoint exists yet.
- The one-off `/query` endpoint isn't wired into the UI (chat-only for now, per scope).
