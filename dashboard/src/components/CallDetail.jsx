export default function CallDetail({ call }) {
  const when = new Date(call.ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });
  const transcript = call.payload.transcript || call.payload.conversation || call.payload.messages;
  return (
    <article className="detail">
      <header className="detail-head">
        <span className="badge info">Call ended</span>
        <span className="muted mono">
          {when} · #{call.id}
        </span>
      </header>
      {transcript ? (
        <section>
          <h3>Transcript</h3>
          <pre className="transcript">
            {typeof transcript === "string" ? transcript : JSON.stringify(transcript, null, 2)}
          </pre>
        </section>
      ) : null}
      <details open={!transcript}>
        <summary>Everything Gnani sent after the call</summary>
        <pre>{JSON.stringify(call.payload, null, 2)}</pre>
      </details>
    </article>
  );
}
