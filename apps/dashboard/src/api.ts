const key = "dpg.session";
export function getToken() {
  return sessionStorage.getItem(key) || "";
}
export function setToken(token: string) {
  if (token) sessionStorage.setItem(key, token);
  else sessionStorage.removeItem(key);
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const form = options.body instanceof FormData;
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers: {
      ...(!form && options.body ? { "Content-Type": "application/json" } : {}),
      ...(getToken() ? { Authorization: "Bearer " + getToken() } : {}),
      ...options.headers,
    },
  });
  let data: any;
  try {
    data = await response.json();
  } catch {
    data = { detail: `Companion returned ${response.status}` };
  }
  if (!response.ok) {
    if (response.status === 401) {
      setToken("");
      window.dispatchEvent(new Event("dpg:unpaired"));
    }
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : typeof data.error === "string"
          ? data.error
          : JSON.stringify(data.detail || data),
    );
  }
  return data;
}
export const post = <T = any>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const remove = (path: string) => api(path, { method: "DELETE" });
