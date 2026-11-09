export class ProviderError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly retryable = false,
  ) {
    super(message);
    this.name = "ProviderError";
  }
}

export interface FetchOpts extends Omit<RequestInit, "signal"> {
  timeoutMs?: number;
  retries?: number;
  parse?: "json" | "text";
}

/** fetch with timeout, bounded retries (exponential backoff + jitter) on 429/5xx/network. */
export async function fetchRaw(url: string, opts: FetchOpts = {}): Promise<Response> {
  const { timeoutMs = 8000, retries = 1, ...init } = opts;
  let attempt = 0;
  for (;;) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { ...init, signal: ctrl.signal, cache: "no-store" });
      if (res.ok) return res;
      const retryable = res.status === 429 || res.status >= 500;
      if (!retryable || attempt >= retries) {
        throw new ProviderError(`HTTP ${res.status} from ${new URL(url).host}`, res.status, retryable);
      }
    } catch (e) {
      if (e instanceof ProviderError && (!e.retryable || attempt >= retries)) throw e;
      if (!(e instanceof ProviderError) && attempt >= retries) {
        throw new ProviderError(
          `Network error contacting ${new URL(url).host}: ${(e as Error).name === "AbortError" ? "timeout" : (e as Error).message}`,
          undefined,
          true,
        );
      }
    } finally {
      clearTimeout(timer);
    }
    attempt++;
    await new Promise((r) => setTimeout(r, 250 * 2 ** attempt + Math.random() * 100));
  }
}

export async function fetchJson<T>(url: string, opts: FetchOpts = {}): Promise<T> {
  const res = await fetchRaw(url, opts);
  return (await res.json()) as T;
}
export async function fetchText(url: string, opts: FetchOpts = {}): Promise<string> {
  return (await fetchRaw(url, opts)).text();
}
