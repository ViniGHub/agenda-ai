// Renders the model's plain text: paragraphs, plus "- " lines as a list.
// Text is never injected as HTML, so whatever the model writes cannot run in the page.

export function AnswerText({ text }: { text: string }) {
  const blocks: { kind: 'p' | 'ul'; lines: string[] }[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const isItem = /^[-•*]\s+/.test(line)
    const kind = isItem ? 'ul' : 'p'
    const content = isItem ? line.replace(/^[-•*]\s+/, '') : line
    const last = blocks[blocks.length - 1]
    if (kind === 'ul' && last?.kind === 'ul') last.lines.push(content)
    else blocks.push({ kind, lines: [content] })
  }

  return (
    <div className="answer-text">
      {blocks.map((block, index) =>
        block.kind === 'ul' ? (
          <ul key={index}>
            {block.lines.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        ) : (
          <p key={index}>{block.lines[0]}</p>
        ),
      )}
    </div>
  )
}
