"use client";

import { useEffect } from "react";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div className="aurora grid min-h-dvh place-items-center p-4">
      <div className="glass max-w-md p-8 text-center">
        <h1 className="text-2xl font-bold">Something went wrong</h1>
        <p className="mt-2 text-muted">Please try again. If this keeps happening, contact support{error.digest ? ` and mention code ${error.digest}` : ""}.</p>
        <button onClick={reset} className="btn btn-primary mt-6">Try again</button>
      </div>
    </div>
  );
}
