export default function MessageBubble({ role, content }) {
  const isUser = role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[70%] whitespace-pre-wrap rounded-lg px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? 'bg-accent text-white'
            : 'border border-line bg-surface text-ink'
        }`}
      >
        {content}
      </div>
    </div>
  )
}
