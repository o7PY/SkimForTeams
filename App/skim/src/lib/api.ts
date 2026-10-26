// The /api/v1 client (PHASE4.md section 6.2). Bearer token on every call; one silent refresh-and-retry on 401.
export class ApiError extends Error {
  constructor(
    public status: number,
    public body: { error?: string; details?: string[]; [k: string]: unknown } | null,
    message?: string,
  ) {
    super(message ?? body?.error ?? `HTTP ${status}`);
    this.name = 'ApiError';
  }
  /** status 0 = the request never reached the server (offline, DNS, timeout) */
  get isNetwork() {
    return this.status === 0;
  }
}

export type TokenSource = {
  /** the current access token, or null when signed out */
  getToken(): Promise<string | null>;
  /** try to get a fresh token (refresh the session); null when that is not possible */
  refresh(): Promise<string | null>;
  /** called when the server still says 401 after a refresh: the session is dead */
  onSignedOut?(): void;
};

export type ApiOptions = { baseUrl: string; tokens: TokenSource; fetchImpl?: typeof fetch; timeoutMs?: number };

export function createApi({ baseUrl, tokens, fetchImpl, timeoutMs = 20000 }: ApiOptions) {
  const doFetch = fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));

  async function once(method: string, path: string, token: string | null, body?: unknown): Promise<Response> {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      return await doFetch(`${baseUrl}/api/v1${path}`, {
        method,
        signal: ctl.signal,
        headers: {
          Accept: 'application/json',
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new ApiError(0, null, e instanceof Error && e.name === 'AbortError' ? 'The request timed out' : 'No connection');
    } finally {
      clearTimeout(timer);
    }
  }

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let token = await tokens.getToken();
    let res = await once(method, path, token, body);
    if (res.status === 401) {
      const fresh = await tokens.refresh();
      if (fresh && fresh !== token) {
        token = fresh;
        res = await once(method, path, token, body);
      }
      if (res.status === 401) {
        tokens.onSignedOut?.();
        throw new ApiError(401, await parse(res), 'Your session has ended. Please sign in again.');
      }
    }
    if (res.status === 204) return undefined as T;
    const parsed = await parse(res);
    if (!res.ok) throw new ApiError(res.status, parsed && typeof parsed === 'object' ? parsed : null);
    return parsed as T;
  }

  return {
    get: <T>(path: string) => request<T>('GET', path),
    post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
    put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body ?? {}),
    patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body ?? {}),
    delete: <T = void>(path: string) => request<T>('DELETE', path),
  };
}

async function parse(res: Response): Promise<any> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

export type Api = ReturnType<typeof createApi>;

export type Me = {
  id: string;
  email: string | null;
  name: string | null;
  created_at: string;
  settings: Record<string, unknown>;
};
