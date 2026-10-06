import { useMemo, type ReactNode } from 'react';

/**
 * Minimal Markdown renderer for generated documentation.
 *
 * Deliberately dependency-free and rendered as React elements (never
 * `dangerouslySetInnerHTML`), so generated content can never inject markup into
 * the app. Supports exactly what the M3 renderers emit: headings, paragraphs,
 * bullet lists, pipe tables, blockquotes, bold/inline-code, and fenced blocks.
 */
export function MarkdownView({ content }: { content: string }) {
  const blocks = useMemo(() => parseMarkdown(content || ''), [content]);
  return <div className="md">{blocks}</div>;
}

function parseMarkdown(src: string): ReactNode[] {
  const lines = src.replace(/\r\n/g, '\n').split('\n');
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    // fenced code
    if (line.trim().startsWith('```')) {
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push(
        <pre key={key++} className="md-pre">
          <code>{buf.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    // table
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      const header = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|')) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      out.push(
        <div key={key++} className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                {header.map((h, hi) => (
                  <th key={hi}>{inline(h)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci}>{inline(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // heading
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const Tag = (`h${Math.min(level, 6)}`) as keyof JSX.IntrinsicElements;
      out.push(<Tag key={key++} className="md-h">{inline(h[2])}</Tag>);
      i += 1;
      continue;
    }

    // blockquote
    if (line.trim().startsWith('>')) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      out.push(
        <blockquote key={key++} className="md-quote">
          {inline(buf.join(' '))}
        </blockquote>,
      );
      continue;
    }

    // horizontal rule
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      out.push(<hr key={key++} className="md-hr" />);
      i += 1;
      continue;
    }

    // bullet list
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
        i += 1;
      }
      out.push(
        <ul key={key++} className="md-list">
          {items.map((it, ii) => (
            <li key={ii}>{inline(it)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // paragraph (join soft-wrapped lines)
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith('#') &&
      !lines[i].trim().startsWith('>') &&
      !lines[i].trim().startsWith('```') &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !lines[i].includes('|')
    ) {
      buf.push(lines[i].trim());
      i += 1;
    }
    out.push(
      <p key={key++} className="md-p">
        {inline(buf.join(' '))}
      </p>,
    );
  }

  return out;
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
}

/** Renders **bold** and `code` spans as React nodes. Everything else is text. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const token = m[0];
    if (token.startsWith('**')) {
      out.push(<strong key={k++}>{token.slice(2, -2)}</strong>);
    } else {
      out.push(<code key={k++} className="md-code">{token.slice(1, -1)}</code>);
    }
    last = m.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
