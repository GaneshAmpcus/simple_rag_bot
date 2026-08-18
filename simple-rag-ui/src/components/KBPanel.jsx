import { useCallback, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { ingestDocument, ApiError } from '../api/client'

const ACCEPTED_EXT = ['.pdf', '.docx']

function isAccepted(file) {
  const name = file.name.toLowerCase()
  return ACCEPTED_EXT.some((ext) => name.endsWith(ext))
}

export default function KBPanel() {
  const { token } = useAuth()
  const inputRef = useRef(null)
  const [isDragging, setIsDragging] = useState(false)
  // list of { id, name, status: 'uploading' | 'done' | 'error', error? }
  const [uploads, setUploads] = useState([])

  const uploadFiles = useCallback(
    async (fileList) => {
      const files = Array.from(fileList).filter((f) => {
        if (!isAccepted(f)) return false
        return true
      })
      const rejected = Array.from(fileList).filter((f) => !isAccepted(f))

      for (const file of files) {
        const id = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
        setUploads((prev) => [{ id, name: file.name, status: 'uploading' }, ...prev])

        try {
          await ingestDocument(token, file)
          setUploads((prev) =>
            prev.map((u) => (u.id === id ? { ...u, status: 'done' } : u)),
          )
        } catch (err) {
          const message = err instanceof ApiError ? err.message : 'Upload failed.'
          setUploads((prev) =>
            prev.map((u) => (u.id === id ? { ...u, status: 'error', error: message } : u)),
          )
        }
      }

      for (const file of rejected) {
        setUploads((prev) => [
          {
            id: `${file.name}-${Date.now()}-rej`,
            name: file.name,
            status: 'error',
            error: 'Only PDF or DOCX files are supported.',
          },
          ...prev,
        ])
      }
    },
    [token],
  )

  function handleDrop(e) {
    e.preventDefault()
    setIsDragging(false)
    if (e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files)
  }

  function handlePick(e) {
    if (e.target.files?.length) uploadFiles(e.target.files)
    e.target.value = ''
  }

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-line bg-surface">
      <div className="border-b border-line px-4 py-4">
        <h2 className="font-display text-sm font-semibold text-ink">Knowledge base</h2>
        <p className="mt-0.5 text-xs text-muted">
          Documents added here are available to every thread.
        </p>
      </div>

      <div className="px-4 py-4">
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-4 py-8 text-center transition ${
            isDragging ? 'border-accent bg-accent-soft' : 'border-line hover:border-accent'
          }`}
        >
          <div className="mb-2 h-8 w-8 rounded-sm border-2 border-accent" />
          <p className="text-sm font-medium text-ink">Drop files or click to add</p>
          <p className="mt-1 text-xs text-muted font-mono">PDF, DOCX</p>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.docx"
            multiple
            onChange={handlePick}
            className="hidden"
          />
        </div>
      </div>

      {uploads.length > 0 && (
        <div className="flex-1 overflow-y-auto border-t border-line px-4 py-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
            This session's uploads
          </p>
          <ul className="space-y-2">
            {uploads.map((u) => (
              <li
                key={u.id}
                className="flex items-start justify-between gap-2 rounded-sm border border-line bg-paper px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-ink">{u.name}</p>
                  {u.status === 'error' && (
                    <p className="mt-0.5 text-xs text-danger">{u.error}</p>
                  )}
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    u.status === 'done'
                      ? 'bg-accent-soft text-accent-deep'
                      : u.status === 'error'
                        ? 'bg-danger/10 text-danger'
                        : 'bg-line text-muted'
                  }`}
                >
                  {u.status === 'uploading' ? 'Uploading…' : u.status === 'done' ? 'Added' : 'Failed'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-auto border-t border-line px-4 py-4">
        <div className="rounded-md border border-line bg-paper px-3 py-3">
          <p className="text-xs font-medium text-ink">Manage documents</p>
          <p className="mt-1 text-xs text-muted">
            Viewing, replacing, or removing individual documents isn't available yet — the API
            currently only supports adding new files. This section will unlock once listing and
            deletion endpoints exist.
          </p>
        </div>
      </div>
    </aside>
  )
}
